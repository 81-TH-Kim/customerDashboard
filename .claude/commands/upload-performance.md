---
description: RPA 실적 메일을 수집해 암호화 후 GitHub Pages 대시보드에 반영합니다
---

# 실적 업로드 (제휴현황 대시보드)

아래 순서를 그대로 따르고, 각 단계 결과를 한국어로 간단히 보고할 것. 지시 없이 다음 단계로
넘어가되, 판단이 필요한 지점(신규 채널 표시명 등)에서는 절대 추측하지 말고 사용자에게 물어보거나
임시 라벨을 남겨둘 것.

$ARGUMENTS 가 있으면 그 값을 `--days` 로 사용, 없으면 7.

## 1. 메일 수집
```bash
python scripts/collect.py --days <N>
```
`[success] <date> 신규 적재` 로 새로 들어온 날짜를 확인한다. "신규 0건" 이면 새 데이터가 없다는
뜻이니 4번은 건너뛰고 "업로드할 신규 실적이 없습니다" 로 보고하고 종료.

## 2. 신규 레코드 정합성 확인
새로 적재된 각 날짜에 대해 `data/live/inflow.json` 을 읽어 해당 레코드의
`sum(channels[].total)` / `sum(channels[].y)` 를 계산하고, 원본 RPA 메일(수집 로그 또는
`data/_schedule.log`)의 총합과 비교한다.

`record["warnings"]` 에 `"채널 마스터에 없는 채널: <CODE>"` 가 있으면:
- `data/live/channels.json` 의 `channels` 배열 끝에 아래 형태로 추가 (sort 는 마지막 값+1):
  ```json
  { "code": "<CODE>", "name": "<CODE>(표시명 확인필요)", "type": "", "use_yn": "Y", "display_yn": "Y", "sort": <N> }
  ```
- 이 채널을 추가했다는 사실과 정확한 count 를 최종 보고에 반드시 포함 (표시명은 나중에
  관리자 화면 "채널 관리" 탭에서 확정하도록 안내).
- 채널을 추가한 직후에는 `DASH_DATA_DIR=<repo>/data/live python -c "..."` 로 `scripts/analytics.py`
  의 `build_view` 를 돌려, 그 날짜의 총합이 메일 수치와 정확히 일치하는지 반드시 재확인한다
  (일치하지 않으면 배포하지 말고 원인을 먼저 보고).

## 3. 발행
- **신규 채널을 하나도 추가하지 않았다면**:
  ```bash
  python scripts/auto_publish.py
  ```
  (git pull → pack → 실제 변경 있을 때만 commit+push 까지 알아서 처리한다.)
- **신규 채널을 추가했다면** (관리자 화면에서 그 사이 다른 채널명을 편집했을 수 있으므로 먼저 원격 최신화):
  ```bash
  git pull --ff-only origin main
  python scripts/manage_users.py pack --channels-from-live
  git add docs/data/bundle.enc
  git commit -m "fix: <date> 데이터 반영 (+ 신규 채널 <CODE>)"
  git push origin main
  ```

## 4. 라이브 확인
`.env` 의 `DASH_CONTENT_KEY` 로 `https://81-th-kim.github.io/customerDashboard/data/bundle.enc` 를
직접 복호화해서 방금 올린 날짜와 총합이 정확히 반영됐는지 확인한다 (CDN 전파에 수 초~수십 초 걸릴
수 있으니 `until curl ... | grep -q <date_or_code>; do sleep 5; done` 형태로 폴링).

## 5. 보고
- 반영된 날짜/총합/동의·미동의
- 신규 채널이 있었다면 코드와 "표시명 확인 필요" 안내
- git 커밋 해시
