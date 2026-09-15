"""자동 발행 (무인 실행용). run_collect.bat 이 collect.py 뒤에 이어서 호출한다.

절차:
  1. git pull --ff-only  — 관리자 화면(PAT)에서 채널/사용자를 편집했을 수 있으므로
     원격 최신 상태를 먼저 받는다. 충돌 등으로 실패하면 로컬 상태로 계속 진행.
  2. pack 전/후로 bundle.enc 를 **복호화해서** 내용을 비교한다. AES-GCM 은 매번
     IV 가 랜덤이라 같은 데이터를 다시 암호화해도 바이트가 달라지므로, 그냥
     git diff 로는 항상 "변경"으로 보인다 — 실제 내용(레코드/채널/로그)이
     달라졌을 때만 commit + push 한다(빈 발행 방지).
  3. 변경이 있으면 docs/data/bundle.enc 만 commit + push.

실패해도 로컬 data/live 데이터는 그대로 남으므로, 다음 실행(07:30/09:30) 이나
수동 publish.bat 으로 다시 시도할 수 있다.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from _console import force_utf8  # noqa: E402

force_utf8()

import manage_users as mu  # noqa: E402


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    print("$ " + " ".join(cmd))
    r = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, encoding="utf-8", errors="replace")
    if r.stdout:
        print(r.stdout.rstrip())
    if r.stderr:
        print(r.stderr.rstrip(), file=sys.stderr)
    return r


def _decrypt_bundle(ck: bytes) -> dict | None:
    if not mu.BUNDLE.exists():
        return None
    try:
        blob = json.loads(mu.BUNDLE.read_text(encoding="utf-8"))
        return json.loads(mu._dec(ck, blob))
    except Exception as e:  # noqa: BLE001
        print(f"[auto_publish] 기존 bundle.enc 복호화 실패(무시): {e}", file=sys.stderr)
        return None


class _PackArgs:
    channels_from_live = False  # 배포된(=pull 받은) 채널 설정 유지 — 관리자 화면 편집 우선
    from_ = "live"


def main() -> int:
    r = run(["git", "pull", "--ff-only", "origin", "main"])
    if r.returncode != 0:
        print("[auto_publish] git pull 실패 — 로컬 상태로 계속 진행", file=sys.stderr)

    ck = mu._content_key()
    if not ck:
        print("[auto_publish] DASH_CONTENT_KEY 없음 — 발행 중단", file=sys.stderr)
        return 1

    before = _decrypt_bundle(ck)
    try:
        mu.cmd_pack(_PackArgs(), key=ck)
    except Exception as e:  # noqa: BLE001
        print(f"[auto_publish] pack 실패 — 발행 중단: {e}", file=sys.stderr)
        return 1
    after = _decrypt_bundle(ck)

    if before == after:
        print("[auto_publish] 내용 변경 없음 — 발행 생략")
        run(["git", "checkout", "--", "docs/data/bundle.enc"])  # 재암호화로 생긴 diff 되돌림
        return 0

    run(["git", "add", "docs/data/bundle.enc"])
    r = run(["git", "commit", "-m", "chore: 데이터 자동 발행 (RPA 수집 반영, scheduled)"])
    if r.returncode != 0:
        print("[auto_publish] commit 실패 — 발행 중단", file=sys.stderr)
        return 1

    r = run(["git", "push", "origin", "main"])
    if r.returncode != 0:
        print("[auto_publish] push 실패 — 로컬 커밋은 남아있음. 다음 실행 시 재시도.", file=sys.stderr)
        return 1

    print("[auto_publish] 발행 완료 (1~2분 후 사이트 반영)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
