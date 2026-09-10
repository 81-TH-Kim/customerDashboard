"""제휴현황 대시보드 웹서버 (Python 표준 라이브러리만 사용).

로컬 미리보기:  python scripts/serve.py --open        # http://127.0.0.1:8787  (static 모드)
Render 배포:    APP_MODE=server python scripts/serve.py --host 0.0.0.0 --port $PORT

모드
  static  : docs/ 정적 파일만 서비스 (GitHub Pages 와 동일, 프론트가 클라이언트 복호화)
  server  : 로그인 세션 + 서버측 데이터 API. 데이터는 서버에만 있고 인증된 요청에만 응답.

server 모드 환경변수
  DASH_CONTENT_KEY    데이터 번들 복호화 마스터 키 (b64 32B, 필수)
  GH_TOKEN            변경분을 GitHub 저장소에 되커밋 (Contents R/W, 필수 권장)
  SESSION_SECRET      세션 쿠키 서명 키 (필수 권장)
  BOOTSTRAP_ADMIN     "id:password" — users.json 없을 때 첫 관리자 생성 (이미 있으면 무시)
  DASH_DATA_DIR       임시 작업 디렉터리 (기본 /tmp/dash, 재시작 시 GitHub 에서 재구성)
  COLLECT_ON_SERVER   "1" 이면 매일 07:05(KST) 자동 메일 수집
  GMAIL_ADDRESS / GMAIL_APP_PASSWORD   IMAP 수집용
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import threading
import time
import webbrowser
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "docs"
sys.path.insert(0, str(ROOT / "scripts"))

import analytics
import auth_server as A
import store
from _console import force_utf8

force_utf8()

APP_MODE = os.environ.get("APP_MODE", "static")  # static | server
MIME = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml", ".ico": "image/x-icon", ".map": "application/json",
    ".enc": "application/json; charset=utf-8",
}
# 로그인 없이 접근 가능한 정적 자원
PUBLIC = {"/login.html", "/auth.js", "/style.css", "/lib/chart.umd.min.js",
          "/favicon.ico", "/github.js", "/analytics.js"}
GATED_PAGES = {"/", "/index.html", "/report.html", "/admin.html"}


def _split(v):
    return [x.strip() for x in v.split(",")] if v else []


def _inject_mode(html: str, user: dict | None) -> str:
    tag = (f'<script>window.APP_MODE={json.dumps(APP_MODE)};'
           f'window.__ME={json.dumps(user, ensure_ascii=False)};</script>')
    low = html.lower()
    i = low.find("<head>")
    return html[: i + 6] + tag + html[i + 6:] if i >= 0 else tag + html


class Handler(BaseHTTPRequestHandler):
    server_version = "AllianceDash/2.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s - %s\n" % (self.address_string(), fmt % args))

    # -------- 응답 헬퍼 -------- #
    def _send(self, code, body: bytes, ctype, extra_headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200, extra_headers=None):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8", extra_headers)

    def _redirect(self, location):
        self._send(302, b"", "text/plain", [("Location", location)])

    # -------- 세션 -------- #
    @property
    def session(self):
        if not hasattr(self, "_sess"):
            self._sess = A.read_session(self.headers.get("Cookie")) if APP_MODE == "server" else None
        return self._sess

    def _require(self, admin=False):
        """인증 확인. 통과 못하면 응답을 보내고 None 대신 False 반환."""
        if APP_MODE != "server":
            return {"id": "local", "name": "local", "role": "admin"}
        s = self.session
        if not s:
            self._json({"error": "로그인이 필요합니다."}, 401)
            return None
        if admin and s.get("role") != "admin":
            self._json({"error": "관리자 권한이 필요합니다."}, 403)
            return None
        return s

    # -------- GET -------- #
    def do_GET(self):
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)
        try:
            if path == "/api/me":
                s = self.session
                return self._json({"user": s} if s else {"user": None}, 200 if s else 200)
            if path == "/api/bundle":
                if not self._require():
                    return
                return self._json({
                    "inflow": store.load_inflow(),
                    "channels": store.load_channels(),
                    "log": store.load_log(),
                })
            if path.startswith("/api/"):
                if not self._require(admin=(path == "/api/admin/users")):
                    return
                if path == "/api/dashboard":
                    return self._json(analytics.build_view(
                        granularity=(qs.get("granularity", ["day"])[0]) or "day",
                        start=(qs.get("start", [None])[0]) or None,
                        end=(qs.get("end", [None])[0]) or None,
                        channels=_split(qs.get("channels", [None])[0]) or None))
                if path == "/api/channels":
                    return self._json(store.load_channels())
                if path == "/api/logs":
                    return self._json(store.load_log())
                if path == "/api/admin/users":
                    doc = A.load_users()
                    return self._json({"users": [
                        {"id": k, "name": v.get("name", k), "role": v.get("role", "viewer")}
                        for k, v in doc.get("users", {}).items()]})
                return self._json({"error": "unknown endpoint"}, 404)

            if path == "/report":            # 하위호환
                path = "/report.html"
            return self._static(path)
        except Exception as e:  # noqa: BLE001
            import traceback; traceback.print_exc()
            return self._json({"error": str(e)}, 500)

    do_HEAD = do_GET

    # -------- POST / DELETE -------- #
    def do_POST(self):
        u = urlparse(self.path)
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        try:
            if u.path == "/api/login":
                if APP_MODE != "server":
                    return self._json({"ok": True, "user": {"id": "local", "name": "local", "role": "admin"}})
                user = A.verify((body.get("id") or "").strip(), body.get("password") or "")
                if not user:
                    return self._json({"ok": False, "error": "아이디 또는 비밀번호가 올바르지 않습니다."}, 401)
                return self._json({"ok": True, "user": user}, extra_headers=[("Set-Cookie", A.make_cookie(user))])
            if u.path == "/api/logout":
                return self._json({"ok": True}, extra_headers=[("Set-Cookie", A.clear_cookie())])

            if u.path == "/api/channels":
                if not self._require(admin=True):
                    return
                if "channels" not in body:
                    return self._json({"error": "channels 필드 필요"}, 400)
                store.save_channels({"_comment": store.load_channels().get("_comment", ""),
                                     "channels": body["channels"]})
                pushed = A.persist_store("chore: 채널 마스터 변경 (관리자 화면)")
                return self._json({"ok": True, "pushed": pushed,
                                   "channels": store.load_channels()["channels"]})

            if u.path == "/api/collect":
                if not self._require(admin=True):
                    return
                return self._json(self._run_collect(body))

            if u.path == "/api/admin/import":
                if not self._require(admin=True):
                    return
                wrote = []
                if isinstance(body.get("inflow"), dict) and "records" in body["inflow"]:
                    store._write(store.INFLOW, body["inflow"]); wrote.append("inflow")
                if isinstance(body.get("channels"), dict) and "channels" in body["channels"]:
                    store.save_channels({"_comment": body["channels"].get("_comment", ""),
                                         "channels": body["channels"]["channels"]}); wrote.append("channels")
                if isinstance(body.get("log"), dict) and "logs" in body["log"]:
                    store._write(store.LOG, body["log"]); wrote.append("log")
                pushed = A.persist_store("chore: 데이터 업로드 (관리자 화면)") if wrote else False
                return self._json({"ok": True, "wrote": wrote, "pushed": pushed} if wrote
                                  else {"error": "가져올 데이터가 없습니다."}, 200 if wrote else 400)

            if u.path == "/api/admin/users":
                if not self._require(admin=True):
                    return
                act = body.get("action")
                if act == "add":
                    uid = (body.get("id") or "").strip()
                    if not uid or len(body.get("password", "")) < 6:
                        return self._json({"error": "아이디/비밀번호(6자+) 필요"}, 400)
                    A.set_user(uid, body.get("name") or uid, body["password"],
                               "admin" if body.get("admin") else "viewer")
                    return self._json({"ok": True})
                if act == "passwd":
                    doc = A.load_users(); u2 = doc.get("users", {}).get(body.get("id"))
                    if not u2:
                        return self._json({"error": "없는 사용자"}, 404)
                    A.set_user(body["id"], u2.get("name", body["id"]), body["password"], u2.get("role", "viewer"))
                    return self._json({"ok": True})
                if act == "remove":
                    me = self.session
                    if me and me["id"] == body.get("id"):
                        return self._json({"error": "본인은 제거할 수 없습니다."}, 400)
                    return self._json({"ok": A.remove_user(body.get("id"))})
                return self._json({"error": "action?"}, 400)

            return self._json({"error": "unknown endpoint"}, 404)
        except Exception as e:  # noqa: BLE001
            import traceback; traceback.print_exc()
            return self._json({"error": str(e)}, 500)

    do_DELETE = do_POST

    # -------- 수집 -------- #
    def _run_collect(self, opts):
        import collect
        argv = ["--days", str(opts.get("days", 7))]
        if opts.get("replace"):
            argv.append("--replace")
        buf = io.StringIO(); code = 1
        try:
            with redirect_stdout(buf), redirect_stderr(buf):
                sys.argv = ["collect.py", *argv]
                code = collect.main()
        except SystemExit as e:
            code = int(e.code or 0)
        except Exception as e:  # noqa: BLE001
            buf.write(f"\n[예외] {e}")
        pushed = False
        try:
            pushed = A.persist_store("chore: 자동 수집 데이터 반영")
        except Exception as e:  # noqa: BLE001
            buf.write(f"\n[커밋 경고] {e}")
        return {"exit_code": code, "output": buf.getvalue(), "pushed": pushed,
                "recent_logs": store.load_log().get("logs", [])[-10:]}

    # -------- 정적 파일 -------- #
    def _static(self, path):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")

        # 게이트: server 모드에서 미로그인 → 로그인 페이지
        norm = "/" + rel
        if APP_MODE == "server" and not self.session:
            if norm in GATED_PAGES or norm == "/":
                return self._redirect("/login.html")

        if rel.startswith("data/") and rel.endswith(".json"):
            live = store.DATA_DIR / Path(rel).name
            if live.is_file():
                return self._send(200, live.read_bytes(), MIME[".json"])

        target = (WEB / rel).resolve()
        if not str(target).startswith(str(WEB.resolve())) or not target.is_file():
            return self._send(404, b"Not Found", "text/plain; charset=utf-8")

        if target.suffix == ".html":
            html = _inject_mode(target.read_text(encoding="utf-8"), self.session)
            return self._send(200, html.encode("utf-8"), MIME[".html"])
        self._send(200, target.read_bytes(), MIME.get(target.suffix, "application/octet-stream"))


# --------------------------------------------------------------------------- #
def _collector_loop():
    """server 모드 백그라운드: 매일 07:05(KST) 이후 첫 요청 처리 시점에 1회 수집."""
    import collect
    last_day = None
    while True:
        try:
            now = datetime.now(store.KST)
            if now.hour >= 7 and now.strftime("%Y-%m-%d") != last_day:
                last_day = now.strftime("%Y-%m-%d")
                print(f"[collector] {now:%Y-%m-%d %H:%M} 자동 수집 시작", file=sys.stderr)
                try:
                    sys.argv = ["collect.py", "--days", "3"]
                    collect.main()
                    A.persist_store("chore: 매일 자동 수집 반영")
                except SystemExit:
                    A.persist_store("chore: 매일 자동 수집 반영")
                except Exception as e:  # noqa: BLE001
                    print(f"[collector] 오류: {e}", file=sys.stderr)
        except Exception:
            pass
        time.sleep(1800)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8787)))
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()

    if APP_MODE == "server":
        store.DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            A.hydrate_store()          # GitHub 번들 → 로컬 평문 (analytics/store 용)
        except Exception as e:  # noqa: BLE001
            print(f"[시작] 번들 hydrate 실패(계속): {e}", file=sys.stderr)
        A.bootstrap()
        if os.environ.get("COLLECT_ON_SERVER") == "1":
            threading.Thread(target=_collector_loop, daemon=True).start()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"[{APP_MODE}] 제휴현황 대시보드  →  {url}")
    if args.open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
