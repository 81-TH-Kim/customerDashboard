"""Windows 콘솔(cp949)에서도 한글/기호 출력이 깨지지 않도록 표준출력을 UTF-8로 전환."""
from __future__ import annotations

import sys


def force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
