"""조회조건 → 대시보드 뷰모델 계산. 대시보드와 PDF 리포트가 공용으로 사용한다.

granularity: 'day' | 'month' | 'year'
  - day   : period=='daily' 레코드만, 버킷=날짜(YYYY-MM-DD)
  - month : 모든 레코드, 버킷=YYYY-MM (일별은 그 달로 합산)
  - year  : 모든 레코드, 버킷=YYYY
start/end 는 granularity 와 같은 포맷의 문자열.
"""
from __future__ import annotations

import calendar
import sys
from collections import OrderedDict
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store

# 채널 구분용 팔레트 (색상 대비 큰 6~13색)
PALETTE = ["#0067ac", "#ef8a2b", "#3aa757", "#d64550", "#8a63c9", "#00a1b5",
           "#c9a227", "#5b7089", "#e0748a", "#2f8f8f", "#7a6fbe", "#b0611f", "#4c78a8"]

GRAN_LABEL = {
    "day":   {"unit": "일", "latest": "금일 유입",     "delta": "전일 대비", "col": "최근 이용건"},
    "month": {"unit": "월", "latest": "해당 월 이용건", "delta": "전월 대비", "col": "월 이용건"},
    "year":  {"unit": "년", "latest": "해당 년 이용건", "delta": "전년 대비", "col": "연 이용건"},
}


def _pct(cur: float, prev: float) -> float | None:
    if prev == 0:
        return None
    return round((cur - prev) / prev * 100, 1)


def visible_channels(master: dict, *, for_display: bool = True) -> list[dict]:
    chans = master.get("channels", [])
    if for_display:
        chans = [c for c in chans if c.get("display_yn", "Y") == "Y"]
    return sorted(chans, key=lambda c: c.get("sort", 999))


def _bucket_of(date_str: str, gran: str) -> str:
    if gran == "year":
        return date_str[:4]
    if gran == "month":
        return date_str[:7]
    return date_str


def build_view(*, granularity: str = "day", start: str | None = None,
               end: str | None = None, channels: list[str] | None = None,
               **_ignore) -> dict:
    gran = granularity if granularity in GRAN_LABEL else "day"
    inflow = store.load_inflow()
    master = store.load_channels()
    log = store.load_log()

    records = sorted(inflow.get("records", []), key=lambda r: r["date"])
    all_dates = [r["date"] for r in records]

    disp = visible_channels(master, for_display=True)
    order = {c["code"]: i for i, c in enumerate(disp)}
    name_by = {c["code"]: c.get("name", c["code"]) for c in disp}
    type_by = {c["code"]: c.get("type", "") for c in disp}
    color_by = {c["code"]: PALETTE[i % len(PALETTE)] for i, c in enumerate(disp)}
    allowed = set(name_by)
    if channels:
        allowed &= set(channels)

    # day 모드는 일별 레코드만, 나머지는 전체
    pool = [r for r in records if r.get("period", "daily") == "daily"] if gran == "day" else records
    pool_buckets = sorted({_bucket_of(r["date"], gran) for r in pool})

    if not start:
        start = pool_buckets[0] if pool_buckets else _bucket_of(date.today().isoformat(), gran)
    if not end:
        end = pool_buckets[-1] if pool_buckets else _bucket_of(date.today().isoformat(), gran)
    if start > end:
        start, end = end, start

    in_range = [r for r in pool if start <= _bucket_of(r["date"], gran) <= end]

    # 버킷 → 채널 → {y,n,total}
    agg: "OrderedDict[str, dict]" = OrderedDict()
    for r in in_range:
        b = _bucket_of(r["date"], gran)
        slot = agg.setdefault(b, {})
        for c in r.get("channels", []):
            if c["channel"] not in allowed:
                continue
            cs = slot.setdefault(c["channel"], {"y": 0, "n": 0, "total": 0})
            cs["y"] += c["y"]; cs["n"] += c["n"]; cs["total"] += c["total"]
    buckets = sorted(agg)

    # 추이 / 누적
    trend, cumulative = [], []
    run = 0
    for b in buckets:
        rows = agg[b].values()
        t = sum(x["total"] for x in rows)
        y = sum(x["y"] for x in rows)
        n = sum(x["n"] for x in rows)
        run += t
        trend.append({"date": b, "total": t, "y": y, "n": n})
        cumulative.append({"date": b, "total": run})

    period_total = {k: sum(d[k] for d in trend) for k in ("total", "y", "n")}

    # 최신 버킷 / 직전 버킷
    latest = trend[-1] if trend else {"date": None, "total": 0, "y": 0, "n": 0}

    # 최신 버킷이 '진행 중(부분 집계)' 인지 — 월/년의 마지막 데이터가 그 구간 말일이 아닐 때
    latest_partial = False
    if trend and all_dates and gran != "day":
        dmax = all_dates[-1]
        yy, mm, dd = (int(x) for x in dmax.split("-"))
        if gran == "month":
            latest_partial = _bucket_of(dmax, "month") == latest["date"] and dd < calendar.monthrange(yy, mm)[1]
        else:  # year
            latest_partial = dmax[:4] == latest["date"] and (mm, dd) < (12, 31)

    delta = {"prev_date": None, "total_pct": None, "total_diff": 0, "note": None}
    if len(trend) >= 2:
        prev = trend[-2]
        if latest_partial:
            delta["note"] = f"최신 {GRAN_LABEL[gran]['unit']}({latest['date']})은 아직 진행 중이라 비교 생략"
        else:
            delta = {
                "prev_date": prev["date"],
                "total_pct": _pct(latest["total"], prev["total"]),
                "total_diff": latest["total"] - prev["total"],
                "note": None,
            }
    elif trend:
        delta["note"] = "비교할 이전 구간 없음"

    # 채널별
    last_b = buckets[-1] if buckets else None
    prev_b = None if latest_partial else (buckets[-2] if len(buckets) >= 2 else None)
    by_channel = []
    for code in sorted(allowed, key=lambda c: order.get(c, 999)):
        series = [{"date": b, "total": agg[b].get(code, {}).get("total", 0)} for b in buckets]
        p_total = sum(agg[b].get(code, {}).get("total", 0) for b in buckets)
        p_y = sum(agg[b].get(code, {}).get("y", 0) for b in buckets)
        p_n = sum(agg[b].get(code, {}).get("n", 0) for b in buckets)
        cur = agg.get(last_b, {}).get(code) if last_b else None
        prv = agg.get(prev_b, {}).get(code) if prev_b else None
        by_channel.append({
            "channel": code,
            "name": name_by.get(code, code),
            "type": type_by.get(code, ""),
            "color": color_by.get(code, "#888"),
            "today_total": (cur or {}).get("total", 0),
            "today_y": (cur or {}).get("y", 0),
            "today_n": (cur or {}).get("n", 0),
            "period_total": p_total,
            "period_y": p_y,
            "period_n": p_n,
            "share_pct": round(p_total / period_total["total"] * 100, 1) if period_total["total"] else 0.0,
            "dod_pct": _pct(cur["total"], prv["total"]) if (cur and prv) else None,
            "series": series,
        })
    by_channel.sort(key=lambda c: c["period_total"], reverse=True)

    return {
        "query": {
            "granularity": gran, "start": start, "end": end,
            "channels": sorted(channels) if channels else [],
        },
        "labels": GRAN_LABEL[gran],
        "buckets": buckets,
        "as_of": {
            "latest_date": all_dates[-1] if all_dates else None,
            "record_count": len(records),
            "last_collected_at": (log.get("logs") or [{}])[-1].get("collected_at"),
            "date_min": all_dates[0] if all_dates else None,
            "date_max": all_dates[-1] if all_dates else None,
        },
        "kpi": {
            "today": latest,
            "latest_partial": latest_partial,
            "period_total": period_total,
            "dod": delta,
            "active_channels": len(disp),
            "channels_in_view": len(allowed),
        },
        "daily_trend": trend,
        "cumulative_trend": cumulative,
        "by_channel": by_channel,
        "channel_share": [
            {"name": c["name"], "total": c["period_total"], "pct": c["share_pct"], "color": c["color"]}
            for c in by_channel if c["period_total"] > 0
        ],
        "channel_master": [
            {**c, "color": color_by.get(c["code"], "#888")} for c in disp
        ],
        "available_dates": all_dates,
    }


if __name__ == "__main__":
    import json
    for g in ("day", "month", "year"):
        v = build_view(granularity=g)
        print(f"\n=== {g} === buckets={v['buckets']}")
        print("  kpi.today:", v["kpi"]["today"])
        print("  by_channel:", [(c["name"], c["period_total"]) for c in v["by_channel"]])
