# customerDashboard — 제휴 마케팅 고객 유입현황 대시보드

RPA가 매일 보내는 `[RPA]플랫폼 제휴 현황_<날짜>` 메일을 수집해
**일자·월·년 단위 × 채널별 유입현황**을 웹 대시보드로 제공합니다.

## 🌐 공개 사이트 (GitHub Pages)

**https://81-th-kim.github.io/customerDashboard/**

> Settings → Pages → Source: **Deploy from a branch** / Branch **main** / Folder **/docs**
>
> ⚠️ **공개 사이트에는 `docs/data/*.json` 의 샘플(가상) 데이터만 올라갑니다.**
> 실 데이터는 로컬에서만 다루며, 공개 배포는 `publish.bat` 로만 명시적으로 실행합니다.
> (실 데이터를 공개하려면 저장소를 **Private + GitHub Pro** 로 전환하는 것을 권장)

---

## 구조

```
docs/                     ← GitHub Pages 가 서비스하는 정적 사이트 (서버 불필요)
  index.html  app.js  analytics.js  style.css   대시보드 (조회단위 일/월/년, 채널별 차트)
  report.html                                    PDF용 리포트
  admin.html  admin.js                           채널 설정 뷰어 + channels.json 다운로드
  lib/chart.umd.min.js                           Chart.js (로컬 벤더)
  data/inflow.json  channels.json  collection_log.json   ← 사이트가 읽는 데이터(현재 샘플)

scripts/                  ← 로컬 전용 (Python). 메일 수집·데이터 적재
  serve.py               로컬 미리보기 서버 (docs/ 를 서비스, data/live/ 우선 노출)
  analytics.py           집계 로직 (analytics.js 와 동일 규칙 — 검증용)
  collect.py             메일 수집 → 파싱 → 검증 → data/live/*.json 적재
  imap_client.py         Gmail IMAP (앱 비밀번호)
  parser.py              RPA 메일 표 파서 + 제목 날짜 추출
  import_history.py / import_xlsx_history.py   과거 데이터 일괄 적재
  store.py               파일 저장소 (원자적 쓰기, 중복·검증, 샘플 자동생성)

data/
  *.sample.json          샘플 데이터 원본
  history_sample.csv     과거 CSV 적재 양식
  live/                  ← 로컬 실 데이터 (git 제외). collect.py 가 여기에 씀

run_collect.bat          매일 07:30 자동 수집 (작업 스케줄러 등록됨)
publish.bat              data/live → docs/data 복사 + git push (공개 배포, 확인 프롬프트)
.env / .env.example      Gmail 앱 비밀번호 등 (git 제외)
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

## 4. 공개 사이트에 배포

```bash
publish.bat        # 확인 프롬프트 → data/live/*.json 를 docs/data/ 로 복사 → git commit + push
```
푸시 후 1~2분이면 GitHub Pages에 반영됩니다.

> 실 데이터를 올리면 **공개 URL에서 누구나 조회 가능**합니다.
> 사내 데이터는 저장소를 Private 로 두고 GitHub Pro(Pages 비공개) 사용을 권장합니다.

---

## 용어

- **총합** = 보장분석 이용건 (대표 지표) = Y + N
- **Y** = 마케팅 동의건 / **N** = 미동의건, 동의율 = Y / 총합
- 조회 단위: 일별(RPA 메일=일별) / 월별·년도별(합산). 과거 월별 데이터는 해당 월 말일 레코드
