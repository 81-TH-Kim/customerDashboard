"""서버측 인증 (Render 웹서비스 모드). 표준 라이브러리만 사용.

- 계정: DATA_DIR/users.json  { "fmt":"server", "iterations":N, "users": {id: {name, role, salt, hash}} }
  hash = base64( pbkdf2_hmac('sha256', pw, salt, N, 32) )
- 세션: HMAC 서명 쿠키  sid = <b64(json payload)>.<b64(hmac)>
  payload = {"uid","name","role","exp"}
- SESSION_SECRET 은 환경변수. 없으면 임시 생성(재시작 시 세션 무효) + 경고.
- 최초 관리자: BOOTSTRAP_ADMIN="id:password" 환경변수 → users.json 없을 때 생성.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
from http.cookies import SimpleCookie
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store

ITERATIONS = 260_000
SESSION_TTL = 12 * 3600          # 12시간
COOKIE = "dash_sid"

_b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
def _ub64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _secret() -> bytes:
    s = os.environ.get("SESSION_SECRET")
    if s:
        return s.encode()
    if not getattr(_secret, "_warned", False):
        print("[경고] SESSION_SECRET 미설정 — 임시 키 사용(재시작 시 로그인 풀림)", file=sys.stderr)
        _secret._warned = True
    if not getattr(_secret, "_tmp", None):
        _secret._tmp = secrets.token_bytes(32)
    return _secret._tmp


# --------------------------------------------------------------------------- #
# 계정 저장소
# --------------------------------------------------------------------------- #
def _hash_pw(password: str, salt: bytes, iters: int = ITERATIONS) -> str:
    return _b64(hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters, 32))


def load_users() -> dict:
    if store.USERS.exists():
        return json.loads(store.USERS.read_text(encoding="utf-8"))
    return {"fmt": "server", "iterations": ITERATIONS, "users": {}}


def save_users(doc: dict) -> None:
    store.USERS.parent.mkdir(parents=True, exist_ok=True)
    tmp = store.USERS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, store.USERS)


def set_user(uid: str, name: str, password: str, role: str = "viewer") -> None:
    doc = load_users()
    salt = secrets.token_bytes(16)
    doc.setdefault("users", {})[uid] = {
        "name": name, "role": role,
        "salt": _b64(salt), "hash": _hash_pw(password, salt, doc.get("iterations", ITERATIONS)),
    }
    save_users(doc)


def remove_user(uid: str) -> bool:
    doc = load_users()
    if uid in doc.get("users", {}):
        del doc["users"][uid]
        save_users(doc)
        return True
    return False


def verify(uid: str, password: str) -> dict | None:
    doc = load_users()
    u = doc.get("users", {}).get(uid)
    if not u:
        return None
    calc = _hash_pw(password, _ub64(u["salt"]), doc.get("iterations", ITERATIONS))
    if not hmac.compare_digest(calc, u["hash"]):
        return None
    return {"id": uid, "name": u.get("name", uid), "role": u.get("role", "viewer")}


def bootstrap() -> None:
    """users.json 이 없고 BOOTSTRAP_ADMIN=id:pw 가 있으면 첫 관리자 생성."""
    if load_users().get("users"):
        return
    spec = os.environ.get("BOOTSTRAP_ADMIN", "")
    if ":" in spec:
        uid, pw = spec.split(":", 1)
        uid, pw = uid.strip(), pw.strip()
        if uid and len(pw) >= 4:
            set_user(uid, uid, pw, role="admin")
            print(f"[bootstrap] 관리자 '{uid}' 생성", file=sys.stderr)


# --------------------------------------------------------------------------- #
# 세션 쿠키
# --------------------------------------------------------------------------- #
def make_cookie(user: dict) -> str:
    payload = {"uid": user["id"], "name": user["name"], "role": user["role"],
               "exp": int(time.time()) + SESSION_TTL}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    val = f"{body}.{sig}"
    return (f"{COOKIE}={val}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL}"
            + ("; Secure" if os.environ.get("APP_MODE") == "server" else ""))


def clear_cookie() -> str:
    return f"{COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"


def read_session(cookie_header: str | None) -> dict | None:
    if not cookie_header:
        return None
    jar = SimpleCookie()
    try:
        jar.load(cookie_header)
    except Exception:
        return None
    m = jar.get(COOKIE)
    if not m or "." not in m.value:
        return None
    body, sig = m.value.rsplit(".", 1)
    expect = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expect):
        return None
    try:
        p = json.loads(_ub64(body))
    except Exception:
        return None
    if p.get("exp", 0) < time.time():
        return None
    return {"id": p["uid"], "name": p.get("name", p["uid"]), "role": p.get("role", "viewer")}


if __name__ == "__main__":
    import argparse
    from _console import force_utf8
    force_utf8()
    ap = argparse.ArgumentParser(description="서버 모드 계정 관리 (DATA_DIR/users.json)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add"); p.add_argument("--id", required=True); p.add_argument("--name", required=True)
    p.add_argument("--password", required=True); p.add_argument("--admin", action="store_true")
    sub.add_parser("list")
    p = sub.add_parser("remove"); p.add_argument("--id", required=True)
    a = ap.parse_args()
    if a.cmd == "add":
        set_user(a.id, a.name, a.password, "admin" if a.admin else "viewer")
        print(f"저장: {a.id} ({a.name})  → {store.USERS}")
    elif a.cmd == "remove":
        print("제거됨" if remove_user(a.id) else "없음")
    else:
        for uid, u in load_users().get("users", {}).items():
            print(f"  {uid:<14}{u.get('name',''):<16}{u.get('role','viewer')}")
