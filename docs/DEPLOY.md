# 배포 안내 — 팀장 리더십 리플렉션 v4 (Vercel)

이 문서는 기업문화팀이 GitHub·Vercel·Supabase 화면에서 직접 할 일을 순서대로 적었습니다.
코드 쪽 준비(지역 `icn1`, 보안 헤더, DB 주소 처리, 배포 제외 파일)는 끝나 있습니다.

## 지금 구성 (2026-11-27 연결)

| 항목 | 지금 상태 |
|---|---|
| GitHub 저장소 | `jasonjung0304/leader-reflection-v4` — **공개(public)**. 브랜치 `main`(기본·본 운영), `pilot`(시범) |
| Vercel 프로젝트 | `leader-reflection-v4` — Hobby 팀. Production Branch `main`, 함수 리전 `icn1`(서울) |
| 시범 DB | Supabase `leader-reflection-pilot` — 서울 `icn1`, 무료 요금제, **Preview 환경에만** 연결 |
| 본 운영 DB | Supabase `leader-reflection-prod` — 서울 `icn1`, 무료 요금제, **Production 환경에만** 연결 |
| 시범 주소 | `leader-reflection-v4-git-pilot-<팀>.vercel.app`(정확한 주소: Vercel → Deployments → `pilot` 줄). `/api/health` → `{"ok":true,"version":"4.1.0-pilot.1","db":"postgresql"}` 확인 |
| 시범 환경 변수 | `ENTRY_KEY`·`ADMIN_PASSWORD_FULL`·`ADMIN_PASSWORD_READONLY`·`SESSION_SECRET`(Secret) + `APP_VERSION=4.1.0-pilot.1` — 모두 Preview 만 |
| 본 운영 환경 변수 | DB 주소(`POSTGRES_*`)만 있음. **입장 키·관리자 암호·서명 키는 아직 없음** → 본 운영 주소(`leader-reflection-v4.vercel.app`)는 `/api/health` 가 `db: unavailable`(503)이고 아무도 들어올 수 없다(5장에서 채움) |
| 배포 보호 | Vercel Authentication(기본값) **켜 둠**(2026-11-27 결정) → 시범 주소는 이 Vercel 팀에 로그인한 사람만 열 수 있다(4장 2) |

## 0. 배포 전 확인 (SPEC 10장)

| 확인 | 내용 | 담당 |
|---|---|---|
| Vercel 요금제 | 지금은 **Hobby**(무료)로 시범까지 연결해 두었다. Hobby 플랜의 상업적 사용 제한을 **Vercel 이용 약관(https://vercel.com/legal/terms)** 에서 확인하고, 회사 업무용 본 운영 전에 Pro 등 결제 주체를 정한다. (요금제 안내·공정 사용 페이지에는 이 내용이 적혀 있지 않아 약관 원문 확인이 필요) | 기업문화팀 |
| 사내 정보보안 검토 | 사외 클라우드(Vercel, Supabase)에 교육 응답·직접 쓴 문장이 저장된다. 개인정보(이름·사번·이메일·소속·IP)는 앱 DB에 없음. Vercel 플랫폼 요청 로그(IP 포함)는 앱 DB와 별개로 Vercel 에 남는다. **소스 코드 저장소가 공개**라 화면 문구·결과 규칙도 누구나 볼 수 있다(비밀값은 저장소에 없음) | 정보보안 |
| 사내 접속 | 사내 메신저·메일 앱 안 브라우저, 사내 프록시에서 `*.vercel.app` 접속이 막히지 않는지 | IT |
| DB 백업·휴면 | Supabase 무료 요금제의 백업 방식(주기·보관 기간)과, 오래 쓰지 않으면 프로젝트가 일시 중지되는지를 Supabase 문서에서 확인. 본 운영은 유료 요금제 검토 | 기업문화팀 |

## 1. DB — Supabase, 서울 리전 2개 (완료)

DB 제공사로 **Supabase** 를 쓴다(이유는 README 'DB 제공사 선택').
**시범 운영(Preview)** 과 **본 운영(Production)** 을 서로 다른 DB 로 만들었다(시범 응답이 본 데이터에 섞이지 않게).

| DB 이름 | 연결한 Vercel 환경 | 리전 |
|---|---|---|
| leader-reflection-pilot | Preview | Seoul, South Korea `icn1` (Supabase `ap-northeast-2`) |
| leader-reflection-prod | Production | Seoul, South Korea `icn1` (Supabase `ap-northeast-2`) |

**만든 방법: Vercel Marketplace**(Vercel 프로젝트 → Storage → Create Database → Supabase). 다시 만들거나 다음 차수에 새로 만들 때 기본값 3가지를 꼭 바꾼다.

| 단계 | 기본값 | 바꿀 값 | 이유 |
|---|---|---|---|
| Configuration and Plan → Primary Region | Washington, D.C., USA (East) `iad1` | **Seoul, South Korea (Northeast) `icn1`** | 앱 함수가 서울에 있어 DB 가 미국이면 쿼리마다 태평양 왕복(2026-11-27 처음 만든 DB 가 미국이라 지우고 다시 만듦) |
| Connect a Project → Environments | Production, Preview | **시범 DB 는 Preview 만, 본 운영 DB 는 Production 만** | 두 환경이 같은 DB 를 쓰지 않게 |
| Connect a Project → Custom Prefix | (비어 있음, 안내 글자 `STORAGE`) | **`POSTGRES`** | 앱은 `DATABASE_URL`, 없으면 `POSTGRES_URL` 을 읽는다. 접두어가 다르면 주소를 못 찾는다 |

- 처음 한 번은 Marketplace 약관 동의(Accept and Create)가 필요하다. Vercel 이 계정 이메일 등을 Supabase 와 공유한다.
- Supabase 무료 요금제는 만들 수 있는 프로젝트 수에 제한이 있다(지금 2개 사용 중, 정확한 한도는 Supabase 요금제 문서에서 확인). 더 만들려면 안 쓰는 DB 를 지우거나 유료로 바꾼다.
- 연결하면 `POSTGRES_URL` 등 `POSTGRES_*` 변수가 그 환경에 자동으로 들어온다. 주소에 붙는 `supa=...` 같은 값은 앱이 지우고, 암호화 연결(`sslmode=require`)을 붙인다.
- 테이블은 앱이 처음 요청을 받을 때 자동으로 만든다(따로 SQL 을 실행할 필요 없음).
- (대안) Marketplace 에서 서울 리전이나 환경별 연결이 안 되면: supabase.com 에서 직접 만들고(리전 Seoul), Connect → **Transaction pooler**(포트 6543) 주소를 Vercel 환경 변수 `DATABASE_URL` 에 환경별로 넣는다.

## 2. GitHub 저장소와 Vercel 프로젝트 (완료)

1. GitHub 저장소: **공개(public)** 로 만들었다(2026-11-27 결정). 비밀값은 저장소에 없고, 코드 안의 개발용 관리자 암호(`dev-full`/`dev-read`)는 Vercel 에서는 쓰이지 않는다(값이 없으면 로그인 불가).
   - 처음 올릴 때 git 의 GitHub 로그인(브라우저 승인)이 git 에 전달되지 않으면 **기기 코드 방식**으로 다시 올린다:
     ```powershell
     $env:GCM_GITHUB_AUTHMODES = 'device'; $env:GCM_GUI_PROMPT = 'false'; git push -u origin main; git push -u origin pilot
     ```
     터미널에 나온 코드를 https://github.com/login/device 에 넣고 승인. 로그인 정보는 Windows 자격 증명에 저장돼 다음부터는 묻지 않는다.
2. Vercel → Add New → Project → GitHub 선택 → **Install**(Vercel GitHub 앱 설치, 'Only select repositories' 로 이 저장소만) → 저장소 Import.
   - Application Preset: FastAPI 로 자동 인식(진입 파일 `main.py` 의 `app`). Root Directory `./`
   - 함수 리전 `icn1`(서울)은 `vercel.json` 에 이미 들어 있다
   - Production Branch: 저장소 기본 브랜치 `main`. **빈 저장소에 `main` 을 먼저 올려야** 기본 브랜치가 `main` 이 된다(`pilot` 만 먼저 올리면 `pilot` 이 본 운영으로 잡힘)
   - Vercel 이 2단계 인증 설정을 권하면 설정한다(이 계정에 DB 주소·관리자 암호가 들어 있음)
3. 처음 Import 할 때 만들어지는 Production 배포는 환경 변수를 넣기 전이라 동작하지 않는 것이 정상이다.

## 3. 환경 변수 (Vercel → 프로젝트 → Settings → Environment Variables)

**Production 과 Preview 에 서로 다른 값**을 넣는다. 값은 이 저장소·메신저·메일·화면 캡처에 남기지 않는다.

| 이름 | Production | Preview(시범) | 비고 |
|---|---|---|---|
| `POSTGRES_URL` 등 `POSTGRES_*` | prod DB(자동) ✅ | pilot DB(자동) ✅ | 1장 Marketplace 연결이 넣는다. 직접 넣을 때는 `DATABASE_URL` |
| `ENTRY_KEY` | **아직** | ✅ | 영문·숫자·`-`·`_` 8~64자. 관리자 설정에서 바꾸면 그 값이 우선 |
| `ADMIN_PASSWORD_FULL` | **아직** | ✅ | 기업문화팀 암호. 길고 추측하기 어렵게 |
| `ADMIN_PASSWORD_READONLY` | **아직** | ✅ | HR 임원 암호. 전체 권한 암호와 다르게 |
| `SESSION_SECRET` | **아직** | ✅ | 무작위 32자 이상. 사람이 입력할 일 없음 |
| `APP_VERSION` | **아직**(예: `4.1.0`) | ✅ `4.1.0-pilot.1` | 화면 하단 표시, Git 태그와 맞춤. 비우면 커밋 앞 7자리 |

넣는 방법(4개를 한 번에):

1. 무작위 값 만들기 — 이 폴더에서 실행(`python` 대신 프로젝트 가상환경의 파이썬). 결과를 남들 앞에서 띄우거나 캡처하지 않는다:
   ```powershell
   .venv\Scripts\python.exe -c "import secrets; [print(n + '=' + secrets.token_urlsafe(24)) for n in ['ENTRY_KEY', 'ADMIN_PASSWORD_FULL', 'ADMIN_PASSWORD_READONLY', 'SESSION_SECRET']]"
   ```
2. Add Environment Variable → 나온 4줄(`이름=값`)을 **Key 칸에 그대로 붙여 넣기**(4개 줄로 자동으로 나뉨). Key 칸 하나에 이름 여러 개를 쓰지 않는다
3. Type: **Secret**(저장 뒤 다시 볼 수 없음) → Environments: **넣을 환경 하나만**(기본값이 Production 이니 시범이면 Production 을 끄고 Preview 만) → Save
4. `ENTRY_KEY`·관리자 암호 2개는 저장하기 전에 사내 암호 보관함 등에 따로 적어 둔다(참여자 링크·관리자 로그인에 씀)

환경 변수를 바꾼 뒤에는 **다시 배포(Redeploy)** 해야 반영된다.

## 4. 시범 운영 (Preview)

1. 시범 배포 만들기: `pilot` 브랜치에 push 하면 Preview 배포가 자동으로 만들어진다.
   새 커밋 없이 만들려면 Vercel → Deployments → 오른쪽 위 ⋯ → **Create Deployment** → `pilot` 입력 → Create Preview Deployment.
   브랜치 고정 주소 `leader-reflection-v4-git-pilot-<팀>.vercel.app` 를 쓴다(배포마다 바뀌는 주소 말고).
2. 배포 보호(Vercel Authentication): **켜 둠**(2026-11-27 결정). 시범 주소를 열면 Vercel 로그인을 먼저 요구하고, 이 Vercel 팀 구성원만 들어올 수 있다.
   Vercel 계정이 없는 사람이 시범에 참여하게 하려면 둘 중 하나:
   - Settings → Deployment Protection → Vercel Authentication 끄기(앱은 입장 키 없이는 안내 문구만, 관리자는 암호가 있어야 열림)
   - 그 사람을 Vercel 팀에 초대(Hobby 는 팀원 추가가 제한 → Pro 필요할 수 있음)
3. 시범 참여 링크: `https://leader-reflection-v4-git-pilot-<팀>.vercel.app/?k=<시범 ENTRY_KEY>`, 관리자: 같은 주소 `/admin`
4. 확인 목록
   - [x] `/api/health` → `{"ok": true, "db": "postgresql"}` (2026-11-27)
   - [x] 키 없이 열면 '안내 메일이나 메신저의 링크로 들어와 주세요' (2026-11-27)
   - [x] `/admin` 로그인 화면이 열림 (2026-11-27)
   - [ ] `/?k=<시범 입장 키>` 로 처음부터 완료까지 진행, 중간에 닫고 다시 열면 이어 하기
   - [ ] `/admin` 두 암호로 로그인, 읽기 전용은 설정·CSV 가 보이지 않음
   - [ ] 관리자 설정: 차수명, 마감일, 대상 인원
   - [ ] 기기별: iOS Safari, Android Chrome, PC Chrome·Edge, **사내 메신저 안 브라우저, 메일 앱 안 브라우저** —
         PDF 다운받기(앱 안 브라우저에서 파일이 받아지는지), 동료에게 추천하기(링크가 복사되는지), 꽃가루·카운트다운
         (배포 보호가 켜져 있으면 앱 안 브라우저에서도 Vercel 로그인이 필요하다)
   - [ ] 앱 안 브라우저로 열면 안내 화면에 '메신저·메일 앱 안에서 열었어요…' 안내가, 완료 화면에 '캡처해 두세요' 한 줄이 나오는지.
         나오지 않는 앱이 있으면 그 앱의 User-Agent 를 알려 주세요(`public/app.js` 의 `browserFamily` 에 추가). 관리자 4.5 기기별 표 아래 '앱 안 브라우저로 연 기기' 수가 늘었는지
   - [ ] 앱 안 브라우저에서 PDF 가 받아지지 않으면: 안내 메일·메신저 문구(아래 4-1)의 '다른 브라우저로 열기'를 꼭 넣는다
5. 시범 운영이 끝나면 관리자 '사용 흐름' 탭의 소요 시간 중앙값으로 안내 화면 '약 n분'을 정한다(본 운영 첫 주에 다시 확인).
6. 시범 응답으로 설정 → '판정 기준값 미리보기'를 보고 본 운영의 판정 기준값을 정한다(한 칸이 70% 이상이면 '쏠림'). 바꾸면 그 뒤 결과부터 적용되므로 본 운영 시작 전에 정한다.

### 4-1. 안내 메일·메신저 문구에 넣을 것 (초안, 2026-11-27)

앱 안 브라우저 제약, 브라우저 저장소 만료, 다른 기기 중복 시작을 줄이기 위한 문장이다. 기업문화팀이 다듬어 쓴다.

> - 링크는 **Chrome·Safari 같은 브라우저**에서 열어 주세요. 메신저·메일 앱 안에서 열리면 오른쪽 위 메뉴의 ‘다른 브라우저로 열기’를 눌러 주세요(앱 안에서는 결과 PDF가 저장되지 않을 수 있어요).
> - 약 n분이면 끝나요. **되도록 한 번에** 끝내 주세요. 중간에 나가면 **처음 연 기기·브라우저**에서만 이어 할 수 있고, 며칠 비워 두면 이어 하기가 끊길 수 있어요.
> - 마지막 화면에서 ‘PDF 다운받기’로 결과를 받아 두면 다음 면담 전에 다시 볼 수 있어요.
> - 이 링크는 대상 팀장님께만 보내 드렸어요. 동료 팀장님께는 마지막 화면의 ‘동료에게 추천하기’를 써 주세요.

## 5. 본 운영 (Production)

1. 요금제: Vercel·Supabase 를 회사 업무용으로 쓸 결제 주체를 정한다(0장).
2. **Production 환경 변수 채우기**(3장 방법, Environments 는 Production 만): 시범과 **다른** `ENTRY_KEY`, `ADMIN_PASSWORD_FULL`, `ADMIN_PASSWORD_READONLY`, `SESSION_SECRET`, 그리고 `APP_VERSION`(예: `4.1.0`).
3. `pilot` → `main` 병합하면 Production 배포가 만들어진다(환경 변수만 바꾼 경우는 Deployments → `main` 최신 배포 → Redeploy).
   `https://leader-reflection-v4.vercel.app/api/health` 가 `{"ok":true,...,"db":"postgresql"}` 인지 확인.
4. Git 태그를 `APP_VERSION` 과 맞춘다(예: `v4.1.0`).
5. 관리자(`/admin`) 설정: 차수명, 마감일, 대상 인원 170, 안내 화면 '약 n분', 판정 기준값(4장 6).
6. 안내 링크: `https://leader-reflection-v4.vercel.app/?k=<본 운영 입장 키>` — 메일·메신저 모두 같은 링크.
   관리자 설정 → 입장 키 화면에 지금 쓰는 링크가 그대로 표시된다.
   (Vercel Authentication 기본값은 Preview 만 보호하므로 본 운영 주소는 Vercel 로그인 없이 열린다. Settings → Deployment Protection 에서 확인)

## 6. 운영 중

- 입장 키가 밖으로 퍼진 것 같으면: 관리자 설정 → 입장 키 변경 → 새 링크 재발송(예전 링크·추천 링크는 막힘).
  신호: 대시보드 1.1 의 '등록한 리더가 대상 인원보다 많아요' 경고, 4.6 '추천 링크 유입' 기기 수가 대상 인원에 비해 크게 늚
- 시험 응답·같은 사람의 중복 시작: 관리자 설정 → 참여자 삭제(리더명 + 4자리 번호). 참여자 탭(3.1)에서 직접 지은 리더명이 겹치면 '같은 이름 n명' 표시
- 직접 쓴 문장에 구성원 실명이 보이면: 문항 분석 2.8 → 문장 옆 '삭제'(전체 권한)
- 앱 안 브라우저 비율(4.5)이나 기술 오류(4.13)가 늘면: 안내 재발송 때 4-1 의 '다른 브라우저로 열기' 문장을 앞에 둔다
- 다음 차수: 관리자 설정에서 차수 코드를 바꾸면 집계가 새로 시작된다(이전 차수는 상단 차수 선택으로 열람)
- 관리자 로그인 5번 실패 시 10분 동안 **모든 관리자**가 잠긴다(IP 를 저장하지 않으므로 전체 잠금)
- 관리자 암호·서명 키가 새어 나간 것 같으면: Vercel 환경 변수에서 새 값으로 바꾸고 Redeploy(서명 키를 바꾸면 로그인한 관리자는 모두 다시 로그인)
