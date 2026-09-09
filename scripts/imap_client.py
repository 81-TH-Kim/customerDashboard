"""Gmail IMAP 수집 (예전 방식 · 표준 라이브러리만 사용).

Google Cloud / OAuth 등록 없이 **앱 비밀번호** 하나로 동작한다.

준비 (최초 1회, 약 3분)
  1) Google 계정 → 보안 → '2단계 인증' 사용 설정
  2) Google 계정 → 보안 → '앱 비밀번호' → 앱 이름 아무거나 → 16자리 비밀번호 생성
  3) .env 에 아래 두 줄 추가
       GMAIL_ADDRESS=doyourself81@gmail.com
       GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx      # 공백 없이 16자
  (IMAP 사용 설정은 최신 Gmail에서 기본 ON. 아니면 설정 → 전달/POP·IMAP 에서 IMAP 사용)

collect.py 가 이 모듈을 통해 메일을 읽는다:  python scripts/collect.py --imap
"""
from __future__ import annotations

import email
import imaplib
import os
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAP_HOST = "imap.gmail.com"


class ImapUnavailable(RuntimeError):
    pass


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def connect() -> imaplib.IMAP4_SSL:
    _load_dotenv()
    addr = os.environ.get("GMAIL_ADDRESS")
    pw = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    if not addr or not pw:
        raise ImapUnavailable(
            ".env 에 GMAIL_ADDRESS / GMAIL_APP_PASSWORD 를 설정하세요. "
            "(scripts/imap_client.py 상단 주석 참고)"
        )
    try:
        M = imaplib.IMAP4_SSL(IMAP_HOST)
        M.login(addr, pw)
    except imaplib.IMAP4.error as e:
        raise ImapUnavailable(
            f"Gmail IMAP 로그인 실패: {e}. 앱 비밀번호가 정확한지, 2단계 인증이 켜져 있는지 확인하세요."
        )
    return M


# IMAP SUBJECT 검색은 ASCII 만 안전하므로 'RPA' 로 넓게 잡고,
# 한글 제목 필터(아래 SUBJECT_MUST_CONTAIN)는 Python 쪽에서 처리한다(collect.py).
IMAP_SUBJECT = "RPA"
# 실 운영 메일 제목 '[RPA]플랫폼 제휴 현황_...' 에 반드시 들어가는 토큰.
# 이 중 하나라도 제목에 있어야 수집 대상 (테스트/무관 메일 제외).
SUBJECT_MUST_CONTAIN = ("플랫폼", "제휴")


def search_ids(M: imaplib.IMAP4_SSL, *, subject: str = IMAP_SUBJECT, days: int = 7) -> list[bytes]:
    M.select("INBOX", readonly=True)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")
    typ, data = M.search(None, "SINCE", since, "SUBJECT", subject)
    if typ != "OK" or not data or not data[0]:
        return []
    return data[0].split()


def fetch(M: imaplib.IMAP4_SSL, uid: bytes) -> dict:
    typ, data = M.fetch(uid, "(RFC822)")
    if typ != "OK" or not data or not data[0]:
        raise ImapUnavailable(f"메일 {uid!r} 을 가져오지 못했습니다.")
    msg = email.message_from_bytes(data[0][1])

    html_body = text_body = None
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ct = part.get_content_type()
        if ct not in ("text/html", "text/plain"):
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        decoded = payload.decode(part.get_content_charset() or "utf-8", "replace")
        if ct == "text/html" and html_body is None:
            html_body = decoded
        elif ct == "text/plain" and text_body is None:
            text_body = decoded

    try:
        received = parsedate_to_datetime(msg.get("Date"))
    except (TypeError, ValueError):
        received = None
    if received is None:
        received = datetime.now(timezone.utc)

    mid = _decode(msg.get("Message-ID")) or f"imap-uid:{uid.decode()}"
    return {
        "message_id": mid.strip("<>"),
        "subject": _decode(msg.get("Subject")),
        "sender": _decode(msg.get("From")),
        "received_at": received,
        "html_body": html_body,
        "text_body": text_body,
    }


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from _console import force_utf8
        force_utf8()
    except Exception:
        pass
    try:
        M = connect()
    except ImapUnavailable as e:
        print(f"[IMAP 연동 불가] {e}")
        raise SystemExit(2)
    ids = search_ids(M, days=30)
    kept = 0
    print(f"연동 정상. 최근 30일 제목에 'RPA' 포함 메일 {len(ids)}건")
    for i in ids[-8:]:
        m = fetch(M, i)
        ok = any(t in m["subject"] for t in SUBJECT_MUST_CONTAIN)
        kept += ok
        print(f"  [{'수집' if ok else '제외'}] {m['received_at']:%Y-%m-%d %H:%M}  {m['subject']!r}")
    print(f"→ 수집 대상 {kept}건 (제목에 {'/'.join(SUBJECT_MUST_CONTAIN)} 포함)")
    M.logout()
