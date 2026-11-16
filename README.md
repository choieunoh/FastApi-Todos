# FastAPI To-Do List

FastAPI와 바닐라 JS로 만든 To-Do 웹 앱입니다. 할 일과 작업 템플릿은 JSON 파일에 저장합니다.
**8.0.0 "Account Management & UI/UX Improvement"** — 브랜치 `feature/v8.0.0`(미커밋·미병합, 배포 전)입니다. 화면 표시는 `Version 8.0.0 · 할 일·템플릿은 로그인한 모든 사용자가 함께 씁니다`입니다. 변경 내용은 [RELEASE_NOTES.md](RELEASE_NOTES.md), 명세·결정·완료 기준은 [V8_SPEC.md](V8_SPEC.md)와 [CLAUDE.md](CLAUDE.md)에 있습니다.

> **⚠ 공유 데이터 (8.0.0)**: 할 일·템플릿 API 15개는 이제 **로그인해야** 쓸 수 있고(익명 401), 쓰기 11개는 CSRF 토큰이 필요합니다(403). 하지만 데이터는 여전히 **JSON 파일 하나를 로그인한 모든 사용자가 함께 씁니다** — 다른 사용자가 만든 할 일을 보고 고치고 지우고 되살릴 수 있습니다. **사용자별 데이터 분리는 하지 않았습니다**(후속 과제 "Multi-User Data Migration", 버전 미정). 회원 탈퇴는 계정·로그인 세션만 지우고 **공유 JSON 데이터는 지우지 않습니다.** HTTPS 없이 배포하면 비밀번호와 세션 쿠키가 평문으로 오갑니다.

(7.0.0 당시) 7.0.0 "PostgreSQL Foundation & Authentication"은 로컬 개발·시연 전용이었고, 데이터 API 15개가 인증 없이 열려 있었습니다(로그인은 데이터를 보호하지 않음). 그 기록은 아래 "7.0.0 진행 상황"과 릴리스 노트에 남아 있습니다. 실제로는 PR #3(`7cf68bb`)으로 main에 병합되어 과제 스택에 배포되었습니다(`CLAUDE.md` "Current Version").

(6.0.0 당시) 6.0.0은 릴리스 후보였습니다(화면 표시 `Version 6.0.0`, 배포 전). 그 내용은 아래 4.0.0~6.0.0 표와 릴리스 노트에 그대로 남아 있습니다.

## 기능 (4.0.0)
- 할 일 추가·수정·삭제, 완료 체크
- 제목·설명 검색, 상태 필터(전체/진행 중/완료), 태그 필터
- 우선순위·마감일, 기본순/우선순위순/마감일순 정렬
- 태그 칩, 인라인 수정 폼, 생성/완료 날짜, D-day, 기한 초과 표시
- 진행률 통계(전체/완료/진행 중/기한 초과/완료율)

## 5.0.0 진행 상황
| 단계 | 기능 | 상태 |
|---|---|---|
| 0 | 기반 정비 (명세, 의존성 분리, API·UI 테스트) | 완료 |
| 1 | 즐겨찾기 (☆/★ 토글, `★ 즐겨찾기만` 필터) | 완료 |
| 2 | 삭제 실행 취소 (삭제 후 5초 토스트, 서버는 30초·최대 100건 보관) | 완료 |
| 3 | 일괄 작업 (선택 체크박스, 보이는 항목 전체 선택, 일괄 완료·미완료·삭제) | 완료 |
| 4 | 하위 할 일 (펼치기/접기, 추가·수정·완료·삭제(확인창), 진행률 `1/2 (50%)`) | 완료 |
| 5 | 반복 일정 (매일·매주·매월, 완료하면 다음 일정 1회 생성, `🔁` 배지) | 완료 |
| 6 | 작업 템플릿 (현재 폼을 템플릿으로 저장, 불러오기로 추가 폼 채우기, 인라인 수정·삭제) | 완료 |
| 7 | 데이터 내보내기 (CSV·JSON 다운로드, 전체 목록) | 완료 |
| 8 | 캘린더 보기 (월 그리드, 이전·다음·오늘, 날짜별 항목, 필터 적용) | 완료 |

## 6.0.0 진행 상황
날짜 기준(D-day, 기한 초과, 캘린더의 오늘, 생성/완료일, 통계)은 브라우저 시간대와 상관없이 **Asia/Seoul**입니다. 시각은 계속 UTC로 저장합니다.

| 단계 | 기능 | 상태 |
|---|---|---|
| 1 | 통계 대시보드 ([통계] 보기: 완료율 소수 1자리, 최근 7일/30일 일별 완료, 우선순위별 미완료, 마감일 준수율, 표 요약) | 완료 |
| 2 | 2열 칸반 보드 ([칸반] 보기: 진행 중/완료, 드래그 앤 드롭과 키보드·터치용 이동 버튼, 실패하면 원래 열로 되돌림. 새 API 없이 기존 `PUT /todos/{id}` 사용) | 완료 |
| 3 | 마감일 알림 센터 (제목 아래 🔔: 미완료 할 일의 내일 마감·오늘 마감·기한 초과, 읽지 않은 수 배지, 누르면 그 할 일로 이동, 자정·탭 복귀 때 다시 계산. 읽음 표시는 이 브라우저 localStorage에만 저장 — 서버·새 API 없음) | 완료 |
| 4 | 라이트/다크/시스템 테마(제목 옆 [테마], 기본 시스템, 이 브라우저 localStorage에 저장·다른 탭에도 반영), 375px에서 가로 스크롤 없음(목록 항목·칸반 열 세로 배치), 키보드 포커스 표시·색 외 상태 표시(⚠·취소선)·성공 메시지(`role="status"`), 모든 화면 텍스트 명도 대비 4.5:1 이상 자동 검사 | 완료 |
| 5 | 전체 테스트(332 passed), 런타임 의존성만으로 실행 확인, 릴리스 노트. **Docker 빌드·컨테이너 검증은 미완료** — 아래 "6.0.0 배포 전 확인" | 진행 중 |

## 7.0.0 진행 상황 (`feature/v7.0.0`, 로컬 개발·시연 전용)
2026-11-09에 범위를 조정했습니다. 7.0.0은 PostgreSQL 기반과 계정 인증까지이고, 사용자별 데이터는 8.0.0으로 옮겼습니다.

| 범위 | 내용 | 상태 |
|---|---|---|
| Phase 1 | PostgreSQL 기반(로컬 PostgreSQL 18.6), SQLAlchemy 2.x ORM, Alembic `0001` | 완료 |
| Phase 2 | 회원가입·로그인·로그아웃(Argon2id, HttpOnly 쿠키 서버 세션, 14일 고정 만료), 로그인·가입 시도 제한(PostgreSQL 기록, 여러 워커 공유), 접힌 로그인·가입 패널과 헤더 사용자명·[로그아웃], 앱 시작 가드, Alembic `0002` | 완료 — 실제 Docker·프록시 환경의 클라이언트 IP 처리(R-12)는 미검증 |
| 3-0 | Phase 3 설계 결정·문서 | 완료 |
| 3-1 | 요청 단위 DB Session 구조, 사용자 데이터 DB 헬퍼(소유자 조건 조회·쓰기, soft delete 등) | 완료 — **데이터 경로에는 연결하지 않음** |
| 3-2 | PostgreSQL 인증 UI 서버에서 실제 로그인 세션으로 도는 UI 테스트 기반(`tests/pg_harness.py`) | 완료(미커밋) |
| 3-3~3-6, Phase 4~6 | UI 로그인 게이트, 데이터 API 15개 인증·CSRF·사용자별 격리, 할 일·템플릿의 PostgreSQL 전환, JSON → PostgreSQL 이전, 운영 전환·공개 준비 | **8.0.0 예정(미구현)** |

- **PostgreSQL에 실제로 저장하는 것**: 계정(`users`)·로그인 세션(`sessions`)·시도 제한 기록(`auth_failures`)뿐입니다.
- **스키마에만 있는 표**: `todos`·`subtasks`·`task_templates`·`data_migrations`는 8.0.0 이전·전환용으로 만들어 두었을 뿐, 지금 앱은 이 표를 읽거나 쓰지 않습니다. 할 일·템플릿은 위와 같이 JSON 파일입니다.
- 알림 읽음 키·테마 키는 v6 그대로입니다(브라우저 localStorage).

## 8.0.0 — 계정 관리와 화면 정리 (`feature/v8.0.0`)
| 단계 | 내용 | 상태 |
|---|---|---|
| 1 | 계정 API(`/auth/me` 가입일, `POST /auth/password`, `POST /auth/account/delete`), 데이터 API 15개 로그인 필수·쓰기 11개 CSRF | 완료 — PostgreSQL 97/97 |
| 2 | 화면 로그인 게이트, 계정 메뉴(내 정보·비밀번호 변경·회원 탈퇴), 공유 데이터 안내, CSS 정리(버튼 3단계·로그인 카드) | 완료 — PostgreSQL UI 125/125 |
| 3 | `Version 8.0.0`, 통합 테스트·JMeter·Jenkins 로그인 절차, 보안 회귀 테스트, 문서 | 진행 중 — 로컬 검증 완료(PostgreSQL 보안 회귀 98/98·UI 125/125, 로컬 통합 9/9, 로컬 JMeter 재검증 통과), Jenkins 사본(C안) 정적 검증 완료. **실제 Jenkins 빌드는 승인 대기** |

**계정 기능**
- 로그인하지 않으면 로그인·가입 카드만 보이고, 할 일 화면과 데이터 요청은 없습니다. 세션이 만료되거나 다른 기기에서 비밀번호를 바꾸면 다음 요청에서 로그인 화면으로 돌아갑니다.
- 헤더의 [👤 사용자명] 버튼이 계정 패널을 엽니다.
  - **내 정보**: 아이디, 가입일(한국 날짜).
  - **비밀번호 변경**: 현재 비밀번호 + 새 비밀번호(8~128자, 현재와 달라야 함) + 확인. 성공하면 **모든 기기의 로그인을 해제**하고 이 화면에는 새 세션을 줍니다. 현재 비밀번호를 틀리면 400이고, 틀린 횟수는 로그인 실패와 같은 시도 제한(429)에 셉니다.
  - **회원 탈퇴**: 비밀번호 확인 + 확인 창. 계정과 모든 로그인 세션을 지웁니다. 할 일·템플릿은 공유 데이터라 지우지 않습니다.

**데이터 API 인증 정책**
- 데이터 경로 15개(아래 API 표)는 세션 쿠키가 없거나 만료되면 401입니다. GET이 아닌 11개는 `X-CSRF-Token` 헤더가 세션 값과 같아야 하고, 없거나 다르면 403입니다. 내보내기 링크도 로그인해야 받을 수 있습니다.
- 경로·메서드·성공 상태 코드·응답 형식은 7.0.0과 같습니다. 서버의 데이터 저장(JSON)도 같습니다.
- 인증 우회 설정(환경변수·헤더·IP 예외)은 없습니다. 테스트·부하 측정도 테스트 계정으로 로그인합니다.


> **7.0.0 주의**: 아래 "Docker"·"로컬" 명령과 [배포와 데이터 관리](#배포와-데이터-관리)는 **6.0.0(JSON) 기준**이며, 7.0.0 실행 방법이 아닙니다. 7.0.0은 운영에 배포하지 않습니다.
> - 7.0.0 앱은 시작할 때 `APP_ENV`(`development`·`test`·`production`)와 시도 제한 비밀키 `AUTH_FAILURE_KEY`를 확인합니다(`V7_SPEC.md` §28.1). 코드를 읽어 보면 `APP_ENV`가 `development`·`test`로 명시되지 않고 `AUTH_FAILURE_KEY`도 없으면 시작을 거부하므로, 지금의 compose 설정이나 `uvicorn main:app --reload`만으로는 시작되지 않을 것으로 판단합니다. **코드 분석에 따른 판단이며 실행해서 확인하지는 않았습니다.**
> - 인증 경로(`/auth/*`)는 `DATABASE_URL`의 PostgreSQL이 필요합니다. 할 일·템플릿 경로는 JSON 파일만 씁니다.
> - 7.0.0 로컬 시연 실행 명령은 아직 이 저장소에서 확인하지 않아 여기에 적지 않습니다. 개발·테스트 DB 접속 형식은 [`.env.dev.example`](.env.dev.example)을 참고하세요(비밀번호는 넣지 않고 `pgpass.conf`에 둡니다).
> - 유일한 예외는 아래 [7.0.0 학교 서버 제한 시연](#700-학교-서버-제한-시연-ssh-터널-전용)입니다. 이 구성도 아직 서버에서 실행해 확인하지 않았습니다.

### 7.0.0 학교 서버 제한 시연 (SSH 터널 전용)
**예외 범위**: 7.0.0은 운영에 배포하지 않습니다. 다만 학교 개인 서버에서 **SSH 터널을 통한 제한적 시연**만 예외로 둡니다(`V7_PLAN.md` §P13 "학교 서버 제한 시연", R-37).

**외부 공개 금지 조건** — 하나라도 어기면 시연하지 않습니다.
- 앱은 서버의 `127.0.0.1:8007`에만 바인딩합니다. `0.0.0.0`·외부 IP·다른 포트로 바꾸지 않고, 리버스 프록시·방화벽 개방·포트 포워딩으로 외부에 노출하지 않습니다. 데이터 API 15개에 인증이 없기 때문입니다(위 "보안 한계").
- 브라우저는 SSH 터널(예: MobaXterm 로컬 포워딩 `localhost:8007 → 127.0.0.1:8007`)로만 접속합니다.
- PostgreSQL은 호스트 포트를 공개하지 않습니다(Compose 내부 네트워크만).
- 운영 v6.0.0(`docker-compose.yml`, 프로젝트 `fastapi_todos`, 컨테이너 `FastApi-app`, 포트 5001, 볼륨 `fastapi_todos_fastapi-data`)과 서버의 다른 프로젝트(PostgreSQL 5432~5434 등)는 쓰지도 바꾸지도 않습니다.
- 시연 데이터는 합성 값만 씁니다. 실제 개인정보·운영 데이터를 넣지 않습니다.

**구성** — [`docker-compose.v7-demo.yml`](docker-compose.v7-demo.yml), 프로젝트 `fastapi-v7-demo`
| 서비스 | 내용 |
|---|---|
| `db` | `postgres:18.6-trixie`, 볼륨 `fastapi_v7_demo_pg-data`, 호스트 포트 없음. 첫 기동 때 비슈퍼유저 `todo_migrator`(스키마 객체 소유)·`todo_app`(표 읽기·쓰기만)을 만든다 |
| `migrate` | 일회성 단계(`todo_migrator`): `alembic upgrade head` → `todo_app`의 `alembic_version` 권한 회수 → 권한 검증(업무 표 7개·시퀀스 권한 유지 확인). `up`에 포함되지 않고 앱 실행 전에 따로 실행한다. **실패하면(종료 코드 0이 아님) 앱을 띄우지 않는다** |
| `app` | v7 이미지, `127.0.0.1:8007:8000`, `todo_app`으로 접속, 할 일·템플릿은 새 볼륨 `fastapi_v7_demo_app-data`(빈 목록에서 시작) |

- 네트워크는 `fastapi_v7_demo_backend`(내부 전용)와 `fastapi_v7_demo_frontend`(앱 포트 공개용)입니다.
- 비밀번호·키는 저장소에 넣지 않고 서버의 시연 폴더 `.env`(권한 600, Git 제외)에 둡니다. 필요한 변수와 실행 순서는 compose 파일 머리 주석에 있습니다. `docker compose config`는 `.env` 값을 채워 출력하므로 `--no-interpolate`로만 확인합니다.
- 앱 설정은 `APP_ENV=development`, `COOKIE_SECURE=false`(터널이 HTTP라서), 명시적 `AUTH_FAILURE_KEY`(공개 대체 키 사용 안 함), 워커 1개, `--no-proxy-headers`입니다.
- 중단은 `docker compose -f docker-compose.v7-demo.yml stop`(데이터 유지)입니다. `down -v`·`docker volume prune`은 쓰지 않습니다. 볼륨 삭제는 별도 승인 후 이름을 지정해서만 합니다.
- **검증 상태**: 이 구성과 Dockerfile 수정은 아직 이미지 빌드·실행·마이그레이션으로 확인하지 않았습니다. 이미지는 Python 3.13이고 로컬 테스트는 Python 3.14 기준입니다(`V7_PLAN.md` R-20). 시도 제한은 모든 요청이 Docker 게이트웨이 IP 하나로 보여 사실상 전역 제한이 됩니다(R-12).

### Docker
> (2026-11 과제 적용 뒤) `docker-compose.yml`의 `fastapi-app`은 과제용 5002 스택(`fastapi-jmeter-app`)입니다 — 아래 [Jenkins + JMeter 과제 배포 (5002)](#jenkins--jmeter-과제-배포-5002). 이 절의 아래 내용(5001, `FastApi-app`)은 그 이전 compose 파일 기준의 기록입니다.

```bash
docker compose up --build fastapi-app
```
http://localhost:5001 에서 열립니다 (컨테이너 이름 `FastApi-app`, 내부 포트 8000).

할 일과 작업 템플릿은 Docker 볼륨 `fastapi_todos_fastapi-data`(컨테이너 안 `/app/data`)에 저장되므로 컨테이너를 다시 만들어도 유지됩니다. 백업·복구·재배포 절차는 아래 [배포와 데이터 관리](#배포와-데이터-관리)를 보세요.

### 로컬
```bash
cd fastapi-app
pip install -r requirements.txt
uvicorn main:app --reload
```
http://localhost:8000 에서 열립니다. 데이터 파일 위치는 환경변수 `TODO_FILE`로 바꿀 수 있습니다 (기본값 `fastapi-app/todo.json`). 작업 템플릿은 `TEMPLATE_FILE`(기본값 `fastapi-app/task_templates.json`)에 저장되며, 처음 템플릿을 저장할 때 만들어집니다. 이 파일은 사용자 데이터라 `.gitignore`에 들어 있어 Git에 커밋되지 않습니다.

## API
> (8.0.0) 아래 데이터 경로는 **로그인 필수**(세션 쿠키, 없으면 401)이고, GET이 아닌 요청은 `X-CSRF-Token` 헤더도 필요합니다(403). 데이터는 로그인한 모든 사용자가 함께 씁니다.

| 메서드 | 경로 | 동작 |
|---|---|---|
| GET | `/todos` | 전체 목록 |
| POST | `/todos` | 생성 (201) |
| PUT | `/todos/{id}` | 전체 수정 (없으면 404). 즐겨찾기 등록·해제도 `favorite` 필드로 합니다 |
| DELETE | `/todos/{id}` | 삭제 (204, 없으면 404) |
| POST | `/todos/bulk` | `{"ids": [1, 2], "action": "complete" \| "uncomplete" \| "delete"}` — 1~100건 일괄 처리 (200, `{"updated": [...], "deleted": [...]}`). 없는 id가 하나라도 있으면 404이고 아무것도 바꾸지 않음 |
| POST | `/todos/restore` | `{"ids": [1, 2]}` (1~100건) — 30초 안에 지운 항목을 원래 id·생성 시각·자리로 복구 (200). 복구할 수 없는 id가 하나라도 있으면 404이고 아무것도 복구하지 않음 |
| POST | `/todos/{id}/subtasks` | `{"title": "..."}` — 하위 할 일 추가 (201, 갱신된 할 일). 할 일당 20개까지 |
| PUT | `/todos/{id}/subtasks/{sid}` | `{"title": "...", "completed": true}` — 둘 다 필수 (200, 갱신된 할 일) |
| DELETE | `/todos/{id}/subtasks/{sid}` | 하위 할 일 삭제 (204). 없는 할 일·하위 할 일은 404 |
| GET | `/todos/export?format=json` | 전체 목록을 JSON 파일로 다운로드 (`todos-YYYYMMDD.json`, 날짜는 한국 시간) |
| GET | `/todos/export?format=csv` | 전체 목록을 CSV 파일로 다운로드 (`todos-YYYYMMDD.csv`, UTF-8 BOM). `format`이 없거나 다른 값이면 422 |
| GET | `/stats` | 통계 대시보드 집계 (200, 읽기 전용). 전체/완료/미완료/기한 초과, 완료율(소수 1자리), 최근 30일 일별 완료(`daily_completions`), 7·30일 합계, 완료 시각이 없어 추이에서 뺀 수(`trend_excluded`), 우선순위별 미완료, 마감일 준수율(분모 0이면 `rate: null`). 날짜는 Asia/Seoul |
| GET | `/task-templates` | 템플릿 목록 |
| POST | `/task-templates` | `{"name", "title", "description", "priority", "tags", "recurrence", "subtasks": ["..."]}` — 템플릿 생성 (201). 이름이 겹치면 409 |
| PUT | `/task-templates/{id}` | 템플릿 전체 수정 (200, 없으면 404, 이름이 겹치면 409) |
| DELETE | `/task-templates/{id}` | 템플릿 삭제 (204, 없으면 404) |
| GET | `/` | 화면 |

반복 일정은 `recurrence`(`none`·`daily`·`weekly`·`monthly`)로 정하며, 반복하려면 `due_date`가 필요합니다. 반복 항목이 미완료에서 완료로 바뀌면(PUT, 일괄 완료, `completed: true`로 생성) 직전 마감일 + 1주기를 마감일로 하는 다음 일정이 한 번 만들어집니다. 매월 반복에서 없는 날짜는 그 달 말일로 당깁니다(1/31 → 2/28).

CSV 컬럼은 `id,title,description,completed,priority,due_date,tags,favorite,recurrence,subtasks,created_at,completed_at`입니다. 태그는 `;`로 잇고, 하위 작업은 `완료/전체`(예: `1/2`)로 적습니다. `= + - @`로 시작하는 값은 엑셀 수식으로 실행되지 않도록 앞에 `'`를 붙입니다.

서버를 띄운 뒤 http://localhost:8000/docs 에서 요청·응답 스키마를 볼 수 있습니다.

### 인증·계정 경로 (7.0.0, 8.0.0)
| 메서드 | 경로 | 동작 |
|---|---|---|
| POST | `/auth/register` | 회원가입 (201). `Origin` 검사, 이미 있는 이름이면 409 |
| POST | `/auth/login` | 로그인 (200, `{id, username, csrf_token}`). `Origin` 검사, HttpOnly 세션 쿠키 발급(14일 고정) |
| POST | `/auth/logout` | 로그아웃 (204). 유효한 세션이 있으면 `X-CSRF-Token` 검사 |
| GET | `/auth/me` | 현재 로그인 사용자 (200, (8.0.0) `created_at` 가입 시각 포함. 세션이 없거나 만료되면 401) |
| POST | `/auth/password` | (8.0.0) `{"current_password", "new_password"}` — 비밀번호 변경 (200 + 새 세션 쿠키·새 `csrf_token`). 그 사용자의 모든 세션을 지운다 |
| POST | `/auth/account/delete` | (8.0.0) `{"password"}` — 회원 탈퇴 (204, 쿠키 삭제). 공유 JSON 은 그대로 |

- `Origin` 불일치·CSRF 오류는 403, 로그인·가입·현재 비밀번호 확인 시도가 많으면 429(`Retry-After`), 해시 처리 혼잡·잠금 대기 초과는 503입니다.
- (8.0.0) 비밀번호 변경·탈퇴: 현재 비밀번호 불일치 400, 처리 중 세션이 사라지면 401, 동시 요청 충돌 409(`다른 요청과 충돌했습니다. 다시 시도해 주세요`), 탈퇴를 막는 참조가 있으면 409(`지금은 탈퇴할 수 없는 계정입니다. 관리자에게 문의하세요`), 입력 검증 422. 상세는 `V8_SPEC.md` §37.
- (7.0.0 당시) 데이터 경로 15개는 인증·CSRF 없이 동작했습니다. 8.0.0에서 위 "API" 절의 정책으로 바뀌었습니다.

## Jenkins + JMeter 과제 배포 (5002)
> 과제 전용 예외입니다. v7 공개 시연(5001)과 아래 "배포와 데이터 관리"의 v6 기록은 바뀌지 않습니다.

- Jenkins 파이프라인: Checkout → 의존성 점검 → pytest(빌드 전용 PostgreSQL 18) → 이미지 빌드 → Docker Hub push(`eunohchoi/fastapi-app:<빌드번호>`) → `docker compose -p fastapi-jmeter` 배포(5002) → JMeter 부하 테스트.
- 앱: `http://<서버>:5002` (컨테이너 `fastapi-jmeter-app`). 데이터는 `fastapi_jmeter_app-data` 볼륨의 합성 데이터뿐입니다.
- JMeter 결과: Jenkins 빌드의 "JMeter HTML Report"와 아티팩트(`results.jtl`, `jmeter.log`). 샘플 실패·샘플 수 불일치·InfluxDB 미기록이면 빌드가 실패합니다.
- 성능 그래프: 과제 전용 Grafana `http://127.0.0.1:3001`(SSH 터널 `ssh -L 3001:127.0.0.1:3001 <서버>`), 데이터 소스 `InfluxDB-JMeter`, 대시보드 ID 5496 가져오기.
- pytest 의 PostgreSQL 테스트는 빌드마다 만드는 임시 DB 에서만 돕니다(호스트 포트 없음, 빌드 후 삭제). 테스트 DB 보호 장치는 그대로입니다.
- 손으로 실행할 때는 반드시 `-p fastapi-jmeter` 와 서비스 이름을 붙입니다. `docker compose config`(값 출력)·`down -v`·`volume prune` 은 쓰지 않습니다.
- (8.0.0) 8.0.0 앱을 대상으로 JMeter를 돌리려면 아래 "JMeter 로그인 절차"를 따라야 합니다. 5002 Jenkins 작업의 실제 스크립트는 저장소에 없으므로, 그 작업에도 같은 변경(Credentials 바인딩 → 속성 파일 → `-q` → `--expected-auth`)을 따로 넣어야 합니다(별도 승인).

### JMeter 로그인 절차 (8.0.0)
- 대상 환경마다 **합성 테스트 계정 1개**를 둡니다. 가입이 막혀 있으면(`REGISTRATION_ENABLED=false`) 잠시 허용 → 계정 생성 → 다시 `false`로 되돌립니다. 계정명·비밀번호는 저장소·로그·보고에 넣지 않습니다.
- 계획(`jmeter/fastapi_test_plan.jmx`, `jmeter/experiments/concurrency_readonly.jmx`)은 스레드마다 첫 반복에서 `AUTH POST /auth/login`을 1회 보내 세션 쿠키(스레드별 Cookie Manager)와 `csrf_token`을 받고, `POST /todos`에 `X-CSRF-Token`을 붙입니다. 마지막 반복 뒤 `AUTH POST /auth/logout`으로 세션을 지웁니다. 로그인에 실패한 스레드는 측정 샘플을 보내지 않고 멈춥니다(샘플 수가 모자라 판정 실패).
- **측정 분리**: InfluxDB Backend Listener는 측정 샘플러(`GET /`, `POST /todos`, `GET /todos`)를 묶은 "Measured" 컨트롤러 안에만 있어서 `AUTH ...` 샘플은 InfluxDB 지표(`transaction='all'` 포함)에 들어가지 않습니다. JTL·HTML 리포트에는 별도 라벨로 남고, `jmeter/check_results.py --expected-auth`가 인증 샘플을 측정 샘플과 따로 셉니다(실패율은 측정 샘플 기준).
- **자격 증명 전달**: 명령줄 `-J`(프로세스 목록·콘솔 로그에 노출)를 쓰지 않습니다. `jmeter/auth_properties.py write <경로>`가 Jenkins Credentials 환경변수(`JM_AUTH_USER`·`JM_AUTH_PASS`) 또는 대화형 입력으로 속성 파일을 **권한 600의 새 파일**로 만들고, JMeter 컨테이너에 읽기 전용으로 붙여 `-q`로 넘깁니다. Jenkins는 워크스페이스 밖 임시 디렉터리(700)에 만들고 끝나면 지웁니다.
- v8 결과는 요청마다 세션 조회가 더해지므로 v7(인증 없음) 측정값과 직접 비교하지 않습니다.
- (2026-11-16, 로컬 격리 실행) 첫 실행은 로그아웃 조건식 오류로 미통과였고(로그아웃 0/10), 조건식을 고친 뒤 남은 합성 데이터를 출처 확인 후 테스트 하네스 경로로 정리하고 다시 실행해 **통과**했습니다: 측정 **30/30**, 인증 **20/20**(로그인 10·로그아웃 10), `JMETER_CHECK_OK`, Groovy 오류·ERROR·FATAL 0건, 가짜 InfluxDB의 `AUTH` 지표 **0건**·`all/all` 합계 30. 실제 InfluxDB와 Jenkins에서는 아직 실행하지 않았습니다.

## 팀 서버 배포 (3004)와 JMeter 동시 사용자 실험
> 과제 전용입니다. 앱은 같은 v7.0.0 코드라 데이터 API 15개가 인증 없이 열려 있습니다(`V7_PLAN.md` R-34) — 합성 데이터만 둡니다.

**팀 서버 배포** — `deploy/team/docker-compose.team.yml`
- 팀 서버 `163.239.77.78:3004`에 앱·전용 PostgreSQL 18(호스트 포트 없음)·일회성 `migrate`를 올립니다. 이미지는 Jenkins가 push한 `eunohchoi/fastapi-app`을 digest로 고정해 pull만 합니다(서버에서 빌드하지 않음).
- 같은 폴더의 `.env`(권한 600, 서버에서만 만들고 출력·커밋하지 않음)에 다음을 둡니다.
  - 비밀값: `TEAM_PG_ADMIN_PASSWORD`, `TEAM_MIGRATOR_DB_PASSWORD`, `TEAM_APP_DB_PASSWORD`, `TEAM_AUTH_FAILURE_KEY`
  - 설정: `TEAM_ACCOUNT`(필수, 팀 서버 계정명 — 저장소에는 넣지 않음), `TEAM_REGISTRATION_ENABLED`(선택)
- 프로젝트·컨테이너·네트워크·볼륨 이름은 `<TEAM_ACCOUNT>-todo3004`, `<TEAM_ACCOUNT>_todo3004_*`입니다. 이미 배포된 이름을 바꾸지 않습니다.
- 순서: `config --quiet` → `up -d db`(healthy 확인) → `run --rm -T migrate` → `up -d --no-deps app`. `down`·`down -v`·`volume prune`과 값이 출력되는 `config`는 쓰지 않습니다.

**회원가입 설정 `TEAM_REGISTRATION_ENABLED`**
- 기본은 `false`입니다(공개 포트). 이때 가입하면 403 `회원가입이 비활성화되어 있습니다`가 화면에 표시됩니다 — 오류가 아니라 설정입니다.
- 켜려면 `.env`에 `TEAM_REGISTRATION_ENABLED=true`(소문자만 — 다른 값이면 앱이 시작하지 않음)를 넣고 앱만 다시 만듭니다: `up -d --no-deps app`. 설정은 시작할 때만 읽습니다. DB·할 일 볼륨은 그대로이고 기존 로그인 세션도 유지됩니다.
- 필요한 계정을 만든 뒤에는 `false`로 되돌리는 것을 권합니다. 가입·로그인은 HTTP로 오가고, 계정이 있어도 할 일 데이터는 모든 사용자가 공유합니다.

**Jenkins 검증 파이프라인** — `ci/Jenkinsfile.team3004`
- Jenkins 작업("Jenkins Deploy Jmeter")에 "Pipeline script"로 붙여 넣도록 만든 스크립트의 저장소 사본입니다. 작업에 실제로 들어 있는 스크립트(빌드 Replay 화면)와 같은지는 아직 확인하지 않았습니다 — 다르면 작업 쪽이 기준입니다.
- (8.0.0 C안, 2026-11-16 — 사본만 수정, **실제 Jenkins 빌드는 하지 않았습니다**) 공유 `todo.json` 오염을 막으려고 JMeter를 팀 서버가 아닌 **빌드마다 띄우는 일회용 v8 앱·PostgreSQL**에 보냅니다(`V8_SPEC.md` §44.3).
  - 체크아웃: 기본 브랜치 `feature/v8.0.0`(`CHECKOUT_BRANCH`, 허용 목록은 `main`·`feature/v8.0.0`). HEAD가 `origin/<브랜치>`와 같은지, `REQUIRED_COMMIT` 포함·깨끗한 작업 트리를 확인합니다.
  - 순서: pip-audit → pytest(빌드 전용 PostgreSQL 18) → v8 이미지 로컬 빌드(push 없음) → 팀 서버 확인 → JMeter 이미지 → 일회용 대상 기동 → 테스트 계정 준비 → 격리 게이트 → JMeter → 결과·InfluxDB 확인 → 정리. 배포·SSH는 하지 않습니다.
  - 팀 서버: `TEAM_APP_VERSION=7.0.0`으로 **읽기 전용 GET 확인**만 합니다(`/` 200, 비로그인 `/todos` 200, `/auth/me` 401, `Version 7.0.0`). **팀 서버에는 JMeter 요청(쓰기 포함)을 보내지 않습니다.**
  - 일회용 대상: 이름 `todo-eph-team3004-<빌드번호>-*`, 라벨 `org.fastapi-todos.ci-build=<BUILD_TAG>`, 내부 전용 네트워크, 호스트 포트 없음, PostgreSQL·앱 데이터는 tmpfs. 같은 이름이 이미 있으면 멈춥니다. 격리 게이트(대상 이름·라벨·포트·마운트·네트워크 구성원·`DATABASE_URL`·JSON 경로)가 실패하면 JMeter는 돌지 않습니다.
  - 테스트 계정: 기본 `JM_AUTH_MODE=generated` — 빌드마다 무작위 합성 계정을 일회용 앱에만 가입합니다(Credentials 불필요). `credentials` 모드(Credentials `jm-team3004-test-account`)는 선택 사항이며 **별도 검증이 필요**합니다. 어느 모드든 값은 워크스페이스 밖 속성 파일(700/600) → `-q`로만 넘깁니다.
  - 판정: 측정 30·인증 20(`check_results.py --expected-auth`), 라벨별 응답 코드, JTL 요청 URL이 모두 일회용 앱, JMeter 로그의 Groovy 오류·ERROR·FATAL 없음, InfluxDB(`application=fastapi-todos-eph-team3004-<빌드번호>`, 이번 실행 구간만)의 이벤트 1건 이상·`AUTH` 지표 0건·`all/all` 합계 30. 쿼리 실패는 0건과 구분해 실패로 처리합니다.
  - 정리: 파이프라인 `post { always }`에서 이 빌드의 정확한 이름이고 라벨이 같은 자원만 지웁니다(다르면 `EPH_CLEANUP_SKIP`). 성공·실패·중단 모두 실행됩니다.
  - 남은 것: 커밋·push, 작업 스크립트 교체(기존 스크립트 백업), 빌드 1회 실행 — 각각 별도 승인. tmpfs 옵션, 실제 InfluxDB 쿼리, Jenkins 호스트 `python3`는 첫 실행에서 확인합니다.
- (7.0.0 당시) 팀 서버 확인은 `/` 200, `/todos` 200, `/auth/me` 401, `Version 7.0.0`이었고 JMeter 30 샘플은 인증 없이 보냈습니다.
- Test 단계의 `[PG 진단]` 블록(PostgreSQL 로그 옵션, 백엔드 샘플러, `pg-diag/` 아티팩트)은 R-19 연결 수 2 원인 확인용 임시 코드입니다. 판정에는 쓰이지 않으며, 원인이 확인되면 따로 제거합니다.

**InfluxDB·Grafana**
- JMeter Backend Listener가 개인 서버의 기존 InfluxDB(`fastapi-jmeter-influxdb`, 네트워크 `fastapi_jmeter_net`)에 `http://influxdb:8086/write?db=jmeter`로 기록합니다.
- 구분 태그: Jenkins 팀 검증은 `application=fastapi-todos-team3004`(7.0.0 당시) → (8.0.0 C안) `fastapi-todos-eph-team3004-<빌드번호>`(빌드마다 고유), 실험은 `team3004-exp-<STAMP>-u1|u3|u5`. 시작·종료는 `events` 측정값의 `build=<RUN_ID>`입니다.
- 집계는 `transaction='all' AND statut='all'` 행만 씁니다. 그래프는 위 Grafana(3001, SSH 터널)에서 봅니다.

**동시 사용자 실험** — `jmeter/experiments/` (절차는 [jmeter/experiments/README.md](jmeter/experiments/README.md))
- 동시 사용자 1·3·5명 × 사용자당 50회 × (`GET /`, `GET /todos`) = 총 900요청, 요청마다 400~600ms 대기. **읽기 전용**이라 팀 서버 데이터가 바뀌지 않습니다.
- (8.0.0) 팀 서버가 v8이면 테스트 계정 속성 파일(`JMETER_AUTH_FILE`, 권한 600)이 필요합니다. 스레드마다 로그인·로그아웃 각 1회(총 18요청)가 더해지며 지표에서는 빠집니다. 기존 u1·u3·u5 결과는 v7(인증 없음) 기준이라 v8 결과와 직접 비교하지 않습니다.
- 개인 서버에서 손으로 실행합니다(Jenkins와 무관): `--check-only`로 사전 점검 → 실행. 단계마다 판정이 실패하면 다음 단계로 가지 않습니다.
- 결과는 서버의 `~/jmeter-exp/<STAMP>/`(`summary.md`, 단계별 JTL·HTML 리포트)이며 저장소에 넣지 않습니다.

## 배포와 데이터 관리
> **이 절은 6.0.0(JSON) 운영 절차입니다.** 7.0.0은 운영에 배포하지 않습니다. 현재 compose·Jenkins 설정에는 7.0.0 앱이 요구하는 설정(`APP_ENV`, `AUTH_FAILURE_KEY`, PostgreSQL)이 없어서, 이 절차로 7.0.0 코드를 배포하면 앱이 시작되지 않을 것으로 판단합니다(코드 분석, 실행 미확인). 7.0.0용 compose·Jenkins 변경안은 8.0.0에서 다룹니다(`V7_PLAN.md` §P10).

명령은 모두 **배포 서버(Linux)의 저장소 루트**에서 실행합니다.

### 구성
| 항목 | 값 |
|---|---|
| 컨테이너 | `FastApi-app` (서비스 `fastapi-app`), 이전 이름 `FastApi-app4` |
| 포트 | `5001` → 컨테이너 `8000` |
| 데이터 볼륨 | `fastapi_todos_fastapi-data` → `/app/data` (이름 고정) |
| 데이터 경로 | `TODO_FILE=/app/data/todo.json`, `TEMPLATE_FILE=/app/data/task_templates.json` |

- 이미지에는 데이터를 넣지 않습니다. 볼륨이 비어 있으면 앱이 시작하면서 `todo.json`을 `[]`로 만들고, 템플릿 파일은 처음 저장할 때 만듭니다.
- 백업 파일은 `backups/`에 둡니다 (`.gitignore`에 들어 있어 커밋되지 않습니다).
- [`scripts/load-data.sh`](scripts/load-data.sh)는 백업 폴더의 파일을 볼륨으로 옮깁니다. 앱 이미지 안에서 `appuser` 권한으로 실행되므로 파일 소유자가 맞게 들어갑니다. 동작은 다음과 같습니다.
  - 모든 파일이 JSON 목록인지 먼저 검사합니다. 하나라도 깨졌으면 아무것도 바꾸지 않습니다.
  - `todo.json`이 없거나 비어 있으면 `[]`로 시작합니다. 템플릿 파일이 없으면 건너뜁니다.
  - 볼륨에 이미 할 일 데이터가 있으면 멈춥니다. 덮어쓰려면 `--force`를 붙입니다.

### Compose 프로젝트 이름 (Jenkins와 수동 배포 공통)
컨테이너 이름이 `FastApi-app`로 고정이므로, Jenkins와 수동 배포가 **같은 Compose 프로젝트 이름**을 써야 합니다.
- 프로젝트 이름이 다르면 서로를 다른 프로젝트로 봅니다.
- 그래서 `container name "/FastApi-app" is already in use` 충돌이 나고 포트 5001도 겹칩니다.
- SonarQube·Prometheus·Grafana 볼륨도 프로젝트 이름이 앞에 붙으므로, 이름이 달라지면 빈 볼륨이 새로 만들어집니다.

1. 서버에서 지금 쓰는 프로젝트 이름을 확인합니다.
   ```bash
   docker inspect FastApi-app4 --format '{{index .Config.Labels "com.docker.compose.project"}}'
   docker volume ls --filter name=sonarqube_data      # 앞부분(<프로젝트>_sonarqube_data)이 프로젝트 이름
   ```
2. 그 이름을 수동 배포와 Jenkins에 똑같이 지정합니다. 아래 예시의 `fastapi_todos`를 1번에서 확인한 이름으로 바꾸세요.
   ```bash
   export COMPOSE_PROJECT_NAME=fastapi_todos          # 수동 배포 (셸마다)
   ```
   ```groovy
   // Jenkinsfile
   environment {
       COMPOSE_PROJECT_NAME = 'fastapi_todos'
   }
   stages {
       stage('Deploy') {
           steps {
               sh 'docker compose build fastapi-app'
               sh 'docker compose up -d --no-deps fastapi-app'   // 앱만 교체, 다른 서비스는 건드리지 않음
           }
       }
   }
   ```
3. 배포 스크립트에 `docker compose down -v`와 `docker volume prune`을 쓰지 않습니다. 볼륨이 지워집니다.

FastAPI 데이터 볼륨은 이름을 `fastapi_todos_fastapi-data`로 고정했습니다. 그래서 프로젝트 이름이 달라도 같은 데이터를 씁니다. 다만 다른 프로젝트 이름으로 실행하면 Compose가 "volume already exists but was created for project ..." 경고를 냅니다. 이 경고가 보이면 프로젝트 이름이 어긋났다는 뜻입니다.

### FastApi-app4 → FastApi-app 데이터 이전 (한 번만)
**순서를 지키고, 6단계 검증이 끝나기 전에는 이전 컨테이너·스냅샷·백업을 지우지 않습니다.**

```bash
export COMPOSE_PROJECT_NAME=fastapi_todos             # 위에서 확인한 이름

# 1. 사전 점검 (읽기만 함)
docker ps -a --filter name=FastApi-app --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'
docker inspect FastApi-app4 --format '{{index .Config.Labels "com.docker.compose.project"}}'
docker volume ls --filter name=fastapi_todos_fastapi-data   # 비어 있어야 함
ss -ltn | grep ':5001 ' || echo "5001 사용 가능"

# 2. 이전 컨테이너에서 백업 (실행 중이든 멈춰 있든 가능)
B=backups/$(date +%Y%m%d-%H%M%S); mkdir -p "$B"
docker cp FastApi-app4:/app/todo.json "$B/todo.json"
docker cp FastApi-app4:/app/task_templates.json "$B/task_templates.json" \
  || echo "템플릿 파일 없음 (정상, 건너뜀)"
docker commit FastApi-app4 "fastapi-app4-backup:${B#backups/}"   # 컨테이너 전체 스냅샷 (롤백용)
ls -l "$B"

# 3. 새 이미지 빌드 (아직 컨테이너는 바꾸지 않음)
docker compose build fastapi-app

# 4. 백업을 새 볼륨에 넣기 (볼륨은 여기서 처음 만들어짐)
docker compose run --rm --no-deps \
  -v "$PWD/$B:/backup:ro" -v "$PWD/scripts:/scripts:ro" \
  fastapi-app sh /scripts/load-data.sh
#    출력된 건수가 백업 파일의 항목 수와 같은지 확인한다

# 5. 새 컨테이너 시작
docker compose up -d --no-deps fastapi-app

# 6. 검증
docker compose ps fastapi-app
curl -s http://localhost:5001/todos          | python3 -c 'import json,sys; print(len(json.load(sys.stdin)), "건")'
curl -s http://localhost:5001/task-templates | python3 -c 'import json,sys; print(len(json.load(sys.stdin)), "건")'
docker compose up -d --no-deps --force-recreate fastapi-app   # 다시 만들어도 같은 건수인지 위 두 줄로 재확인
```
- 4단계 출력과 6단계 건수가 2단계 백업과 같아야 합니다.
- 화면(http://서버:5001)에서 항목 추가와 템플릿 저장이 되는지도 확인합니다. 쓰기 권한까지 확인하는 단계입니다.
- Prometheus(`http://127.0.0.1:7070/targets`)에서 `fastapi` 대상이 UP인지 봅니다.

**5단계에서 이전 컨테이너가 어떻게 되나**
- `FastApi-app4`가 **같은 프로젝트** 소속이면 Compose가 새 컨테이너로 교체하면서 이전 컨테이너를 지웁니다. 2단계의 파일 백업과 `docker commit` 스냅샷이 그래서 필요합니다.
- **다른 프로젝트**(예: Jenkins 작업 폴더) 소속이면 그대로 남아 5004에서 계속 돌아갑니다. 검증이 끝난 뒤 직접 정리합니다.

**롤백** (검증 실패 시)
```bash
docker compose stop fastapi-app
docker start FastApi-app4 2>/dev/null \
  || docker run -d --name FastApi-app4 -p 5004:8000 --restart unless-stopped "fastapi-app4-backup:<시각>"
```

**정리** (검증 통과 후, 충분히 운영해 본 다음)
```bash
docker stop FastApi-app4 && docker rm FastApi-app4    # 남아 있는 경우만
docker image rm "fastapi-app4-backup:<시각>"
# backups/ 폴더는 보관
```

### 백업
```bash
B=backups/$(date +%Y%m%d-%H%M%S); mkdir -p "$B"
docker compose cp fastapi-app:/app/data/. "$B/"
ls -l "$B"
```

### 복구
복구는 볼륨의 현재 데이터를 덮어씁니다. 먼저 위 "백업"으로 지금 상태를 남겨 두세요.
```bash
R=backups/<복구할 시각>
docker compose stop fastapi-app
docker compose run --rm --no-deps \
  -v "$PWD/$R:/backup:ro" -v "$PWD/scripts:/scripts:ro" \
  fastapi-app sh /scripts/load-data.sh --force
docker compose start fastapi-app
```
백업에 템플릿 파일이 없으면 템플릿은 지금 상태 그대로 둡니다.

### 재배포
```bash
export COMPOSE_PROJECT_NAME=fastapi_todos
docker compose build fastapi-app
docker compose up -d --no-deps fastapi-app            # 데이터는 볼륨에 남는다
```
코드만 바꾼 재배포라면 데이터 이전이나 복구가 필요 없습니다. 그래도 큰 변경 전에는 백업을 먼저 해 두세요.

### 6.0.0 배포 전 확인 (로컬 격리 환경)
운영과 이름·포트·볼륨이 겹치지 않도록 임시 override 파일과 별도 프로젝트 이름으로 확인합니다. 운영 볼륨 `fastapi_todos_fastapi-data`와 컨테이너 `FastApi-app`은 건드리지 않습니다.
```bash
cat > /tmp/compose.v6test.yml <<'EOF'
services:
  fastapi-app:
    image: fastapi-todos-v6test:6.0.0
    container_name: FastApi-app-v6test
    ports: !override
      - "127.0.0.1:15001:8000"
    restart: "no"
volumes:
  fastapi-data:
    name: fastapi_v6test_data
EOF
T="docker compose -p fastapi-v6test -f docker-compose.yml -f /tmp/compose.v6test.yml"
$T config fastapi-app | grep -E "container_name|published|name: fastapi"   # 운영 이름·5001이 없는지 먼저 확인

$T build fastapi-app                                                         # C1 일부: 이미지 빌드
docker run --rm fastapi-todos-v6test:6.0.0 sh -c 'ls -A /app; id; pip list 2>/dev/null | grep -iE "pytest|playwright|httpx|prometheus"'
                                    # /app 에 tests·requirements-dev.txt 없음, uid 10001(appuser), 개발 도구 없음, prometheus 있음 (0-3)
$T up -d --no-deps fastapi-app
curl -s -o /dev/null -w "%{http_code}
" http://127.0.0.1:15001/            # 200, 화면에 Version 6.0.0 (C2)
curl -s http://127.0.0.1:15001/stats | head -c 120; echo                      # 200
curl -s http://127.0.0.1:15001/metrics | grep -m1 http_requests_total         # /metrics
curl -s -X POST http://127.0.0.1:15001/todos -H 'Content-Type: application/json' -d '{"title":"persist-check"}'
$T up -d --no-deps --force-recreate fastapi-app                                # C5: 컨테이너 재생성
curl -s http://127.0.0.1:15001/todos | grep -o persist-check                 # 남아 있어야 한다

$T down                                                                       # 테스트 컨테이너·네트워크만 정리
docker volume rm fastapi_v6test_data                                          # 테스트 볼륨만 삭제 (-v·prune 쓰지 않음)
docker image rm fastapi-todos-v6test:6.0.0
```

## 테스트
```bash
cd fastapi-app
pip install -r requirements-dev.txt
playwright install chromium        # UI 테스트용 브라우저 (처음 한 번)

```

### 7.0.0 테스트 실행 조건
7.0.0부터 테스트는 실행 조건이 다른 묶음으로 나뉩니다(`V7_SPEC.md` §33). **`pytest`만 실행하면 안 됩니다**: `tests/test_ui.py`는 `TEST_DATABASE_URL`이 없으면 skip이 아니라 실패하도록 되어 있습니다(코드 분석에 따른 판단, 그 상태로 실행해 확인하지는 않았습니다). 결과는 묶음마다 따로 보고하고 합산하지 않습니다.

**1. DB 없는 회귀** — `DATABASE_URL`·`TEST_DATABASE_URL`을 비운 환경에서 PostgreSQL·브라우저 인증 테스트 3개 파일을 빼고 실행합니다.
```bash
python -m pytest -q --ignore=tests/test_ui.py --ignore=tests/test_ui_auth.py --ignore=tests/test_db_postgres.py
```
- (8.0.0, 2026-11-16 3단계) **1161 passed, 9 deselected**(2026-11-15 1142 → 통합 테스트 주소 검사·Jenkins C안 정적 테스트 추가). deselected 9개는 기본 실행에서 빠지는 배포 서버 대상 integration 테스트입니다. `tests/test_jmeter_plan.py`(JMeter 계획·자격 증명 처리·판정 스크립트·Jenkins 사본의 정적 검증)가 여기에 포함됩니다.
- (7.0.0 당시) 2026-11-09: 994 passed, 3 deselected.

**2. `tests/test_ui_auth.py`** — 로그인 게이트·로그인·가입·계정 화면 Playwright 테스트. DB 없이 `/auth/*`와 데이터 경로(`/todos`·`/stats`·`/task-templates`) 응답을 브라우저 안에서 흉내 내 따로 실행합니다(앱에 인증 우회 없음).
- (8.0.0, 2026-11-15) **54 passed**. (7.0.0 당시 31 passed)

**3. PostgreSQL 테스트 (`tests/test_db_postgres.py`, `tests/test_ui.py`)** — 실행 전에 아래를 반드시 확인하세요.
- **파괴적 초기화**: 테스트 DB의 `public` 스키마를 **지우고 다시 만듭니다.** 테스트 DB 외의 DB에 절대 쓰지 마세요.
- **고정 대상**: `TEST_DATABASE_URL`은 로컬 PostgreSQL 18.x의 `todo_test_user@127.0.0.1:5433/todo_test`만 허용합니다. 포트 생략·5432·다른 호스트·DB·계정이면 테스트 세션을 시작하지 않습니다. `DATABASE_URL`은 비워 둡니다. 운영·개발 DB(`todo_dev`)와 PostgreSQL 16(5432)에는 연결하지 않습니다.
- **삭제 전 게이트(R-19)**: 스키마를 지우기 전에 서버·알려진 객체·소유자·연결 수를 확인하고, 어긋나면 지우지 않고 멈춥니다. 게이트에도 남은 위험이 있습니다(아래, 릴리스 노트 "알려진 제한").
- **별도 승인과 사전·사후 점검**: 이 저장소의 작업 규칙상 PostgreSQL 실행은 **실행마다 별도 승인**을 받고, 실행 전후에 읽기 전용 R-19 점검(서버·객체 수·소유자·다른 연결·사용자 데이터 0행)을 합니다(`V7_PLAN.md` §P8.6). 점검 스크립트는 저장소에 들어 있지 않습니다.
- 비밀번호는 `pgpass.conf`에만 두고, URL·저장소·로그에 넣지 않습니다.

위 조건을 확인한 뒤, `TEST_DATABASE_URL`을 고정 대상으로 두고 `DATABASE_URL`은 비운 환경에서 파일별로 따로 실행합니다.
```bash
python -m pytest -q tests/test_db_postgres.py   # PostgreSQL 하네스
python -m pytest -q tests/test_ui.py            # PostgreSQL UI (인증 UI 서버 + 실제 로그인 세션, 할 일·템플릿은 임시 JSON)
```
- (8.0.0, 2026-11-15) PostgreSQL 하네스 **97/97**(1단계), PostgreSQL UI **125/125**(2단계). 두 실행 모두 사전·사후 R-19 점검이 같았고 종료 후 사용자 데이터 7개 표는 0행이었습니다. 3단계(2026-11-15)에 PostgreSQL 보안 회귀 테스트 1개를 더한 하네스 **98/98**, `Version 8.0.0` 문구 변경 뒤 UI **125/125**를 각각 다시 확인했습니다(사전·사후 R-19 점검 일치).
- R-19 게이트가 간헐적으로 "DB 연결 수 2"를 보고 멈추는 일이 있습니다(원인 미확정, autovacuum은 가설). 게이트가 멈추면 하네스가 그 순간의 백엔드 목록(PID·종류·역할·상태·시작 시각, 진단 연결 표시)을 중단 메시지에 덧붙입니다. 게이트 조건은 그대로입니다.
- (7.0.0 당시) 2026-11-09: PostgreSQL 하네스 83 passed, PostgreSQL UI 125 passed.
- UI 종료 때의 스키마 초기화(알려진 8개 표만 RESTRICT로 삭제)는 정상 경로를 코드 구조와 테스트 결과로 **간접 확인**했을 뿐입니다. RESTRICT 실패 경로, 표 재생성과 Alembic 업그레이드 사이의 공백, 출처 확인의 한계는 남아 있습니다(`V7_PLAN.md` R-19·R-36).

### 그 밖의 주의
- `pytest -m integration`은 `INTEGRATION_BASE_URL`의 서버에 POST·DELETE를 보냅니다. (8.0.0) 이 주소는 **필수이고 기본값이 없습니다**(없거나 형식이 틀리면 요청 전에 실패). **명시적 승인 없이 실행하지 않습니다.**
  - (8.0.0, 2026-11-15) 로컬 격리 서버(테스트 DB)에서 1회 실행해 **9/9** 통과했습니다. 외부 서버에서는 실행하지 않았습니다.
  - (8.0.0) 테스트 계정으로 로그인합니다: `INTEGRATION_USERNAME`·`INTEGRATION_PASSWORD` 환경변수(Jenkins는 Credentials 바인딩)가 없으면 skip이 아니라 실패합니다. 세션 쿠키와 `csrf_token`으로 기존 CRUD를 확인하고, 익명 401·CSRF 없음 403도 확인한 뒤 로그아웃합니다. 값은 명령줄·로그에 넣지 않습니다.
- 화면·드래그 검증은 Chromium 헤드리스에서만 자동으로 했습니다. 다른 브라우저와 실제 화면의 줄바꿈·px 배치는 확인하지 않았습니다.
- API 테스트는 FastAPI `TestClient`로, 테스트마다 임시 `todo.json`을 씁니다. 실제 `todo.json`·`task_templates.json`은 `tests/conftest.py`가 해시로 보호합니다.
- UI 테스트는 임시 데이터 파일로 uvicorn을 따로 띄우고 Playwright로 확인합니다.
- `tests/`와 `requirements-dev.txt`는 Docker 이미지에 들어가지 않습니다.

(6.0.0 당시) 테스트 명령은 `pytest`(API + UI), `pytest tests/test_api.py`, `pytest tests/test_ui.py --headed`, `pytest -m integration`이었습니다. 7.0.0 코드에서는 위 실행 조건을 따릅니다.

## 구조
```
fastapi-app/
├── main.py               # API, 모델, 저장 로직 (7.0.0: /auth/* 4개, 시작 가드)
├── db.py                 # (7.0.0) PostgreSQL 엔진·ORM·세션, 테스트 DB 보호·삭제 전 게이트(R-19), 인증·사용자 데이터 헬퍼
├── auth.py               # (7.0.0) 순수 인증 함수 (설정, Argon2id, 토큰·CSRF, Origin, 시도 제한 판정)
├── alembic.ini, alembic/ # (7.0.0) 스키마 마이그레이션 (0001, 0002)
├── templates/index.html  # 화면 (HTML + CSS + JS 한 파일)
├── todo.json             # 로컬 실행용 데이터 (Docker는 볼륨 /app/data 사용)
├── requirements.txt      # 런타임 의존성
├── requirements-dev.txt  # 테스트 의존성
└── tests/                # pytest (API, UI, 통합, DB 없는 인증·스키마, PostgreSQL)
    └── pg_harness.py     # (7.0.0) PostgreSQL 테스트 공용 하네스
scripts/load-data.sh      # 백업 → 데이터 볼륨 이전·복구 (6.0.0 운영)
ci/                       # (과제) test-runner.Dockerfile(pytest 이미지), Jenkinsfile.team3004(팀 서버 GET 확인 + 일회용 v8 대상 JMeter 파이프라인 사본)
jmeter/                   # (과제) JMeter 이미지·계획·판정, experiments/(읽기 전용 동시 사용자 실험)
                          #   (8.0.0) auth_properties.py — 테스트 계정 속성 파일(600) 작성·로그인 개수 확인
deploy/team/              # (과제) 팀 서버 3004 compose (계정명·비밀값은 서버의 .env)
```
`migrate_json.py`(JSON → PostgreSQL 이전 도구)는 8.0.0 예정이며 아직 없습니다.
