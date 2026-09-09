"""파일 기반 데이터 저장소 (물리 DB 미사용).

data/inflow.json          : 일자별 × 채널별 누적 실적
data/channels.json        : 제휴채널 마스터
data/collection_log.json  : 메일 수집 이력 / 오류현황

동시 쓰기 충돌을 줄이기 위해 임시파일 write 후 os.replace 로 원자적 교체한다.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
ROOT = Path(__file__).resolve().parent.parent
# 로컬 실 데이터 (git 제외). collect.py / import_*.py 가 여기에 기록한다.
# 공개(GitHub Pages)용 샘플은 docs/data/*.json 에 별도 커밋되어 있고,
# 실 데이터를 공개하려면 publish.bat 로 명시적으로 복사·푸시한다.
DATA_DIR = ROOT / "data" / "live"
SAMPLE_DIR = ROOT / "data"        # <name>.sample.json 보관
INFLOW = DATA_DIR / "inflow.json"
CHANNELS = DATA_DIR / "channels.json"
LOG = DATA_DIR / "collection_log.json"

# 비정상적으로 큰 값 판단 임계치 (일 채널 유입건수)
ABNORMAL_TOTAL = 100_000


def _now() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _ensure(path: Path) -> None:
    """데이터 파일이 없으면 <name>.sample.json 을 복사, 그것도 없으면 빈 구조를 만든다."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    for sample in (path.parent / f"{path.stem}.sample{path.suffix}",
                   SAMPLE_DIR / f"{path.stem}.sample{path.suffix}"):
        if sample.exists():
            shutil.copy2(sample, path)
            return
    empty = {"channels": []} if path.name == "channels.json" else \
            {"logs": []} if path.name == "collection_log.json" else \
            {"schema_version": 1, "records": []}
    with path.open("w", encoding="utf-8") as f:
        json.dump(empty, f, ensure_ascii=False, indent=2)


def _read(path: Path) -> dict:
    _ensure(path)
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _write(path: Path, obj: dict) -> None:
    obj["updated_at"] = _now()
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _backup(path: Path) -> None:
    bdir = DATA_DIR / "_backup"
    bdir.mkdir(exist_ok=True)
    stamp = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
    shutil.copy2(path, bdir / f"{path.stem}_{stamp}{path.suffix}")


# --------------------------------------------------------------------------- #
def load_inflow() -> dict:
    return _read(INFLOW)


def load_channels() -> dict:
    return _read(CHANNELS)


def load_log() -> dict:
    return _read(LOG)


def save_channels(obj: dict) -> None:
    _backup(CHANNELS)
    _write(CHANNELS, obj)


def known_message_ids() -> set[str]:
    ids = set()
    for r in load_inflow().get("records", []):
        mid = (r.get("source") or {}).get("message_id")
        if mid:
            ids.add(mid)
    for l in load_log().get("logs", []):
        if l.get("message_id"):
            ids.add(l["message_id"])
    return ids


def known_dates() -> set[str]:
    return {r["date"] for r in load_inflow().get("records", [])}


def append_log(entry: dict) -> None:
    log = load_log()
    entry.setdefault("collected_at", _now())
    log.setdefault("logs", []).append(entry)
    _write(LOG, log)


def validate_record(record: dict, *, channels_master: dict | None = None) -> list[str]:
    """반환: 오류 메시지 리스트 (비어 있으면 통과). 경고는 record['warnings']에 별도 누적."""
    errors: list[str] = []
    warnings: list[str] = record.setdefault("warnings", [])

    if not record.get("date"):
        errors.append("기준일 없음")
    else:
        try:
            datetime.strptime(record["date"], "%Y-%m-%d")
        except ValueError:
            errors.append(f"기준일 형식 오류: {record['date']}")

    rows = record.get("channels") or []
    if not rows:
        errors.append("채널 데이터 없음")

    master_codes = None
    if channels_master:
        master_codes = {c["code"] for c in channels_master.get("channels", [])}

    seen = set()
    for row in rows:
        ch = row.get("channel")
        if not ch:
            errors.append("채널명 없음인 행 존재")
            continue
        if ch in seen:
            errors.append(f"동일 기준일 내 채널 중복: {ch}")
        seen.add(ch)
        for k in ("y", "n", "total"):
            v = row.get(k)
            if not isinstance(v, int):
                errors.append(f"{ch}.{k} 값이 숫자가 아님: {v!r}")
            elif v < 0:
                errors.append(f"{ch}.{k} 음수: {v}")
            elif v > ABNORMAL_TOTAL:
                warnings.append(f"{ch}.{k} 비정상적으로 큰 값: {v}")
        if all(isinstance(row.get(k), int) for k in ("y", "n", "total")):
            if row["y"] + row["n"] != row["total"]:
                warnings.append(f"{ch}: Y+N != 총합")
        if master_codes is not None and ch not in master_codes:
            warnings.append(f"채널 마스터에 없는 채널: {ch} (관리자 등록 필요)")

    return errors


def upsert_record(record: dict, *, allow_replace: bool = False) -> str:
    """inflow.json 에 record 를 추가/갱신.

    반환: 'inserted' | 'replaced' | 'skipped_duplicate'
    """
    data = load_inflow()
    records = data.setdefault("records", [])
    mid = (record.get("source") or {}).get("message_id")
    date = record.get("date")

    for i, existing in enumerate(records):
        emid = (existing.get("source") or {}).get("message_id")
        if mid and emid and mid == emid:
            return "skipped_duplicate"
        if existing.get("date") == date:
            if not allow_replace:
                return "skipped_duplicate"
            _backup(INFLOW)
            records[i] = record
            records.sort(key=lambda r: r["date"])
            _write(INFLOW, data)
            return "replaced"

    _backup(INFLOW)
    records.append(record)
    records.sort(key=lambda r: r["date"])
    _write(INFLOW, data)
    return "inserted"
