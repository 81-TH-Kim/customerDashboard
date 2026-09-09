"""대시보드 로그인 사용자 관리 + 데이터 암호화 (정적 사이트용).

정적 사이트(GitHub Pages)에는 서버가 없으므로, 데이터를 **암호화**해 올린다.
  - contentKey(32B 랜덤) : data 번들(inflow/channels/log)을 AES-256-GCM 으로 암호화
  - 사용자별로 contentKey 를 그 사람의 비밀번호로 감싸(wrap) docs/users.json 에 저장
  - 브라우저 로그인 = 비번으로 contentKey unwrap → 번들 복호화 → 대시보드 표시
  - contentKey 는 .env(DASH_CONTENT_KEY) 에만 평문 보관 (로컬 전용)

명령
  python scripts/manage_users.py init  --admin-id kim --admin-name "김관리자"
  python scripts/manage_users.py add   --id lee --name "이임원"
  python scripts/manage_users.py add   --id park --name "박부장" --admin
  python scripts/manage_users.py passwd --id lee
  python scripts/manage_users.py remove --id lee
  python scripts/manage_users.py list
  python scripts/manage_users.py pack                 # data/live → docs/data/bundle.enc 재암호화
  python scripts/manage_users.py rekey                # 키 교체(전원 임시비번 재발급)

비번 입력은 기본 대화형(getpass). 스크립트용은 --password 사용.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import sys
from getpass import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _console import force_utf8

force_utf8()

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
except ImportError:
    print("[설치 필요] pip install cryptography", file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
USERS = ROOT / "docs" / "users.json"
BUNDLE = ROOT / "docs" / "data" / "bundle.enc"
LIVE = ROOT / "data" / "live"
ITERATIONS = 600_000
VERIFY_TEXT = b"customerDashboard-ok"

b64 = lambda b: base64.b64encode(b).decode()
ub64 = lambda s: base64.b64decode(s)


# --------------------------------------------------------------------------- #
def _env() -> dict[str, str]:
    d: dict[str, str] = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip().strip('"').strip("'")
    return d


def _set_env(key: str, value: str) -> None:
    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    out, done = [], False
    for line in lines:
        if line.strip().startswith(key + "="):
            out.append(f"{key}={value}"); done = True
        else:
            out.append(line)
    if not done:
        out.append(f"{key}={value}")
    ENV.write_text("\n".join(out) + "\n", encoding="utf-8")


def _derive(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERATIONS)
    return kdf.derive(password.encode("utf-8"))


def _enc(key: bytes, plaintext: bytes) -> dict:
    iv = secrets.token_bytes(12)
    ct = AESGCM(key).encrypt(iv, plaintext, None)
    return {"iv": b64(iv), "ct": b64(ct)}


def _dec(key: bytes, blob: dict) -> bytes:
    return AESGCM(key).decrypt(ub64(blob["iv"]), ub64(blob["ct"]), None)


def _content_key() -> bytes:
    k = _env().get("DASH_CONTENT_KEY")
    if not k:
        print("[오류] .env 에 DASH_CONTENT_KEY 가 없습니다. 먼저 `init` 을 실행하세요.", file=sys.stderr)
        raise SystemExit(2)
    return ub64(k)


def _load_users() -> dict:
    if USERS.exists():
        return json.loads(USERS.read_text(encoding="utf-8"))
    return {"v": 1, "iterations": ITERATIONS, "verify": None, "users": {}}


def _save_users(u: dict) -> None:
    USERS.parent.mkdir(parents=True, exist_ok=True)
    USERS.write_text(json.dumps(u, ensure_ascii=False, indent=2), encoding="utf-8")


def _wrap_for(password: str, content_key: bytes) -> dict:
    salt = secrets.token_bytes(16)
    kek = _derive(password, salt)
    w = _enc(kek, content_key)
    return {"salt": b64(salt), "iv": w["iv"], "wrapped": w["ct"]}


def _ask_pw(args, confirm=True) -> str:
    if getattr(args, "password", None):
        return args.password
    pw = getpass("비밀번호: ")
    if confirm and getpass("비밀번호 확인: ") != pw:
        print("일치하지 않습니다."); raise SystemExit(1)
    if len(pw) < 6:
        print("비밀번호는 6자 이상."); raise SystemExit(1)
    return pw


# --------------------------------------------------------------------------- #
def cmd_init(args):
    if USERS.exists() and not args.force:
        print("이미 docs/users.json 이 있습니다. 다시 만들려면 --force.", file=sys.stderr)
        raise SystemExit(1)
    content_key = secrets.token_bytes(32)
    _set_env("DASH_CONTENT_KEY", b64(content_key))
    pw = _ask_pw(args)
    u = {"v": 1, "iterations": ITERATIONS,
         "verify": _enc(content_key, VERIFY_TEXT),
         "users": {args.admin_id: {"name": args.admin_name, "role": "admin",
                                   **_wrap_for(pw, content_key)}}}
    _save_users(u)
    cmd_pack(args, key=content_key)
    print(f"완료. 관리자 '{args.admin_id}' 생성. .env 에 DASH_CONTENT_KEY 저장됨.")
    print("→ publish.bat 로 docs/users.json + docs/data/bundle.enc 를 배포하세요.")


def cmd_add(args):
    ck = _content_key()
    u = _load_users()
    if args.id in u["users"] and not args.force:
        print(f"'{args.id}' 이미 존재. 비번 변경은 passwd, 강제는 --force.", file=sys.stderr)
        raise SystemExit(1)
    pw = _ask_pw(args)
    u["users"][args.id] = {"name": args.name, "role": "admin" if args.admin else "viewer",
                           **_wrap_for(pw, ck)}
    _save_users(u)
    print(f"추가: {args.id} ({args.name}, {'admin' if args.admin else 'viewer'})")
    print("→ publish.bat 로 배포")


def cmd_passwd(args):
    ck = _content_key()
    u = _load_users()
    if args.id not in u["users"]:
        print(f"'{args.id}' 없음"); raise SystemExit(1)
    pw = _ask_pw(args)
    keep = {k: u["users"][args.id][k] for k in ("name", "role")}
    u["users"][args.id] = {**keep, **_wrap_for(pw, ck)}
    _save_users(u)
    print(f"'{args.id}' 비밀번호 변경. → publish.bat 로 배포")


def cmd_remove(args):
    u = _load_users()
    if args.id not in u["users"]:
        print(f"'{args.id}' 없음"); raise SystemExit(1)
    del u["users"][args.id]
    _save_users(u)
    print(f"제거: {args.id}")
    print("※ 이미 데이터를 받아 본 사용자의 접근을 완전히 끊으려면 `rekey` 를 실행하세요.")
    print("→ publish.bat 로 배포")


def cmd_list(args):
    u = _load_users()
    print(f"{'ID':<14}{'이름':<16}{'권한'}")
    for uid, info in u["users"].items():
        print(f"{uid:<14}{info.get('name',''):<16}{info.get('role','viewer')}")


def cmd_rekey(args):
    old_u = _load_users()
    ids = list(old_u["users"].items())
    new_key = secrets.token_bytes(32)
    _set_env("DASH_CONTENT_KEY", b64(new_key))
    temp = {}
    users = {}
    for uid, info in ids:
        tp = secrets.token_urlsafe(9)
        temp[uid] = tp
        users[uid] = {"name": info.get("name", uid), "role": info.get("role", "viewer"),
                      **_wrap_for(tp, new_key)}
    _save_users({"v": 1, "iterations": ITERATIONS,
                 "verify": _enc(new_key, VERIFY_TEXT), "users": users})
    cmd_pack(args, key=new_key)
    print("키 교체 완료. 아래 임시 비밀번호를 각자에게 전달하세요(로그인 후 변경 권장):")
    for uid, tp in temp.items():
        print(f"  {uid:<14} {tp}")
    print("→ publish.bat 로 배포")


def cmd_pack(args, key: bytes | None = None):
    ck = key or _content_key()
    src = LIVE
    suffix = ""
    if getattr(args, "from_", "live") == "sample":
        src = ROOT / "data"
        suffix = ".sample"
    def _read(name):
        stem, ext = name.rsplit(".", 1)
        p = src / f"{stem}{suffix}.{ext}"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    # 채널 설정은 관리자 화면에서도 편집되므로, 배포된 bundle.enc 의 것을 유지한다.
    # (--channels-from-live 를 주면 로컬 data/live/channels.json 로 덮어씀)
    channels = None
    if not getattr(args, "channels_from_live", False) and suffix == "" and BUNDLE.exists():
        try:
            cur = json.loads(_dec(ck, json.loads(BUNDLE.read_text(encoding="utf-8"))))
            channels = cur.get("channels")
        except Exception:
            channels = None
    if channels is None:
        channels = _read("channels.json") or {"channels": []}

    bundle = {
        "inflow": _read("inflow.json") or {"records": []},
        "channels": channels,
        "log": _read("collection_log.json") or {"logs": []},
    }
    blob = _enc(ck, json.dumps(bundle, ensure_ascii=False).encode("utf-8"))
    BUNDLE.parent.mkdir(parents=True, exist_ok=True)
    BUNDLE.write_text(json.dumps(blob), encoding="utf-8")
    # 평문 데이터가 docs 에 남아있으면 삭제 (공개 노출 방지)
    for n in ("inflow.json", "channels.json", "collection_log.json"):
        p = BUNDLE.parent / n
        if p.exists():
            p.unlink()
    recs = len(bundle["inflow"].get("records", []))
    print(f"암호화 완료: docs/data/bundle.enc ({recs}건, {BUNDLE.stat().st_size:,}B)")


def cmd_demo(args):
    """공개 저장소 배포용. 샘플 데이터를 임시 키로 암호화 + demo 계정 생성.
    .env(DASH_CONTENT_KEY)는 건드리지 않는다(실 배포 키와 분리)."""
    ck = secrets.token_bytes(32)
    class _A:
        from_ = "sample"
    cmd_pack(_A(), key=ck)
    _save_users({"v": 1, "iterations": ITERATIONS,
                 "verify": _enc(ck, VERIFY_TEXT),
                 "users": {"demo": {"name": "데모", "role": "admin", **_wrap_for("demo1234", ck)}}})
    print("데모 번들/계정 생성 (demo / demo1234). docs/users.json + docs/data/bundle.enc")


def main() -> int:
    ap = argparse.ArgumentParser(description="대시보드 사용자 관리 + 데이터 암호화")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init"); p.add_argument("--admin-id", required=True)
    p.add_argument("--admin-name", required=True); p.add_argument("--password"); p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("add"); p.add_argument("--id", required=True); p.add_argument("--name", required=True)
    p.add_argument("--admin", action="store_true"); p.add_argument("--password"); p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("passwd"); p.add_argument("--id", required=True); p.add_argument("--password")
    p.set_defaults(fn=cmd_passwd)

    p = sub.add_parser("remove"); p.add_argument("--id", required=True); p.set_defaults(fn=cmd_remove)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    sub.add_parser("rekey").set_defaults(fn=cmd_rekey)
    p = sub.add_parser("pack"); p.add_argument("--from", dest="from_", choices=["live", "sample"], default="live")
    p.add_argument("--channels-from-live", action="store_true",
                   help="채널 설정을 data/live/channels.json 로 덮어씀 (기본: 배포본 유지)")
    p.set_defaults(fn=cmd_pack)

    p = sub.add_parser("demo")  # 공개 저장소용: 샘플 데이터 + demo/demo1234 계정
    p.set_defaults(fn=cmd_demo)

    args = ap.parse_args()
    args.fn(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
