# customerDashboard — 제휴 마케팅 고객 유입현황 대시보드

RPA가 매일 보내는 `[RPA]플랫폼 제휴 현황_<날짜>` 메일을 수집해
**일자·월·년 단위 × 채널별 유입현황**을 웹 대시보드로 제공합니다.

## 배포 방식 2가지

| | GitHub Pages (static) | **Render (server) — 권장** |
|---|---|---|
| 인증 | 클라이언트 (데이터 AES-GCM 암호화) | **서버 세션** (비번은 서버에서 PBKDF2 검증) |
| 데이터 노출 | 암호문이 공개 저장소에 올라감 | 서버에만 존재, 인증 요청에만 응답 |
| 사용자 관리 | 화면 편집 → 파일/토큰 커밋 | **화면에서 추가/삭제 즉시 반영, 즉시 차단** |
| 데이터 갱신 | 로컬 수집 → `publish.bat` | 서버가 **매일 자동 수집** |
| 비용 | 무료 | Render Free (15분 무사용 시 슬립) |

같은 코드가 `APP_MODE` 로 두 모드를 지원합니다.

### 🅰 Render 배포 (server 모드)

1. GitHub 저장소에 이 코드가 올라가 있어야 함
2. **Render** (render.com) 가입 → Dashboard → **New → Blueprint**
3. 이 저장소 연결 → `render.yaml` 자동 인식 → **Apply**
4. 아래 환경변수 입력 (Render 가 물어봄):
   - `BOOTSTRAP_ADMIN` = `doyourself:강한비밀번호` ← 첫 관리자 (users.json 없을 때만 생성)
   - `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` = Gmail 앱 비밀번호 (자동 수집용, 선택)
5. 배포 완료 → `https://customer-dashboard-xxxx.onrender.com` 접속 → 로그인
6. **데이터 초기 업로드**: 관리자 로그인 → 관리자 화면 → 📥 데이터 업로드 →
   로컬 `data/live/inflow.json` · `channels.json` 선택 → 업로드
   (이후엔 서버가 매일 07:05 자동 수집)

이후 **사용자 추가/삭제, 채널 관리, 지금 수집** 전부 관리자 화면에서 즉시 됩니다.

### 🅱 GitHub Pages (static 모드)

**https://81-th-kim.github.io/customerDashboard/**
> Settings → Pages → Deploy from branch **main** / folder **/docs**

- 데이터는 `docs/data/bundle.enc` 로 암호화(AES-256-GCM). 로그인 = 비밀번호로 복호화.
- 링크를 알아도 로그인 없이는 못 봅니다 (암호문만 공개).
- 사용자/채널 관리는 관리자 화면에서 편집 후 **GitHub 연결**(PAT) 로 즉시 커밋 (README 4-1).
- 공개 데모: `demo` / `demo1234` (샘플)

---

## 구조

```
docs/                     ← GitHub Pages 정적 사이트 (서버 불필요)
  login.html  auth.js      로그인 화면 + 클라이언트 암·복호화 (PBKDF2·AES-GCM)
  index.html  app.js  analytics.js  style.css   대시보드 (일/월/년, 채널별 차트)
  report.html                                    PDF용 리포트
  admin.html  admin.js                           채널 관리 + 사용자 관리(관리자)
  lib/chart.umd.min.js                           Chart.js
  data/bundle.enc          ← 암호화된 데이터 번들 (inflow+channels+log)
  users.json               ← 로그인 사용자 목록 (비번은 없음, 감싼 키만)

scripts/                  ← 로컬 전용 (Python)
  serve.py               로컬 미리보기 서버 (docs/ 서비스)
  analytics.py           집계 로직 (analytics.js 와 동일 — 검증용)
  collect.py             메일 수집 → data/live/*.json 적재
  imap_client.py         Gmail IMAP (앱 비밀번호)
  parser.py              RPA 메일 파서 + 제목 날짜 추출
  import_history.py / import_xlsx_history.py   과거 데이터 적재
  manage_users.py        ★ 사용자 추가/삭제 + 데이터 암호화(pack)
  store.py               파일 저장소

data/
  *.sample.json          샘플 데이터 (공개 데모용)
  history_sample.csv     과거 CSV 적재 양식
  live/                  ← 로컬 실 데이터 (git 제외)

run_collect.bat          매일 07:30 자동 수집 (작업 스케줄러 등록됨)
publish.bat              data/live 암호화 → docs/data/bundle.enc → git push (확인 프롬프트)
.env / .env.example      Gmail 앱 비밀번호, DASH_CONTENT_KEY (git 제외)
```

---

## 1. 로컬 미리보기

```bash
python scripts/serve.py --open      # http://127.0.0.1:8787
```
또는 정적 서버 아무거나: `python -m http.server 8000 -d docs`

- `data/live/*.json` 이 있으면 그 실 데이터를, 없으면 `docs/data/*.json`(샘플)을 보여줍니다.
- 대시보드: **[일별] [월별] [년도별]** 세그먼트 · 채널별 유입 추이(선/누적막대) · 규모 · 비중 · 상세표
- 진행 중인 월/년은 "진행 중" 표시 + 전월/전년 대비 자동 생략

---

## 2. 메일 수집 (Gmail IMAP)

**준비 (최초 1회, 약 3분)**
1. Google 계정 → 보안 → **2단계 인증** 사용
2. Google 계정 → 보안 → **앱 비밀번호** → 16자리 생성
3. `.env` (없으면 `.env.example` 복사) 에 입력:
   ```
   GMAIL_ADDRESS=you@gmail.com
   GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx
   ```
4. 확인: `python scripts/imap_client.py`

**수집**
```bash
python scripts/collect.py               # 최근 7일 신규 메일 → data/live/
python scripts/collect.py --days 30
python scripts/collect.py --replace     # 같은 기준일 덮어쓰기
python scripts/collect.py --dry-run
python scripts/collect.py --eml x.eml   # 저장한 .eml 1건
```
- 기준일 = 제목 끝 날짜(`[RPA]플랫폼 제휴 현황_2026-09-08` → `2026-09-08`), 없으면 수신일−1일 + 경고
- 제목에 `플랫폼`/`제휴` 포함된 메일만 수집 (테스트·무관 메일 제외)
- 중복 방지: 메일 Message-ID + 기준일

**매일 자동** — Windows 작업 스케줄러 `ABL_RPA_제휴현황_수집`(매일 07:30, `run_collect.bat`) 등록됨.
```powershell
Get-ScheduledTaskInfo ABL_RPA_제휴현황_수집
Start-ScheduledTask   ABL_RPA_제휴현황_수집
```

---

## 3. 과거 데이터 적재

```bash
# CSV (date,channel,y,n,total)
python scripts/import_history.py data/파일.csv --dry-run
python scripts/import_history.py data/파일.csv

# 월별 누적 엑셀 (보장분석 이용건 / 마케팅 동의건 / 동의율)
python scripts/import_xlsx_history.py "파일.xlsx" --year 2026 --months 1-8
```
채널 매핑: 홈페이지→WEB, 인터넷보험→CM, 모바일센터→MobileCenter, 우리원뱅킹→wonMobile, NAVER, PAYCO

---

## 4. 로그인 사용자 관리 (`manage_users.py`)

정적 사이트라 서버 계정 DB가 없습니다. 대신 **데이터를 암호화**하고,
사용자별로 복호화 키를 그 사람 비밀번호로 감싸(wrap) `docs/users.json` 에 둡니다.
`users.json` 에는 **비밀번호가 저장되지 않습니다** (PBKDF2 salt + 감싼 키만).

```bash
# 최초 1회 — 관리자 생성 + 암호화 키(.env: DASH_CONTENT_KEY) 발급
python scripts/manage_users.py init --admin-id kim --admin-name "김관리자"

python scripts/manage_users.py add   --id lee  --name "이임원"        # 조회 사용자
python scripts/manage_users.py add   --id park --name "박부장" --admin # 관리자
python scripts/manage_users.py passwd --id lee                         # 비번 변경
python scripts/manage_users.py remove --id lee                         # 제거
python scripts/manage_users.py list
python scripts/manage_users.py rekey    # 키 교체(전원 임시비번 재발급) — 완전 차단 시
```
- **제거(remove)** 는 로그인만 막습니다. 이미 데이터를 받아 본 사람의 접근까지
  끊으려면 **`rekey`** (키 교체 + 데이터 재암호화) 를 실행하세요.

### 4-1. 화면에서 바로 적용 (GitHub 연결)

관리자 화면(로그인 후)에서 **채널/사용자를 편집 → "저장 및 적용" 한 번**으로 사이트에 반영됩니다(1~2분).

1. GitHub → Settings → Developer settings → **Fine-grained tokens** → Generate new token
   - Repository access: **`customerDashboard` 만**
   - Permissions: **Contents → Read and write**
   - Expiration: 짧게 (예: 90일)
2. 관리자 화면 상단 **🔗 GitHub 연결** 펼치기 → 토큰 붙여넣고 **연결**
   (토큰은 이 브라우저에만 저장됩니다. 저장소·파일에 절대 올라가지 않음)
3. 이후 **저장 및 적용** = `docs/users.json` / `docs/data/bundle.enc` 자동 커밋
4. 미연결이면 **파일로 저장** 버튼으로 다운로드 → 수동 커밋 / `publish.bat`

> 채널 설정은 화면 편집분이 우선됩니다. 로컬 `publish.bat` 는 채널을 덮어쓰지 않습니다
> (덮어쓰려면 `python scripts/manage_users.py pack --channels-from-live`).

## 5. 배포

```bash
publish.bat        # 확인 → data/live 암호화 → docs/data/bundle.enc → git commit + push
```
푸시 후 1~2분이면 사이트에 반영됩니다. (사이트엔 암호문만 올라감)

---

## 6. 더 강력한 접근 제어 (선택)

| 방법 | 특징 |
|---|---|
| 지금 방식 (암호화 + 로그인) | 무료. 링크만으론 못 봄. 비번 공유 관리는 수동 |
| 저장소 **Private + GitHub Pro** ($4/월) | Pages 자체가 비공개, GitHub 계정으로 접근 |
| **Cloudflare Pages + Access** (무료 50명) | 이메일 OTP/SSO, 즉시 차단, 코드 변경 없음 |

Cloudflare Access: Cloudflare에 이 저장소를 Pages로 연결 → Zero Trust → Access →
Application 추가(도메인 = pages.dev 주소) → 정책에 허용 이메일 지정. 끝.

---

## 용어

- **총합** = 보장분석 이용건 (대표 지표) = Y + N
- **Y** = 마케팅 동의건 / **N** = 미동의건, 동의율 = Y / 총합
- 조회 단위: 일별(RPA 메일=일별) / 월별·년도별(합산). 과거 월별 데이터는 해당 월 말일 레코드
