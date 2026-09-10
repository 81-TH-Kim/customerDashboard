"""서버측 인증 (Render 웹서비스 모드). 표준 라이브러리만 사용.

정적 사이트와 **같은 파일**(docs/users.json + docs/data/bundle.enc)을 데이터 저장소로 쓴다.
  - DASH_CONTENT_KEY (env, b64 32B) : 번들 복호화 + users.json 검증의 마스터 키
  - 로그인 = 비번으로 감싼 키를 풀어 CONTENT_KEY 와 일치하면 통과 → 세션 쿠키 발급
  - 변경(사용자/채널/수집)은 GH_TOKEN 으로 GitHub Contents API 에 커밋 → 재시작해도 유지
Render Free 는 영구 디스크가 없으므로, GitHub 저장소 자체가 영속 저장소 역할.
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
import urllib.request
from http.cookies import SimpleCookie
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store

ROOT = Path(__file__).resolve().parent.parent
USERS_FILE = ROOT / "docs" / "users.json"
BUNDLE_FILE = ROOT / "docs" / "data" / "bundle.enc"
SESSION_TTL = 12 * 3600
COOKIE = "dash_sid"
STATE_TTL = 300         # 메모리 캐시 유효기간(초). raw.githubusercontent CDN 지연 감안

GH_REPO = os.environ.get("GH_REPO", "81-TH-Kim/customerDashboard")
GH_BRANCH = os.environ.get("GH_BRANCH", "main")

_b64 = lambda b: base64.b64encode(b).decode()
_ub64 = lambda s: base64.b64decode(s + "=" * (-len(s) % 4))
_b64u = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
_ub64u = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# --------------------------------------------------------------------------- #
def content_key() -> bytes:
    k = os.environ.get("DASH_CONTENT_KEY")
    if not k:
        raise RuntimeError("DASH_CONTENT_KEY 환경변수가 필요합니다 (server 모드).")
    return base64.b64decode(k)


def _secret() -> bytes:
    s = os.environ.get("SESSION_SECRET")
    if s:
        return s.encode()
    if not getattr(_secret, "_tmp", None):
        print("[경고] SESSION_SECRET 미설정 — 임시 키(재시작 시 로그인 풀림)", file=sys.stderr)
        _secret._tmp = secrets.token_bytes(32)
    return _secret._tmp


# --------------------------------------------------------------------------- #
# AES-GCM (WebCrypto 호환: nonce 12B, tag 마지막 16B)
# --------------------------------------------------------------------------- #
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes as _h
    _HAVE_CRYPTO = True
except ImportError:
    _HAVE_CRYPTO = False


def _derive_kek(password: str, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(algorithm=_h.SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(password.encode("utf-8"))


def _aes_dec(key: bytes, iv_b64: str, ct_b64: str) -> bytes:
    return AESGCM(key).decrypt(_ub64(iv_b64), _ub64(ct_b64), None)


def _aes_enc(key: bytes, plaintext: bytes) -> dict:
    iv = secrets.token_bytes(12)
    return {"iv": _b64(iv), "ct": _b64(AESGCM(key).encrypt(iv, plaintext, None))}


# --------------------------------------------------------------------------- #
# 상태(users + bundle) 로드 — 로컬 파일 우선, GH_TOKEN 있으면 GitHub raw
# --------------------------------------------------------------------------- #
_cache = {"t": 0, "users": None, "bundle": None}


def _gh_raw(path: str) -> bytes | None:
    tok = os.environ.get("GH_TOKEN")
    url = f"https://raw.githubusercontent.com/{GH_REPO}/{GH_BRANCH}/{path}"
    req = urllib.request.Request(url)
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    try:
        return urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:  # noqa: BLE001
        print(f"[gh] raw {path} 실패: {e}", file=sys.stderr)
        return None


def _read_state(force=False) -> tuple[dict, dict]:
    if not force and _cache["users"] is not None and time.time() - _cache["t"] < STATE_TTL:
        return _cache["users"], _cache["bundle"]

    # GitHub 우선(재시작 후 최신 반영), 실패 시 로컬 커밋본
    ub = _gh_raw("docs/users.json") or (USERS_FILE.read_bytes() if USERS_FILE.exists() else b"{}")
    bb = _gh_raw("docs/data/bundle.enc") or (BUNDLE_FILE.read_bytes() if BUNDLE_FILE.exists() else b"{}")
    users = json.loads(ub)
    blob = json.loads(bb)
    try:
        bundle = json.loads(_aes_dec(content_key(), blob["iv"], blob["ct"]))
    except Exception as e:  # noqa: BLE001
        print(f"[state] 번들 복호화 실패: {e}", file=sys.stderr)
        bundle = {"inflow": {"records": []}, "channels": {"channels": []}, "log": {"logs": []}}
    _cache.update(t=time.time(), users=users, bundle=bundle)
    return users, bundle


def load_bundle(force=False) -> dict:
    return _read_state(force)[1]


def load_users_doc(force=False) -> dict:
    return _read_state(force)[0]


# serve.py 하위호환 별칭
def load_users(force=False) -> dict:
    return load_users_doc(force)


# --------------------------------------------------------------------------- #
# store.py 로컬 파일 <-> GitHub 번들 동기화
#   analytics.py / store.load_* 는 로컬 JSON 파일을 읽으므로,
#   server 모드에서는 시작 시 GitHub 번들을 로컬로 풀어두고(hydrate),
#   변경 후 다시 암호화해 커밋한다(persist).
# --------------------------------------------------------------------------- #
def hydrate_store() -> None:
    """GitHub 의 bundle.enc 를 복호화해 store.INFLOW/CHANNELS/LOG 평문 파일로 기록."""
    bundle = load_bundle(force=True)
    store.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _dump(store.INFLOW, bundle.get("inflow") or {"schema_version": 1, "records": []})
    _dump(store.CHANNELS, bundle.get("channels") or {"channels": []})
    _dump(store.LOG, bundle.get("log") or {"logs": []})
    print(f"[hydrate] inflow={len(bundle.get('inflow', {}).get('records', []))}건 "
          f"channels={len(bundle.get('channels', {}).get('channels', []))}개", file=sys.stderr)


def persist_store(message: str) -> bool:
    """store.INFLOW/CHANNELS/LOG 를 번들로 합쳐 재암호화 후 GitHub 커밋. 토큰 없으면 False."""
    bundle = {
        "inflow": store.load_inflow(),
        "channels": store.load_channels(),
        "log": store.load_log(),
    }
    _cache["bundle"] = bundle
    _cache["t"] = time.time()
    if not os.environ.get("GH_TOKEN"):
        return False
    _commit_bundle(bundle, message)
    return True


def _dump(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _gh_put(path: str, content_str: str, message: str) -> None:
    tok = os.environ.get("GH_TOKEN")
    if not tok:
        print("[gh] GH_TOKEN 없음 — 변경이 이번 인스턴스에서만 유지됩니다.", file=sys.stderr)
        return
    api = f"https://api.github.com/repos/{GH_REPO}/contents/{path}"
    # sha 조회
    sha = None
    try:
        r = urllib.request.Request(api + f"?ref={GH_BRANCH}",
                                   headers={"Authorization": "Bearer " + tok,
                                            "Accept": "application/vnd.github+json"})
        sha = json.loads(urllib.request.urlopen(r, timeout=10).read()).get("sha")
    except Exception:
        pass
    payload = {"message": message, "branch": GH_BRANCH,
               "content": base64.b64encode(content_str.encode("utf-8")).decode()}
    if sha:
        payload["sha"] = sha
    req = urllib.request.Request(api, method="PUT",
                                 data=json.dumps(payload).encode(),
                                 headers={"Authorization": "Bearer " + tok,
                                          "Accept": "application/vnd.github+json",
                                          "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=15).read()
    print(f"[gh] committed {path}", file=sys.stderr)


def _commit_users(users: dict) -> None:
    _gh_put("docs/users.json", json.dumps(users, ensure_ascii=False, indent=2), "chore: 사용자 변경")


def _commit_bundle(bundle: dict, message: str) -> None:
    ck = content_key()
    blob = _aes_enc(ck, json.dumps(bundle, ensure_ascii=False).encode("utf-8"))
    _gh_put("docs/data/bundle.enc", json.dumps(blob), message)


# --------------------------------------------------------------------------- #
# 계정
# --------------------------------------------------------------------------- #
def list_users() -> list[dict]:
    doc = load_users_doc()
    return [{"id": k, "name": v.get("name", k), "role": v.get("role", "viewer")}
            for k, v in doc.get("users", {}).items()]


def verify(uid: str, password: str) -> dict | None:
    doc = load_users_doc()
    u = doc.get("users", {}).get(uid)
    if not u:
        return None
    iters = doc.get("iterations", 600000)
    try:
        kek = _derive_kek(password, _ub64(u["salt"]), iters)
        got = _aes_dec(kek, u["iv"], u["wrapped"])
    except Exception:
        return None
    if not hmac.compare_digest(got, content_key()):
        return None
    return {"id": uid, "name": u.get("name", uid), "role": u.get("role", "viewer")}


def _wrap_for(password: str, iters: int) -> dict:
    salt = secrets.token_bytes(16)
    w = _aes_enc(_derive_kek(password, salt, iters), content_key())
    return {"salt": _b64(salt), "iv": w["iv"], "wrapped": w["ct"]}


def set_user(uid: str, name: str, password: str, role: str = "viewer") -> None:
    doc = load_users_doc()          # 캐시(직전 변경 반영) 기준으로 이어서 수정
    doc.setdefault("users", {})[uid] = {"name": name, "role": role,
                                        **_wrap_for(password, doc.get("iterations", 600000))}
    _cache.update(t=time.time(), users=doc)
    _commit_users(doc)


def remove_user(uid: str) -> bool:
    doc = load_users_doc()
    if uid in doc.get("users", {}):
        del doc["users"][uid]
        _cache.update(t=time.time(), users=doc)
        _commit_users(doc)
        return True
    return False


def user_exists(uid: str) -> bool:
    return uid in load_users_doc().get("users", {})


def bootstrap() -> None:
    """users.json 이 비어있고 BOOTSTRAP_ADMIN=id:pw 가 있으면 첫 관리자 생성."""
    try:
        if load_users_doc(force=True).get("users"):
            return
    except Exception:
        return
    spec = os.environ.get("BOOTSTRAP_ADMIN", "")
    if ":" in spec:
        uid, pw = spec.split(":", 1)
        uid, pw = uid.strip(), pw.strip()
        if uid and len(pw) >= 4:
            doc = {"v": 1, "iterations": 600000, "users": {}}
            doc["users"][uid] = {"name": uid, "role": "admin", **_wrap_for(pw, 600000)}
            _commit_users(doc)
            _cache.update(t=time.time(), users=doc)
            print(f"[bootstrap] 관리자 '{uid}' 생성", file=sys.stderr)


# --------------------------------------------------------------------------- #
# 세션 쿠키
# --------------------------------------------------------------------------- #
def make_cookie(user: dict) -> str:
    payload = {"uid": user["id"], "name": user["name"], "role": user["role"],
               "exp": int(time.time()) + SESSION_TTL}
    body = _b64u(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64u(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return (f"{COOKIE}={body}.{sig}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL}"
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
    if not hmac.compare_digest(sig, _b64u(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())):
        return None
    try:
        p = json.loads(_ub64u(body))
    except Exception:
        return None
    if p.get("exp", 0) < time.time():
        return None
    # 제거된 사용자는 즉시 차단
    if not user_exists(p["uid"]):
        return None
    return {"id": p["uid"], "name": p.get("name", p["uid"]), "role": p.get("role", "viewer")}
