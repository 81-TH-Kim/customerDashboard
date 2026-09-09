"""과거 실적 데이터 일괄 적재기.

RPA 메일이 쌓이기 전의 과거 데이터를 CSV/JSON 으로 한 번에 넣는다.
적재된 데이터는 origin='history_import' 로 표시되며, 이후 같은 기준일의 실제 메일이
수집되면 `--replace` 옵션으로 교체할 수 있다.

지원 형식
---------
1) Long CSV  (권장) : 한 행 = 한 채널
     date,channel,y,n,total
     2026-09-01,NAVER,300,10,310
     2026-09-01,WEB,28,5,33
   - y,n 컬럼이 없으면 0 으로 채우고 total 만 사용
   - total 이 없으면 y+n 으로 계산

2) Wide CSV : 한 행 = 하루, 채널별 총합만
     date,NAVER,WEB,wonMobile,MobileCenter,ablCenter,wonYear,jong
     2026-09-01,310,33,9,3,1,0,1

3) JSON : inflow.json 의 records 배열과 동일한 구조 (또는 그 배열 자체)

사용법
  python scripts/import_history.py data/history.csv
  python scripts/import_history.py data/history.csv --replace   # 같은 기준일 덮어쓰기
  python scripts/import_history.py data/history.csv --dry-run
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store
from _console import force_utf8

force_utf8()

RESERVED = {"date", "channel", "y", "n", "total", "기준일", "채널", "일자"}


def _norm_date(s: str) -> str:
    s = s.strip().replace(".", "-").replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y-%m-%d", "%y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"날짜 형식을 알 수 없음: {s!r}")


def _int(v) -> int:
    v = str(v).replace(",", "").strip()
    return int(v) if v not in ("", "-", "None") else 0


def _records_from_long(rows: list[dict]) -> list[dict]:
    by_date: dict[str, list[dict]] = {}
    for r in rows:
        d = _norm_date(r.get("date") or r.get("기준일") or r.get("일자"))
        ch = (r.get("channel") or r.get("채널") or "").strip()
        if not ch:
            continue
        y = _int(r.get("y", 0))
        n = _int(r.get("n", 0))
        total = _int(r["total"]) if r.get("total") not in (None, "") else y + n
        if not r.get("y") and not r.get("n") and r.get("total"):
            y, n = total, 0  # 총합만 제공된 경우
        by_date.setdefault(d, []).append({"channel": ch, "y": y, "n": n, "total": total})
    return _assemble(by_date)


def _records_from_wide(header: list[str], rows: list[list[str]]) -> list[dict]:
    date_idx = 0
    channels = [(i, h.strip()) for i, h in enumerate(header) if h.strip().lower() not in RESERVED and i != date_idx]
    by_date: dict[str, list[dict]] = {}
    for row in rows:
        if not row or not row[date_idx].strip():
            continue
        d = _norm_date(row[date_idx])
        entries = []
        for i, name in channels:
            total = _int(row[i]) if i < len(row) else 0
            entries.append({"channel": name, "y": total, "n": 0, "total": total})
        by_date[d] = entries
    return _assemble(by_date)


def _assemble(by_date: dict[str, list[dict]]) -> list[dict]:
    out = []
    for d, chans in sorted(by_date.items()):
        totals = {k: sum(c[k] for c in chans) for k in ("y", "n", "total")}
        out.append({
            "date": d,
            "channels": chans,
            "totals": totals,
            "origin": "history_import",
            "source": {"message_id": f"history:{d}"},
            "collected_at": datetime.now(store.KST).isoformat(timespec="seconds"),
            "warnings": [],
        })
    return out


def load_file(path: Path) -> list[dict]:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        recs = data.get("records", data) if isinstance(data, dict) else data
        for r in recs:
            r.setdefault("origin", "history_import")
            r.setdefault("source", {"message_id": f"history:{r['date']}"})
            r.setdefault("warnings", [])
        return recs

    text = path.read_text(encoding="utf-8-sig")
    reader = list(csv.reader(text.splitlines()))
    if not reader:
        return []
    header = [h.strip() for h in reader[0]]
    low = [h.lower() for h in header]
    if "channel" in low or "채널" in low:
        dict_rows = [dict(zip(header, r)) for r in reader[1:] if r]
        return _records_from_long(dict_rows)
    return _records_from_wide(header, reader[1:])


def main() -> int:
    ap = argparse.ArgumentParser(description="과거 실적 일괄 적재")
    ap.add_argument("file", type=Path)
    ap.add_argument("--replace", action="store_true", help="같은 기준일 데이터 덮어쓰기")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.file.exists():
        print(f"파일 없음: {args.file}", file=sys.stderr)
        return 2

    records = load_file(args.file)
    master = store.load_channels()
    print(f"{len(records)}개 기준일 데이터 발견")

    inserted = replaced = skipped = errored = 0
    for rec in records:
        errs = store.validate_record(rec, channels_master=master)
        if errs:
            errored += 1
            print(f"  [error] {rec.get('date')}  {'; '.join(errs)}")
            continue
        if args.dry_run:
            print(f"  [dry-run] {rec['date']}  총합={rec['totals']}")
            continue
        res = store.upsert_record(rec, allow_replace=args.replace)
        if res == "inserted":
            inserted += 1
        elif res == "replaced":
            replaced += 1
        else:
            skipped += 1
        print(f"  [{res}] {rec['date']}  총합={rec['totals']['total']}")
        for w in rec.get("warnings", []):
            print(f"      ⚠ {w}")

    if not args.dry_run:
        store.append_log({
            "message_id": f"history_import:{datetime.now(store.KST).strftime('%Y%m%d_%H%M%S')}",
            "subject": f"과거 데이터 적재: {args.file.name}",
            "sender": "import_history.py",
            "received_at": datetime.now(store.KST).isoformat(timespec="seconds"),
            "status": "success" if errored == 0 else "error",
            "record_date": None,
            "message": f"신규 {inserted} / 교체 {replaced} / 중복 {skipped} / 오류 {errored}",
            "warnings": [],
        })
    print(f"\n완료 — 신규 {inserted} / 교체 {replaced} / 중복 {skipped} / 오류 {errored}")
    return 0 if errored == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
