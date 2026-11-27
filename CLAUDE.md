# CLAUDE.md — 팀장 리더십 리플렉션 v4

이 폴더는 GC녹십자 기업문화팀의 '팀장 리더십 리플렉션' 새 앱(v4)을 만드는 작업 폴더다.
**Vercel에 배포하는 FastAPI + 정적 화면 앱**이다. 로그인이 없고 개인정보(이름·사번·이메일·소속)를 받지 않는다.
리더는 첫 화면에서 정한 **리더명**으로만 기록된다.

## 먼저 읽을 것
1. `docs/SPEC.md` — 개발 명세서(범위, 화면 흐름, 결과 규칙, 데이터, 관리자, 배포). 이 문서가 기준이다
2. `content/content.json` — 모든 화면 문구·문항·결과 문구·리더명 보기의 단일 원본
3. `design/screens/*.dc.html` (사용자 21개), `design/admin/*.dc.html` (관리자 4개) — 화면 디자인 기준
4. `design/gc-hr-tokens.json` — 디자인 토큰

`design/` 의 `.dc.html` 은 Claude Design 캔버스용 프로토타입이다. 레이아웃·색·간격·문구의 기준으로만 쓰고,
`<x-dc>`, `<sc-for>`, `<sc-if>`, `{{ }}`, `DCLogic` 같은 캔버스 전용 문법은 앱 코드에 가져오지 않는다.
각 파일 하단 `renderVals()` 안의 `// [개발 명세]`, `// [결정 2026-11-25]` 주석도 명세의 일부다.
주석 중 HeyGC·SSO·조직 경로를 언급하는 옛 결정은 '개발 방향 변경' 주석과 SPEC이 우선한다.

## 만들 구조
```
vercel.json            # regions: ["icn1"] (서울). 함수 최대 실행 시간 등
pyproject.toml         # 또는 requirements.txt — fastapi, sqlalchemy, psycopg[binary], itsdangerous
.python-version        # 3.12
main.py                # FastAPI app: 입장 키 확인, 사용자 API, 관리자 API, /api/content
rules.py               # 결과 규칙(SPEC 4장) — 순수 함수
anonymize.py           # 직접 쓴 문장·리더명 패턴 검사(AI 미사용)
db.py                  # SQLAlchemy. DATABASE_URL 이 없으면 로컬 SQLite
content/content.json
public/index.html      # 사용자 SPA (Vercel CDN이 정적 파일로 제공)
public/admin.html      # 관리자 SPA (데이터는 인증된 /api/admin/* 에서만)
tests/                 # rules.py, anonymize.py, 권한 테스트
.env.example           # 환경 변수 이름만(값 없음)
```
Vercel의 FastAPI 진입 파일 이름 규칙과 `public/` 정적 제공 방식은 작업 시작 시 Vercel 공식 문서(FastAPI on Vercel)로 확인한다.

## 지켜야 할 규칙
- 문구를 코드에 하드코딩하지 말고 `content.json` 에서 읽는다(화면은 `/api/content` 로 받는다)
- AI를 쓰지 않는다. 결과는 SPEC 4장 규칙으로만 만든다
- **개인정보를 받지 않는다.** 이름·사번·이메일·소속 입력란을 만들지 않고, IP 주소·User-Agent 원문을 DB에 저장하지 않는다(기기 구분은 모바일/PC 값만)
- 별명은 서버로 보내지 않는다(브라우저 저장소에만). 직접 쓴 문장 안의 별명은 **브라우저에서 ○○로 바꾼 뒤** 보낸다
- 참여자 ID는 서버가 만든 무작위 UUID. 브라우저 저장소에 보관하고, 모든 사용자 API 요청에 함께 보낸다
- 관리자 API는 리더 개인의 체크 응답·직접 쓴 문장을 리더명과 함께 반환하지 않는다. 리더명과 함께 반환하는 결과는 점검별 4분면 경향뿐
- 관리자 인증은 암호 2종(전체 권한, 읽기 전용) + 서명된 HttpOnly·Secure·SameSite=Strict 쿠키. 읽기 전용은 CSV 내보내기·설정·삭제 API 차단(서버에서 막을 것)
- 비밀값(DB 주소, 입장 키, 관리자 암호, 쿠키 서명 키)은 Vercel 환경 변수로만. 저장소·코드·로그에 남기지 않는다
- 사용자 화면은 모바일 UI 하나, 본문 최대 480px, 바깥 여백 흰색
- 이모지·별명 토큰(`#E3F3EA` 배경, `#00703C` 글자)·리더명 토큰(`#f0f0f0` 배경, 검정 굵게) 외에는 GC녹십자 HR 토큰만 쓴다

## 개발 순서
1. 프로젝트 뼈대 → `uvicorn main:app --reload`(로컬 SQLite)로 실행 확인 → `vercel dev` 로 Vercel 환경 확인
2. 입장 키 확인 + 참여자 생성(리더명) API + 이어 하기
3. 데이터 저장 API(step1, check, event)
4. 사용자 화면 21개
5. `rules.py` + 테스트(17문항 응답 조합 → 4분면·결과 1~3·추천 문장)
6. 완료 화면 3칸: 다른 구성원 점검하기 / 나에게 결과 보내기(PDF + navigator.share, 미지원 시 PDF 저장) / 동료에게 추천하기(링크 + navigator.share, 미지원 시 클립보드 복사)
7. 이벤트 기록(SPEC 5.3)
8. 관리자 로그인 + 4개 탭 + 설정 + CSV
9. Preview 배포로 기업문화팀 시범 운영 → Production 배포

## 배포
- GitHub 저장소를 Vercel 프로젝트에 연결: 작업 브랜치 push = Preview 배포, main 병합 = Production 배포
- DB: Vercel Marketplace의 Postgres(Neon 또는 Supabase) 중 하나를 골라 연결한다(선택은 Claude Code에 맡김, 이유를 README에 기록). 연결하면 `DATABASE_URL` 이 자동으로 들어온다. 리전은 서울에 가장 가까운 곳
- 주소: Vercel 기본 주소(`*.vercel.app`). 회사 도메인은 쓰지 않는다
- 화면 하단에는 `content.json` 의 `ui.credit`(지금 "Culture팀")을 표시한다(버전 표시는 2026-11-27 제거). `APP_VERSION` 은 Git 태그와 맞추고 `/api/health`·`/api/content` 응답의 `version` 으로 확인한다
- Preview와 Production은 DB를 분리한다(시범 응답이 본 데이터에 섞이지 않게)
