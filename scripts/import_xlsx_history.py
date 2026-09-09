"""'채널별 제휴현황_월 기준.xlsx' (월별 누적) → inflow.json 적재.

엑셀 구조
  행1: 월 헤더(1월..)   / 각 월 3열 병합
  행2: 보장분석 이용건 · 마케팅 동의건 · 마케팅 동의율
  행3~: 채널 (A열=구분 대내/대외, B열=채널명)
  행 '합계'

지표 매핑
  보장분석 이용건 → total
  마케팅 동의건   → y  (마케팅 동의)
  total - y       → n  (미동의)

월별 레코드는 해당 월 '말일' 날짜, period="monthly", date_source="xlsx-monthly".

사용법
  python scripts/import_xlsx_history.py "[암호화해제]채널별 제휴현황_9월 기준.xlsx" --year 2026
  python scripts/import_xlsx_history.py 파일.xlsx --year 2026 --months 1-8 --dry-run
  python scripts/import_xlsx_history.py 파일.xlsx --year 2026 --replace
"""
from __future__ import annotations

import argparse
import calendar
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store
from _console import force_utf8

force_utf8()

# 엑셀 채널명(B열) → RPA 메일 채널 코드(channels.json code)
CHANNEL_MAP = {
    "홈페이지": "WEB",
    "인터넷보험": "CM",
    "모바일센터": "MobileCenter",
    "PAYCO": "PAYCO",
    "NAVER": "NAVER",
    "우리원뱅킹": "wonMobile",
}
MONTH_HEADERS = {f"{m}월": m for m in range(1, 13)}


def _num(v):
    if v in (None, "", "-"):
        return None
    if isinstance(v, (int, float)):
        return v
    s = str(v).replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s) if "." in s else int(s)
    except ValueError:
        return None


def parse_months(spec: str) -> set[int]:
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out


def load_xlsx(path: Path, year: int, months: set[int]) -> list[dict]:
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))

    # 행1에서 월 → 시작열 인덱스
    month_col: dict[int, int] = {}
    for ci, val in enumerate(rows[0]):
        if val and str(val).strip() in MONTH_HEADERS:
            month_col[MONTH_HEADERS[str(val).strip()]] = ci  # 보장분석이용건 열

    # 채널 행 수집 (행3부터 '합계' 전까지, B열 채널명 있는 행)
    by_month: dict[int, list[dict]] = {m: [] for m in months if m in month_col}
    unknown: set[str] = set()
    for r in rows[2:]:
        gubun = (str(r[0]).strip() if r[0] is not None else "")
        name = (str(r[1]).strip() if r[1] is not None else "")
        if gubun in ("합계", "총계") or name in ("합계", "총계", "소계"):
            break  # '합계' 행 이후는 요약부 — 중단
        if not name:
            continue
        code = CHANNEL_MAP.get(name)
        if not code:
            unknown.add(name)
            continue
        for m in list(by_month):
            base = month_col[m]
            total = _num(r[base])
            agree = _num(r[base + 1])
            if total is None:
                continue
            total = int(round(total))
            agree = int(round(agree)) if agree is not None else 0
            by_month[m].append({
                "channel": code,
                "y": agree,
                "n": max(total - agree, 0),
                "total": total,
            })

    if unknown:
        print(f"  ⚠ 매핑에 없는 채널(건너뜀): {', '.join(sorted(unknown))}")

    records = []
    for m in sorted(by_month):
        chans = by_month[m]
        if not chans:
            continue
        last_day = calendar.monthrange(year, m)[1]
        d = f"{year:04d}-{m:02d}-{last_day:02d}"
        totals = {k: sum(c[k] for c in chans) for k in ("y", "n", "total")}
        records.append({
            "date": d,
            "date_source": "xlsx-monthly",
            "period": "monthly",
            "channels": chans,
            "totals": totals,
            "origin": "history_import",
            "source": {"message_id": f"xlsx-monthly:{year}-{m:02d}",
                       "subject": f"{path.name} / {m}월"},
            "collected_at": datetime.now(store.KST).isoformat(timespec="seconds"),
            "warnings": [],
        })
    return records


def main() -> int:
    ap = argparse.ArgumentParser(description="월별 제휴현황 엑셀 → inflow.json")
    ap.add_argument("file", type=Path)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--months", default="1-8", help="적재할 월 (기본 1-8, 9월은 RPA 일별 사용)")
    ap.add_argument("--replace", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.file.exists():
        print(f"파일 없음: {args.file}", file=sys.stderr)
        return 2

    months = parse_months(args.months)
    records = load_xlsx(args.file, args.year, months)
    master = store.load_channels()
    print(f"{len(records)}개 월 레코드 생성 (월: {sorted(months)})")

    ins = rep = skip = err = 0
    for rec in records:
        errs = store.validate_record(rec, channels_master=master)
        if errs:
            err += 1
            print(f"  [error] {rec['date']}  {'; '.join(errs)}")
            continue
        if args.dry_run:
            chans = ", ".join(f"{c['channel']}={c['total']}" for c in rec["channels"])
            print(f"  [dry-run] {rec['date']}  총합 {rec['totals']['total']}  ({chans})")
            for w in rec.get("warnings", []):
                print(f"      ⚠ {w}")
            continue
        res = store.upsert_record(rec, allow_replace=args.replace)
        ins += res == "inserted"; rep += res == "replaced"; skip += res == "skipped_duplicate"
        print(f"  [{res}] {rec['date']}  총합 {rec['totals']['total']}")

    if not args.dry_run:
        store.append_log({
            "message_id": f"xlsx_import:{datetime.now(store.KST):%Y%m%d_%H%M%S}",
            "subject": f"월별 엑셀 적재: {args.file.name}",
            "sender": "import_xlsx_history.py",
            "received_at": datetime.now(store.KST).isoformat(timespec="seconds"),
            "status": "success" if err == 0 else "error",
            "record_date": None,
            "date_source": "xlsx-monthly",
            "message": f"신규 {ins} / 교체 {rep} / 중복 {skip} / 오류 {err}",
            "warnings": [],
        })
    print(f"\n완료 — 신규 {ins} / 교체 {rep} / 중복 {skip} / 오류 {err}")
    return 0 if err == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
