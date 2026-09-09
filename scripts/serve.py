"""제휴현황 대시보드 로컬 웹서버 (Python 표준 라이브러리만 사용).

  python scripts/serve.py            # http://127.0.0.1:8787
  python scripts/serve.py --port 9000 --open

API
  GET  /api/dashboard?start=&end=&channels=A,B&types=포털&quick=7d
  GET  /api/channels
  POST /api/channels           (관리자: 채널 마스터 저장)
  GET  /api/logs
  POST /api/collect            (지금 메일 수집 실행)
  GET  /report?...(동일 파라미터)  → 인쇄용 리포트 페이지 (브라우저 인쇄 → PDF)
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import webbrowser
from contextlib import redirect_stdout, redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "docs"   # 정적 사이트 = GitHub Pages 배포본. 로컬 미리보기도 동일 파일 사용.
sys.path.insert(0, str(ROOT / "scripts"))

import analytics
import store
from _console import force_utf8

force_utf8()

MIME = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml", ".ico": "image/x-icon", ".map": "application/json",
}


def _split(v: str | None) -> list[str]:
    return [x.strip() for x in v.split(",")] if v else []


class Handler(BaseHTTPRequestHandler):
    server_version = "AllianceDash/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s - %s\n" % (self.address_string(), fmt % args))

    # ------------------------------------------------------------------ #
    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _view_from_query(self, qs: dict) -> dict:
        return analytics.build_view(
            granularity=(qs.get("granularity", ["day"])[0]) or "day",
            start=(qs.get("start", [None])[0]) or None,
            end=(qs.get("end", [None])[0]) or None,
            channels=_split(qs.get("channels", [None])[0]) or None,
        )

    # ------------------------------------------------------------------ #
    def do_GET(self):
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)
        try:
            if path == "/api/dashboard":
                return self._json(self._view_from_query(qs))
            if path == "/api/channels":
                return self._json(store.load_channels())
            if path == "/api/logs":
                return self._json(store.load_log())
            if path == "/report":
                html = (WEB / "report.html").read_text(encoding="utf-8")
                return self._send(200, html.encode("utf-8"), MIME[".html"])
            return self._static(path)
        except Exception as e:  # noqa: BLE001
            import traceback; traceback.print_exc()
            return self._json({"error": str(e)}, 500)

    do_HEAD = do_GET

    def do_POST(self):
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            if u.path == "/api/channels":
                payload = json.loads(raw or b"{}")
                if "channels" not in payload:
                    return self._json({"error": "channels 필드가 필요합니다"}, 400)
                store.save_channels({"_comment": store.load_channels().get("_comment", ""),
                                     "channels": payload["channels"]})
                return self._json({"ok": True, "channels": store.load_channels()["channels"]})
            if u.path == "/api/collect":
                opts = json.loads(raw or b"{}")
                return self._json(self._run_collect(opts))
            return self._json({"error": "unknown endpoint"}, 404)
        except Exception as e:  # noqa: BLE001
            import traceback; traceback.print_exc()
            return self._json({"error": str(e)}, 500)

    # ------------------------------------------------------------------ #
    def _run_collect(self, opts: dict) -> dict:
        import collect
        argv = ["--days", str(opts.get("days", 7))]
        if opts.get("replace"):
            argv.append("--replace")
        if opts.get("query"):
            argv += ["--query", opts["query"]]
        buf = io.StringIO()
        code = 1
        try:
            with redirect_stdout(buf), redirect_stderr(buf):
                sys.argv = ["collect.py", *argv]
                code = collect.main()
        except SystemExit as e:
            code = int(e.code or 0)
        except Exception as e:  # noqa: BLE001
            buf.write(f"\n[예외] {e}")
        logs = store.load_log().get("logs", [])
        return {"exit_code": code, "output": buf.getvalue(),
                "recent_logs": logs[-10:]}

    def _static(self, path: str):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")

        # /data/*.json 은 로컬 실 데이터(data/live/)가 있으면 그걸 우선 노출
        if rel.startswith("data/") and rel.endswith(".json"):
            live = (ROOT / "data" / "live" / Path(rel).name)
            if live.is_file():
                return self._send(200, live.read_bytes(), MIME[".json"])

        target = (WEB / rel).resolve()
        if not str(target).startswith(str(WEB.resolve())) or not target.is_file():
            return self._send(404, b"Not Found", "text/plain; charset=utf-8")
        self._send(200, target.read_bytes(),
                   MIME.get(target.suffix, "application/octet-stream"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"제휴현황 대시보드  →  {url}")
    print(f"  관리자 화면      →  {url}admin.html")
    print("  (Ctrl+C 로 종료)")
    if args.open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
