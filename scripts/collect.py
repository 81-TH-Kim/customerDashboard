"""RPA 제휴현황 메일 수집기.

흐름 : 메일 검색(제목에 RPA) → 신규 메일만 → 파싱 → 검증 → inflow.json 적재 → 수집이력 기록

실 운영 메일 : 발신 '61000791', 제목 "[RPA]플랫폼 제휴 현황_<기준일>"
  - 제목에 'RPA' 포함 → SUBJECT RPA 검색으로 매칭
  - 기준일은 parser.date_from_subject 가 제목 끝 날짜에서 추출
    (YYYY-MM-DD / YYYYMMDD / YYYY.MM.DD / YYYY년 M월 D일 / _2026-09-08 등)
  - 제목에 날짜가 없으면 '수신일 −1일' 로 폴백하고 경고를 남김

수집 방식 (택1)
  --imap   : Gmail IMAP + 앱 비밀번호   ← 기본값/권장 (OAuth 등록 불필요, imaplib 표준 라이브러리)
  --api    : Gmail API + OAuth (secrets/credentials.json 필요)
  --eml    : 로컬로 저장한 .eml 파일 1건
  --msg-json : {subject, received_at, html_body, ...} JSON 1건 (Claude 커넥터로 읽은 메일 주입용)

사용법
  python scripts/collect.py                       # IMAP, 최근 7일 신규 메일
  python scripts/collect.py --days 30
  python scripts/collect.py --replace             # 같은 기준일 덮어쓰기
  python scripts/collect.py --dry-run             # 적재 없이 파싱 결과만
  python scripts/collect.py --eml mail.eml
  python scripts/collect.py --msg-json mail.json
  python scripts/collect.py --api --days 5
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from email import message_from_binary_file
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store
from _console import force_utf8
from parser import ParseError, parse_mail

force_utf8()


def _process_one(msg: dict, *, replace: bool, dry_run: bool) -> dict:
    """msg: {message_id, subject, sender, received_at_ms 또는 received_at, html_body, text_body}"""
    mid = msg["message_id"]
    if "received_at" in msg:
        received = msg["received_at"]
    else:
        received = datetime.fromtimestamp(msg["received_at_ms"] / 1000, tz=timezone.utc)

    log_base = {
        "message_id": mid,
        "subject": msg.get("subject", ""),
        "sender": msg.get("sender", ""),
        "received_at": received.isoformat(),
    }

    try:
        parsed = parse_mail(
            html_body=msg.get("html_body"),
            text_body=msg.get("text_body"),
            received_at=received,
            subject=msg.get("subject", ""),
        )
    except ParseError as e:
        entry = {**log_base, "status": "error", "record_date": None,
                 "message": f"파싱 실패: {e}", "warnings": []}
        if not dry_run:
            store.append_log(entry)
        return entry

    record = parsed.to_record(
        source={
            "message_id": mid,
            "subject": msg.get("subject", ""),
            "sender": msg.get("sender", ""),
            "received_at": received.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
    )
    record["warnings"] = list(parsed.warnings)
    log_base["date_source"] = parsed.date_source

    errors = store.validate_record(record, channels_master=store.load_channels())
    if errors:
        entry = {**log_base, "status": "error", "record_date": record["date"],
                 "message": "검증 실패: " + "; ".join(errors), "warnings": record["warnings"]}
        if not dry_run:
            store.append_log(entry)
        return entry

    if dry_run:
        return {**log_base, "status": "dry-run", "record_date": record["date"],
                "message": json.dumps(record["totals"], ensure_ascii=False),
                "warnings": record["warnings"]}

    result = store.upsert_record(record, allow_replace=replace)
    status = {"inserted": "success", "replaced": "success",
              "skipped_duplicate": "skipped"}[result]
    entry = {**log_base, "status": status, "record_date": record["date"],
             "message": {"inserted": "신규 적재", "replaced": "기존 기준일 덮어쓰기",
                         "skipped_duplicate": "중복 — 건너뜀"}[result],
             "warnings": record["warnings"]}
    store.append_log(entry)
    return entry


def _hdr(m, name: str, default: str = "") -> str:
    raw = m.get(name)
    if raw is None:
        return default
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return str(raw)


def _from_eml(path: Path) -> dict:
    with path.open("rb") as f:
        m = message_from_binary_file(f)
    html_body = text_body = None
    for part in m.walk() if m.is_multipart() else [m]:
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
        received = parsedate_to_datetime(m.get("Date"))
    except (TypeError, ValueError):
        received = datetime.now(timezone.utc)
    if received is None:
        received = datetime.now(timezone.utc)
    return {
        "message_id": _hdr(m, "Message-ID", f"eml:{path.name}").strip("<>"),
        "subject": _hdr(m, "Subject"),
        "sender": _hdr(m, "From"),
        "received_at": received,
        "html_body": html_body,
        "text_body": text_body,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="RPA 제휴현황 메일 수집기")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--subject", default=None,
                    help="단순 제목 검색어로 강제 (지정 시 Gmail 검색식 무시)")
    ap.add_argument("--query", default=None,
                    help="Gmail 검색식 직접 지정 (기본: 'subject:RPA subject:플랫폼')")
    ap.add_argument("--replace", action="store_true", help="같은 기준일 데이터 덮어쓰기")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--eml", type=Path, default=None, help="로컬 .eml 1건 적재")
    ap.add_argument("--msg-json", type=Path, default=None,
                    help="{subject,received_at,html_body,text_body,message_id} JSON 1건 적재")
    ap.add_argument("--imap", action="store_true", help="Gmail IMAP(앱 비밀번호)로 수집 [권장]")
    ap.add_argument("--api", action="store_true", help="Gmail API(OAuth)로 수집")
    ap.add_argument("--max", type=int, default=50)
    args = ap.parse_args()

    results: list[dict] = []

    if args.eml:
        results.append(_process_one(_from_eml(args.eml), replace=args.replace, dry_run=args.dry_run))

    elif args.msg_json:
        raw = json.loads(args.msg_json.read_text(encoding="utf-8"))
        if "received_at" in raw and isinstance(raw["received_at"], str):
            raw["received_at"] = datetime.fromisoformat(raw["received_at"].replace("Z", "+00:00"))
        raw.setdefault("message_id", f"msgjson:{args.msg_json.stem}")
        results.append(_process_one(raw, replace=args.replace, dry_run=args.dry_run))

    elif args.api:
        from gmail_client import GmailUnavailable, fetch_message, get_service, search_messages
        try:
            service = get_service()
        except GmailUnavailable as e:
            print(f"[Gmail 연동 불가] {e}", file=sys.stderr)
            return 2
        base_q = args.query or ("subject:" + args.subject if args.subject else "subject:RPA subject:플랫폼")
        query = f"{base_q} newer_than:{args.days}d"
        known = store.known_message_ids()
        ids = search_messages(service, query, max_results=args.max)
        new_ids = [i for i in ids if i not in known]
        print(f"검색 {len(ids)}건 / 신규 {len(new_ids)}건  (query: {query})")
        for mid in new_ids:
            results.append(_process_one(fetch_message(service, mid),
                                        replace=args.replace, dry_run=args.dry_run))

    else:  # 기본 = IMAP
        from imap_client import (SUBJECT_PREFIX, ImapUnavailable, connect,
                                 fetch, search_ids, subject_matches)
        try:
            M = connect()
        except ImapUnavailable as e:
            print(f"[IMAP 연동 불가] {e}", file=sys.stderr)
            return 2
        try:
            known = store.known_message_ids()
            uids = search_ids(M, subject=args.subject or "RPA", days=args.days)
            fetched = [fetch(M, u) for u in uids]
            # 제목이 '[RPA]플랫폼 제휴 현황' 로 시작하는 실 운영 메일만 (--subject 강제 시 건너뜀)
            if args.subject:
                matched = fetched
            else:
                matched = [m for m in fetched if subject_matches(m["subject"])]
                skipped_subj = len(fetched) - len(matched)
                if skipped_subj:
                    print(f"  (제목 불일치로 {skipped_subj}건 제외 — '{SUBJECT_PREFIX}' 로 시작 안 함)")
            new = [m for m in matched if m["message_id"] not in known]
            print(f"검색 {len(fetched)}건 / 대상 {len(matched)}건 / 신규 {len(new)}건  ({args.days}일 이내)")
            for m in new:
                results.append(_process_one(m, replace=args.replace, dry_run=args.dry_run))
        finally:
            M.logout()

    for r in results:
        line = f"  [{r['status']}] {r.get('record_date') or '-'}  {r['message']}"
        print(line)
        for w in r.get("warnings", []):
            print(f"      ⚠ {w}")
    ok = all(r["status"] not in ("error",) for r in results)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
