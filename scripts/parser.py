"""RPA 제휴현황 메일 파서 (독립 모듈).

메일 본문(HTML 우선, 없으면 plain text)에서 채널별 Y / N / 총합 표를 추출한다.

기준일(실적일자) 산정 우선순위
  1) 메일 제목 맨 끝의 날짜   예) "[RPA]플랫폼 제휴현황 2026-09-08"
  2) (제목에 날짜가 없으면) 메일 수신일 − 1일  ← fallback, 경고 남김

실제 메일 형식 예시
-------------------
제목 : [RPA]플랫폼 제휴현황 2026-09-08
본문 :
    유입채널 | Y | N | 총합
    NAVER    | 305 | 11 | 316
    ...
    총합계   | 352 | 17 | 369
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

# 합계 행으로 취급할 채널명(대소문자 무시)
_TOTAL_ROW_KEYS = {"총합계", "합계", "total", "sum", "계"}
# 헤더 행으로 취급할 첫 칼럼 값
_HEADER_ROW_KEYS = {"유입채널", "채널", "channel"}


class ParseError(Exception):
    pass


@dataclass
class ChannelRow:
    channel: str
    y: int
    n: int
    total: int


@dataclass
class ParsedMail:
    date: str                         # 기준일 YYYY-MM-DD
    channels: list[ChannelRow]
    totals: dict                      # {"y","n","total"} - 본문 총합계 행 (없으면 합산값)
    computed_totals: dict             # 채널 합산값 (검증용)
    date_source: str = "subject"      # 'subject' | 'received-1d'
    warnings: list[str] = field(default_factory=list)

    def to_record(self, source: dict, origin: str = "gmail") -> dict:
        return {
            "date": self.date,
            "date_source": self.date_source,
            "channels": [asdict(c) for c in self.channels],
            "totals": self.totals,
            "origin": origin,
            "source": source,
            "collected_at": datetime.now(KST).isoformat(timespec="seconds"),
        }


# --------------------------------------------------------------------------- #
# HTML 표 추출
# --------------------------------------------------------------------------- #
class _TableExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._cur_table: list[list[str]] | None = None
        self._cur_row: list[str] | None = None
        self._cur_cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._cur_table = []
        elif tag == "tr" and self._cur_table is not None:
            self._cur_row = []
        elif tag in ("td", "th") and self._cur_row is not None:
            self._cur_cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cur_cell is not None:
            self._cur_row.append("".join(self._cur_cell).strip())
            self._cur_cell = None
        elif tag == "tr" and self._cur_row is not None:
            if self._cur_row:
                self._cur_table.append(self._cur_row)
            self._cur_row = None
        elif tag == "table" and self._cur_table is not None:
            if self._cur_table:
                self.tables.append(self._cur_table)
            self._cur_table = None

    def handle_data(self, data):
        if self._cur_cell is not None:
            self._cur_cell.append(data)


def _to_int(value: str) -> int:
    cleaned = re.sub(r"[,\s]", "", value or "")
    if not re.fullmatch(r"-?\d+", cleaned):
        raise ParseError(f"숫자가 아닌 값: {value!r}")
    return int(cleaned)


def _rows_from_html(html: str) -> list[list[str]]:
    ex = _TableExtractor()
    ex.feed(html)
    # Y/N/총합 헤더를 가진 표를 우선 선택
    for table in ex.tables:
        header = [c.strip().lower() for c in table[0]]
        if "y" in header and "n" in header:
            return table
    return ex.tables[0] if ex.tables else []


def _rows_from_text(text: str) -> list[list[str]]:
    """plain text fallback: '채널명 다음에 정수 3개' 패턴을 한 행으로 묶는다."""
    tokens = [t.strip() for t in re.split(r"[\n\r\t]+", text) if t.strip()]
    rows: list[list[str]] = []
    i = 0
    while i < len(tokens):
        name = tokens[i]
        nums = tokens[i + 1 : i + 4]
        if len(nums) == 3 and all(re.fullmatch(r"-?[\d,]+", x) for x in nums):
            rows.append([name, *nums])
            i += 4
        else:
            i += 1
    return rows


# --------------------------------------------------------------------------- #
# 기준일 추출
# --------------------------------------------------------------------------- #
def base_date_from_received(received_at: datetime) -> str:
    """메일 수신 시각(datetime, tz-aware 권장) → 기준일(수신일 전날, KST)."""
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=timezone.utc)
    local = received_at.astimezone(KST)
    return (local.date() - timedelta(days=1)).isoformat()


# 제목 끝의 날짜를 잡는 패턴들 (마지막으로 매칭되는 값을 사용)
_DATE_PATTERNS = [
    # 2026년 9월 8일 / 2026 년 09 월 08 일
    (re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"), "ymd"),
    # 2026-09-08 / 2026.9.8 / 2026/09/08 / 2026_09_08
    (re.compile(r"(\d{4})[.\-/_](\d{1,2})[.\-/_](\d{1,2})"), "ymd"),
    # 20260908
    (re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)"), "ymd"),
    # 9월 8일  (연도 없음 → 수신연도)
    (re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일"), "md"),
    # 09-08 / 09.08  (연도 없음, 뒤에 다른 숫자 없을 때)
    (re.compile(r"(?<!\d)(\d{1,2})[.\-/](\d{1,2})(?!\d)"), "md"),
]


def date_from_subject(subject: str, received_at: datetime | None = None) -> str | None:
    """제목 문자열에서 날짜를 추출해 YYYY-MM-DD 로 반환. 없으면 None.

    여러 개가 잡히면 '가장 마지막에 위치한' 날짜를 사용한다(제목 끝에 기준일이 붙는 형식).
    """
    if not subject:
        return None
    recv_local = (received_at or datetime.now(timezone.utc))
    if recv_local.tzinfo is None:
        recv_local = recv_local.replace(tzinfo=timezone.utc)
    recv_year = recv_local.astimezone(KST).year

    # 우선순위가 높은(구체적인) 패턴부터 확인하고, 매칭이 나오면 그 패턴의
    # '가장 마지막 위치' 값을 사용한다. 하위 패턴은 상위에서 못 찾았을 때만 본다.
    for pat, kind in _DATE_PATTERNS:
        best: tuple[int, date] | None = None
        for m in pat.finditer(subject):
            try:
                if kind == "ymd":
                    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                else:
                    mo, d = int(m.group(1)), int(m.group(2))
                    y = recv_year
                cand = date(y, mo, d)
            except ValueError:
                continue
            if abs(cand.year - recv_year) > 1:
                continue
            if best is None or m.start() > best[0]:
                best = (m.start(), cand)
        if best is not None:
            return best[1].isoformat()
    return None


def parse_mail(*, html_body: str | None, text_body: str | None, received_at: datetime,
               subject: str | None = None) -> ParsedMail:
    rows: list[list[str]] = []
    if html_body:
        rows = _rows_from_html(html_body)
    if not rows and text_body:
        rows = _rows_from_text(text_body)
    if not rows:
        raise ParseError("본문에서 채널 표를 찾지 못했습니다.")

    channels: list[ChannelRow] = []
    body_totals: dict | None = None
    warnings: list[str] = []

    for row in rows:
        if len(row) < 4:
            continue
        name = row[0].strip()
        low = name.lower()
        if low in _HEADER_ROW_KEYS or (row[1].strip().lower() == "y"):
            continue
        try:
            y, n, total = _to_int(row[1]), _to_int(row[2]), _to_int(row[3])
        except ParseError as e:
            warnings.append(f"행 건너뜀({name}): {e}")
            continue
        if low in _TOTAL_ROW_KEYS:
            body_totals = {"y": y, "n": n, "total": total}
            continue
        if y < 0 or n < 0 or total < 0:
            warnings.append(f"음수 값 감지: {name} y={y} n={n} total={total}")
        if y + n != total:
            warnings.append(f"합계 불일치: {name} Y({y})+N({n}) != 총합({total})")
        channels.append(ChannelRow(channel=name, y=y, n=n, total=total))

    if not channels:
        raise ParseError("유효한 채널 행이 없습니다.")

    computed = {
        "y": sum(c.y for c in channels),
        "n": sum(c.n for c in channels),
        "total": sum(c.total for c in channels),
    }
    if body_totals and body_totals != computed:
        warnings.append(
            f"본문 총합계{body_totals} 와 채널 합산{computed} 불일치 — 채널 합산값 사용"
        )
    totals = computed  # 항상 채널 합산값을 신뢰

    subj_date = date_from_subject(subject or "", received_at)
    if subj_date:
        base_date, date_source = subj_date, "subject"
    else:
        base_date, date_source = base_date_from_received(received_at), "received-1d"
        warnings.append(
            f"제목에서 날짜를 찾지 못해 '수신일 −1일'({base_date})로 기준일 지정 — 제목: {subject!r}"
        )

    return ParsedMail(
        date=base_date,
        channels=channels,
        totals=totals,
        computed_totals=computed,
        date_source=date_source,
        warnings=warnings,
    )


if __name__ == "__main__":
    import sys
    sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
    try:
        from _console import force_utf8
        force_utf8()
    except Exception:
        pass

    recv = datetime(2026, 9, 9, 7, 5, 0, tzinfo=timezone.utc)

    # 제목 날짜 추출 점검
    cases = [
        "[RPA]플랫폼 제휴현황 2026-09-08",
        "[RPA]플랫폼 제휴현황 20260908",
        "[RPA]플랫폼 제휴현황 2026.09.08",
        "[RPA]플랫폼 제휴현황 2026년 9월 8일",
        "Fw: [RPA]플랫폼 제휴현황 09-08",
        "Fw: RPA 고객분석팀테스트",  # 날짜 없음
    ]
    for c in cases:
        print(f"  {c!r:48} -> {date_from_subject(c, recv)}")

    sample_html = """<table><tbody>
    <tr><th>유입채널</th><th>Y</th><th>N</th><th>총합</th></tr>
    <tr><td>NAVER</td><td>305</td><td>11</td><td>316</td></tr>
    <tr><td>WEB</td><td>30</td><td>6</td><td>36</td></tr>
    <tr><td>총합계</td><td>335</td><td>17</td><td>352</td></tr>
    </tbody></table>"""
    r = parse_mail(html_body=sample_html, text_body=None, received_at=recv,
                   subject="[RPA]플랫폼 제휴현황 2026-09-08")
    import json
    print(json.dumps(r.to_record({"message_id": "test"}), ensure_ascii=False, indent=2))
    print("date_source:", r.date_source, "| warnings:", r.warnings)
