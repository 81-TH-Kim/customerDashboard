"""Gmail API 연동 (OAuth). 인증정보는 소스에 저장하지 않고 환경변수/파일로 관리한다.

필요 환경변수 (.env 참고)
  GMAIL_CREDENTIALS_FILE : Google Cloud OAuth 클라이언트 JSON 경로 (기본 secrets/credentials.json)
  GMAIL_TOKEN_FILE       : 발급된 사용자 토큰 저장 경로 (기본 secrets/token.json)
  GMAIL_USER             : 대상 메일주소 (기본 'me')

최초 1회 `python scripts/gmail_client.py --auth` 실행 → 브라우저 동의 → token.json 생성.
이후 collect.py 가 token.json 으로 무인 동작한다.
"""
from __future__ import annotations

import base64
import os
from email.header import decode_header, make_header
from pathlib import Path


def _decode_hdr(value: str) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
ROOT = Path(__file__).resolve().parent.parent
SECRETS = ROOT / "secrets"


def _paths() -> tuple[Path, Path]:
    cred = Path(os.environ.get("GMAIL_CREDENTIALS_FILE", SECRETS / "credentials.json"))
    token = Path(os.environ.get("GMAIL_TOKEN_FILE", SECRETS / "token.json"))
    return cred, token


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


class GmailUnavailable(RuntimeError):
    pass


def get_service(*, interactive: bool = False):
    _load_dotenv()
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as e:  # pragma: no cover
        raise GmailUnavailable(
            "Gmail 연동 패키지가 없습니다. `pip install -r requirements.txt` 후 다시 시도하세요.\n"
            f"  ({e})"
        )

    cred_file, token_file = _paths()
    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        elif interactive:
            if not cred_file.exists():
                raise GmailUnavailable(f"OAuth 클라이언트 파일이 없습니다: {cred_file}")
            flow = InstalledAppFlow.from_client_secrets_file(str(cred_file), SCOPES)
            creds = flow.run_local_server(port=0)
        else:
            raise GmailUnavailable(
                "Gmail 토큰이 없거나 만료되었습니다. "
                "`python scripts/gmail_client.py --auth` 를 먼저 실행하세요."
            )
        token_file.parent.mkdir(exist_ok=True)
        token_file.write_text(creds.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def search_messages(service, query: str, max_results: int = 50) -> list[str]:
    user = os.environ.get("GMAIL_USER", "me")
    res = service.users().messages().list(userId=user, q=query, maxResults=max_results).execute()
    return [m["id"] for m in res.get("messages", [])]


def _walk_parts(payload) -> dict:
    out = {"text/plain": None, "text/html": None}

    def rec(part):
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        data = body.get("data")
        if data and mime in out and out[mime] is None:
            out[mime] = base64.urlsafe_b64decode(data.encode()).decode("utf-8", "replace")
        for sub in part.get("parts", []) or []:
            rec(sub)

    rec(payload)
    return out


def fetch_message(service, message_id: str) -> dict:
    user = os.environ.get("GMAIL_USER", "me")
    msg = service.users().messages().get(userId=user, id=message_id, format="full").execute()
    headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
    bodies = _walk_parts(msg["payload"])
    return {
        "message_id": message_id,
        "thread_id": msg.get("threadId"),
        "subject": _decode_hdr(headers.get("subject", "")),
        "sender": _decode_hdr(headers.get("from", "")),
        "received_at_ms": int(msg.get("internalDate", "0")),
        "html_body": bodies["text/html"],
        "text_body": bodies["text/plain"] or msg.get("snippet"),
    }


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _console import force_utf8
    force_utf8()

    _load_dotenv()
    cred_file, token_file = _paths()
    try:
        if "--auth" in sys.argv:
            if not cred_file.exists():
                print(f"[설정 필요] OAuth 클라이언트 파일이 없습니다:\n  {cred_file}\n"
                      "  Google Cloud Console에서 '데스크톱 앱' OAuth 클라이언트 JSON을 받아 위 경로에 저장하세요.\n"
                      "  자세한 절차는 README '3. Gmail 자동 수집' 참고.")
                raise SystemExit(2)
            get_service(interactive=True)
            print("토큰 발급 완료:", token_file)
        else:
            svc = get_service()
            ids = search_messages(svc, "subject:RPA newer_than:30d")
            print(f"연동 정상. 최근 30일 'subject:RPA' 메일 {len(ids)}건:", ids)
    except GmailUnavailable as e:
        print(f"[Gmail 연동 불가] {e}")
        raise SystemExit(2)
