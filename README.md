# 팀장 리더십 리플렉션 v4

Vercel에 배포하는 FastAPI + 정적 화면 앱. 명세는 `docs/SPEC.md`, 작업 규칙은 `CLAUDE.md`.

## 로컬 실행 (SQLite)
```
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\uvicorn main:app --reload
```
- http://127.0.0.1:8000/ (사용자), http://127.0.0.1:8000/admin (관리자)
- `DATABASE_URL` 이 없으면 `.local.db`(SQLite)를 만든다
- 테스트: `.venv\Scripts\python -m pytest -q` (약 50초)
  - `tests/test_rules_exhaustive.py`: 17문항 응답 2^17가지 전부를 SPEC 4장을 그대로 옮긴 기준 구현과 비교(판정 기준값 0.25~1.0 포함)
  - `tests/test_content_spec.py`: content.json 의 문항 축·페이지 순서·상황 우선순위·키워드 연결이 SPEC 표와 같은지
  - `tests/test_result_api.py`: 결과 계산 API(설정 기준값, 결과 후 답 변경 재계산, 완료 시 저장, 실천 보기 검증)
  - `tests/test_choice_paths.py`: 응답자 선택 시뮬레이션에서 찾은 오류 경로(결과 후 답 변경, 문장 정리, CSV 수식, 범위 밖 값)
- 응답자 선택 시뮬레이션: `.venv\Scripts\python scripts\simulate_choices.py . 400` — 가상 리더 400명을 무작위 경로로 진행(임시 DB). `== PROBLEMS ==` 가 `(none)` 이면 통과

- 로컬 입장: `ENTRY_KEY` 가 없으면 입장 키는 `dev` → http://127.0.0.1:8000/?k=dev

## 사용자 API (모든 요청에 `X-Entry-Key`, 리더명 이후 `X-Participant-Id`, 리더명 전 `X-Device-Id`)
| 메서드 | 경로 | 용도 |
|---|---|---|
| GET | `/api/content` | 입장 확인 + 문구. 키 없음/틀림 → 403 `entry_denied`(이벤트 기록), 마감 → `closed` |
| POST | `/api/participants` | 리더명 정하기 → 참여자 생성(UUID, 구분 번호 4자리는 서버에만) |
| PUT | `/api/participants/me` | 리더명 다시 고르기(STEP 1 완료 후 409) |
| GET | `/api/me` | 이어 하기 상태(STEP 1·진행 중 점검·완료 점검 수) |
| PUT | `/api/step1` | 기억 여부(`remembered`/`not_remembered`)·키워드(1~3개 또는 `kw_none`)·마지막 화면. `complete` 로 STEP 1 완료(이후 409) |
| POST | `/api/checks` | 구성원 떠올리기 '다음' → 새 점검(상황 목록, `none` 단독). STEP 1 완료 후, 진행 중 점검이 없을 때만 |
| PATCH | `/api/checks/{seq}` | 답(행동 `done`/`not_done`, 부족 신호 `yes`/`no`)·마지막 화면·실천(`recommended`/`other_preset` + `situation:id`/`item:id`, 또는 `custom` + 문장). `complete` 로 완료(이후 409). 결과를 본 뒤 답·상황이 바뀌면 저장된 결과를 지운다(결과 정리에서 다시 계산) |
| GET | `/api/checks/{seq}/result` | 결과 정리 화면에서 호출. `rules.py` 로 판정·결과 1~3·추천 문장 계산 후 저장 |
| DELETE | `/api/checks/{seq}` | '처음부터 다시' — 진행 중인 점검 삭제 |
| POST | `/api/events` | 이벤트 묶음(최대 50개). 정해 둔 이벤트·속성만 저장, 나머지는 버림 |

사용자 화면: `public/index.html` + `public/app.js` + `public/app.css` (빌드 없음, 프레임워크 없음). 문구는 모두 `/api/content`.
결과 PDF: `public/result-pdf.js` — 외부 라이브러리 없이 브라우저에서 생성(캔버스 → JPEG → A4 PDF, 기기 한글 글꼴 사용). 한 쪽에 안 들어가면 최대 86%까지 줄이고, 그래도 넘치면 2쪽. 결과는 서버로 보내지 않는다.

### 이벤트 (SPEC 5.3)
- 화면에서 모아 0.8초마다 `/api/events` 로 보냄. 탭을 숨기거나 떠날 때 남은 것을 keepalive 로 보냄. 연결 오류면 다시 보냄
- 모든 이벤트에 기기 ID(무작위)·세션 ID. 참여자 ID 는 **이벤트가 생긴 때** 리더명이 있었을 때만(안내·입장 단계는 기기 ID만)
- `screen_view`: 화면에 들어올 때마다 `visit`(무작위 ID). 떠날 때 `final:true`, 탭을 숨길 때 그때까지 값으로 `final:false` → **집계는 visit 별 마지막 값**(창을 그냥 닫아도 남음). `visible_ms` 는 보이는 동안만, 입력 없이 설정값(기본 180초)이 지나면 그 뒤는 뺌. 결과 정리(`analyzing`)는 고정 3초 → 머문 시간 집계에서만 제외
- 화면별 이벤트: `leader_name_pick`(유형·보기 순서·바꾼 횟수, 직접 쓴 이름 없음), `review_recall`, `keyword_pick`, `nickname_method`(별명 내용 없음), `answer_changed`, `answer_changed_after_result`, `blocked_next_tap`, `back_nav`, `result_back_to_checklist`, `action_pick`, `action_custom_abandoned`, `action_custom_text`(○○ 처리 + 서버에서 한 번 더 지움), 완료 화면 3칸(`done_button_click`: again/pdf/recommend — 누를 때마다, `result_pdf_saved`, `recommend_*`, `another_member_started`), `visit_from_recommend`, `resume`, `revisit_after_done`, `error`(pdf / share / storage_unavailable / save_retry + 브라우저 종류. User-Agent 원문은 보내지 않음), `entry_denied`(서버에서 기록)

화면 ID: `notice, leader_name, intro, review_1~5, keyword, member, check_1~5, analyzing, result_1~3, action, done`

## 관리자 (`/admin`)
- 로그인: 암호 2종 — `ADMIN_PASSWORD_FULL`(기업문화팀, 전체 권한), `ADMIN_PASSWORD_READONLY`(HR 임원, 읽기 전용). 서명 키 `SESSION_SECRET`
  - 로컬(Vercel 밖)에서 값이 없으면 `dev-full` / `dev-read`. **Vercel 에서 값이 없으면 로그인 불가**
  - 쿠키: HttpOnly·Secure·SameSite=Strict, 8시간. 암호를 바꾸면 기존 로그인은 풀림
  - 로그인 5회 실패 → 10분 잠금. IP 를 저장하지 않으므로 기기별이 아니라 **전체 잠금**
- 읽기 전용은 설정·참여자/데이터 삭제·CSV 내보내기가 화면에서 숨겨지고 서버에서도 403
- 탭: 대시보드 / 문항 분석 / 참여자 / 사용 흐름 / 설정(전체 권한만). 필터: 리더명 유형·기간·차수
- 개인정보: 참여자 탭은 리더명 + 구분 번호 + 진행 상태 + 점검별 4분면 경향만. 체크 응답·직접 쓴 문장은 리더명과 연결하지 않음. 참여자 ID(이어 하기 열쇠)는 관리자에게도 내보내지 않음(삭제는 리더명 + 구분 번호)
- CSV(전체 권한): 참여자 목록 / 점검 응답(리더명·시각 없음, 순서 섞음) / 직접 쓴 문장(작성자 없음) / 화면별 도달·머문 시간. 엑셀용 UTF-8 BOM
- 문구: `content/admin.json`. 집계 규칙: `admin_stats.py`(순수 함수, `tests/test_admin.py`)
- 로컬 미리보기용 가짜 데이터: `.venv\Scripts\python scripts\seed_demo.py` (로컬 주소에서만 실행됨)

## Vercel 구성
- 진입 파일: 루트 `main.py` 의 `app` (Vercel FastAPI 자동 감지 대상: app.py, index.py, server.py, main.py 등)
- 정적 파일: `public/` 은 Vercel CDN이 제공한다. 로컬 uvicorn에서만 `main.py` 가 `/`, `/admin` 을 직접 내려준다
- `vercel.json`: 함수 리전 `icn1`(서울), `cleanUrls`(`/admin` → `admin.html`), 함수 최대 실행 30초
- Python 버전: `.python-version` = 3.12, 의존성: `requirements.txt`
- 로컬 Vercel 환경 확인: Node.js와 Vercel CLI(48.1.8 이상) 설치 후 `vercel dev`
- 환경 변수 이름: `.env.example` (값은 Vercel 프로젝트 설정에만)

## DB 제공사 선택 — Supabase (서울 `ap-northeast-2`)
- **이유: 서울 리전이 있다.** 함수가 서울(`icn1`)에서 돌기 때문에 DB 도 서울이면 요청마다 생기는 왕복 지연이 가장 작다.
  Neon 은 서울 리전이 없고 가장 가까운 곳이 싱가포르(`aws-ap-southeast-1`)라 매 쿼리가 국가 간 왕복이 된다.
  (확인: Neon·Supabase 공식 리전 문서, 2026-11-26)
- Preview(시범)와 Production(본 운영)은 **Supabase 프로젝트를 따로** 만들어 분리한다(SPEC: 시범 응답이 본 데이터에 섞이지 않게).
  Neon 의 Preview 브랜치는 본 운영 브랜치에서 갈라져 나오는 방식이라 이 분리 요구와 맞추기가 덜 단순하다.
- 연결: Vercel Marketplace 의 Supabase 연동은 `POSTGRES_URL` 을 넣는다(`DATABASE_URL` 아님) → 앱은 `DATABASE_URL`, 없으면 `POSTGRES_URL` 을 쓴다.
  Supabase 주소의 `supa=` 같은 값은 드라이버가 모르므로 앱이 지우고, `sslmode=require` 를 붙인다. 서버리스용 Transaction pooler(6543) 주소를 쓴다.
- Marketplace 화면에서 서울 리전이나 환경별 연결을 고를 수 없으면 Supabase 에서 직접 만들고 주소만 환경 변수로 넣는다 → `docs/DEPLOY.md` 1장

배포 순서는 [docs/DEPLOY.md](docs/DEPLOY.md).
