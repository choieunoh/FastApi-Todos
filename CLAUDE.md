# CLAUDE.md — FastAPI To-Do List

## Purpose
FastAPI와 바닐라 JS로 만든 To-Do 웹 앱이다.
이 문서는 Version 6.0.0 "생산성·보기 확장"의 개발 명세다. v4·v5 명세는 그대로 계약으로 남고, v6에서 바뀐 규칙은 해당 절에 `(6.0.0)`으로 표시한다.
(7.0.0) Version 7.0.0 "Multi-User & PostgreSQL"의 상세 명세는 `V7_SPEC.md`(§25~§34), 단계·절차·위험은 `V7_PLAN.md`에 있다. 이 문서의 v4~v6 규칙은 계속 계약이며, v7에서 바뀌는 규칙만 해당 절에 `(7.0.0)`으로 표시한다. 둘이 다르면 `(7.0.0)` 표시와 `V7_SPEC.md`가 우선한다.
구현과 리뷰는 이 문서를 기준으로 하며, 문서에 없는 기능은 추가하지 않는다.
5.0.0은 기능별 단계(0~8)로, 6.0.0은 단계(1~5, 아래 §19)로, 7.0.0은 Phase 1~6(`V7_PLAN.md`)으로 나누어 구현한다. 각 단계는 앞 단계의 테스트를 모두 통과한 상태에서 시작하고, 단계마다 사용자 승인을 받은 뒤 다음 단계로 간다.

## Current Version
**6.0.0** — v5 기능 + 통계 대시보드, 2열 칸반 보드, 마감일 알림 센터(앱 내부), 라이트/다크/시스템 테마와 반응형 UI, 날짜 기준 Asia/Seoul 통일 (main `176e48a`)

## Target Version
**7.0.0** — PostgreSQL(SQLAlchemy 2.x + Alembic), 회원가입·로그인·로그아웃(Argon2id, HttpOnly 쿠키 세션, CSRF, 로그인 시도 제한), 사용자별 데이터 분리, 기존 JSON 데이터의 안전한 이전. v6 기능은 모두 유지한다.
- v5 당시 Target은 6.0.0이었다. 6.0.0 명세(§19~§24)는 그대로 남는다.

## Technology Stack
| 구분 | 기술 |
|---|---|
| Backend | Python 3.13 (Docker), FastAPI ≥ 0.142, Starlette ≥ 1.7, Pydantic v2, Uvicorn ≥ 0.54 |
| Frontend | `fastapi-app/templates/index.html` 단일 파일 (HTML + CSS + 바닐라 JS, 빌드 없음) |
| Storage | `fastapi-app/todo.json`, `fastapi-app/task_templates.json` (JSON 파일, 후자는 5.0.0 6단계). Docker에서는 볼륨 `/app/data` 안 |
| Storage (7.0.0) | PostgreSQL (SQLAlchemy 2.x ORM, psycopg 3, Alembic 마이그레이션). JSON 파일은 이전 원본·롤백용으로만 남는다 |
| Auth (7.0.0) | argon2-cffi(Argon2id), 서버 세션 테이블 + HttpOnly 쿠키, CSRF 동기화 토큰 |
| Test | pytest, FastAPI `TestClient`(httpx2), UI는 Playwright(pytest-playwright). (7.0.0) PostgreSQL 테스트 DB(`TEST_DATABASE_URL` = `todo_test_user@127.0.0.1:5433/todo_test` 고정) |
| Deploy | Docker (`python:3.13-slim`), docker-compose (포트 `5001:8000`, 컨테이너 `FastApi-app`) |

## Project Structure
```
FastApi_Todos/
├── CLAUDE.md
├── README.md
├── RELEASE_NOTES.md            # 버전별 릴리스 노트 (6.0.0부터)
├── V7_SPEC.md, V7_PLAN.md      # (7.0.0) 명세·구현 계획
├── .gitignore                  # (7.0.0) 실제 환경파일(.env, .env.*)·DB 덤프 제외, .env.dev.example만 추적
├── .env.dev.example            # (7.0.0) 개발·테스트 DB 접속 예시 (비밀번호 없음, 비밀번호는 pgpass.conf)
├── docker-compose.yml
├── docker-compose.dev.yml      # (7.0.0, 선택) Docker 개발용 PostgreSQL만, 운영과 분리. 기본은 로컬 PostgreSQL 18.6
├── scripts/load-data.sh        # 백업 → 데이터 볼륨 이전·복구 (README "배포와 데이터 관리")
├── scripts/dev-postgres-init/  # (7.0.0, 선택) Docker 개발 DB 첫 기동 때 테스트 DB 생성 SQL
└── fastapi-app/
    ├── main.py                 # API + 모델 + 저장 로직 (7.0.0: 라우트·Pydantic 모델·순수 로직). (Phase 2-E) `/auth/*` 4개·시작 가드(lifespan)·`require_user`·`require_csrf`·인증 경로 422 처리기 — v6 데이터 경로는 아직 JSON·인증 없음(Phase 3)
    ├── db.py                   # (7.0.0 Phase 1·2-A·2-C·2-D) 엔진·ORM 7개(2-A `auth_failures`)·세션·테스트 DB 보호·삭제 전 스키마 게이트(R-19)·downgrade 보호, (2-C) 로그인 세션 저장·조회·삭제·만료 정리, (2-D) 시도 제한 잠금·검사·선기록·자기 id 삭제·정리, (2-E) 가입·rehash 헬퍼(`username_exists`·SAVEPOINT `create_user`·`update_password_hash_if_unchanged`·`is_lock_timeout`) (모두 commit 은 호출자). 소유자 조건 헬퍼는 Phase 3 예정
    ├── auth.py                 # (7.0.0 Phase 2-B·2-D) 순수 인증 함수: 설정·입력 검증·Argon2id·토큰·CSRF 비교·Origin 판정·쿠키 속성, (2-D) 제한 판정·Retry-After·username HMAC 키·IP 정규화·Argon2 슬롯 (DB·라우트 없음). 트랜잭션 구성·429·503 응답·`require_user`·라우트 연결은 main.py(2-E)
    ├── migrate_json.py         # (7.0.0 예정) JSON → PostgreSQL 이전·검증·역내보내기·diff CLI
    ├── alembic.ini, alembic/   # (7.0.0 Phase 1·2-A) 스키마 마이그레이션, 리비전 versions/0001_initial.py·0002_auth_failures.py (DATABASE_URL로 접속)
    ├── templates/index.html    # UI 전체
    ├── todo.json               # 데이터
    ├── requirements.txt        # 런타임 의존성 (fastapi, starlette, uvicorn, prometheus 계측 + 7.0.0 sqlalchemy·psycopg·alembic·argon2-cffi)
    ├── requirements-dev.txt    # 테스트 의존성
    ├── pyproject.toml          # pytest 설정 (integration·postgres 마커, 기본 실행은 integration 제외)
    ├── Dockerfile, .dockerignore, sonar-project.properties
    └── tests/
        ├── conftest.py         # 실제 todo.json 보호, UI 테스트용 임시 서버, (7.0.0) TEST_DATABASE_URL 고정 대상 검사
        ├── test_api.py         # TestClient (A, 5.0.0 API)
        ├── test_ui.py          # Playwright (B, 5.0.0 UI)
        ├── test_db_schema.py   # (7.0.0) DB 없이 도는 스키마·보호 장치 테스트
        ├── test_db_postgres.py # (7.0.0) PostgreSQL 테스트 DB(todo_test) 통합 테스트, TEST_DATABASE_URL 없으면 skip
        └── test_integration.py # 배포 서버 대상, `pytest -m integration`
```

## Scope — 기존 명세 (v4.0.0, 회귀 금지)

### 1. 데이터 모델 (Todo)
| 필드 | 타입 | 기본값 | 누가 정하나 |
|---|---|---|---|
| `id` | int | 최대 id + 1 | 서버 |
| `title` | str | (필수) | 클라이언트 |
| `description` | str | `""` | 클라이언트 |
| `completed` | bool | `false` | 클라이언트 |
| `priority` | `"높음" \| "보통" \| "낮음"` | `"보통"` | 클라이언트 |
| `due_date` | `YYYY-MM-DD` 문자열 또는 `null` | `null` | 클라이언트 |
| `tags` | list[str] | `[]` | 클라이언트 |
| `created_at` | ISO 8601 datetime (UTC, 타임존 포함) 또는 `null` | 생성 시각 | **서버** |
| `completed_at` | ISO 8601 datetime (UTC, 타임존 포함) 또는 `null` | `null` | **서버** |

### 2. 서버 검증 규칙 (위반 시 422)
- **title**: 앞뒤 공백을 제거한 뒤 1~100자. 공백만 있으면 거부한다. 저장할 때는 trim한 값을 쓴다.
- **description**: 앞뒤 공백 제거, 최대 500자.
- **priority**: `높음`, `보통`, `낮음` 외의 값은 거부한다.
- **due_date**: 실제로 존재하는 날짜만 허용한다 (`2026-02-30`, `2026/10/05`, `내일` 등은 거부). `""`와 `null`은 "마감일 없음"으로 받아 `null`로 저장한다.
- **tags**:
  - 각 태그는 앞뒤 공백을 제거한 뒤 1~20자. 빈 태그는 거부한다.
  - 쉼표(`,`)가 들어간 태그는 거부한다.
  - 최대 10개.
  - 중복은 대소문자 구분 없이 제거하되, 처음 나온 표기를 유지하고 순서도 유지한다.
- **created_at / completed_at**: 클라이언트가 보낸 값은 **무시**한다.

### 3. 시각 관리 규칙
- POST: `created_at`은 현재 시각이다. `completed=true`로 생성하면 `completed_at`도 현재 시각이다.
- PUT:
  - `created_at`은 기존 값을 그대로 둔다.
  - `completed`가 false → true로 바뀌면 `completed_at`을 현재 시각으로 한다.
  - true → false로 바뀌면 `completed_at`을 `null`로 한다.
  - 상태가 그대로면 기존 `completed_at`을 유지한다.

### 4. 기존 데이터 호환
- `priority`, `due_date`, `tags`, `created_at`, `completed_at`이 없는 항목(v1~v3 데이터)도 오류 없이 읽혀야 한다. 빠진 값은 기본값으로 채운다. `created_at`은 `null`이다.
- 예전 형식의 `due_date: ""`는 `null`로 읽는다.
- (7.0.0) 런타임은 JSON을 읽지 않는다. 이 호환 규칙은 `migrate_json.py`가 JSON → PostgreSQL 이전 때 그대로 적용한다(`V7_SPEC.md` §31).

### 5. API (경로와 상태 코드는 v3와 같다)
- (7.0.0) 경로·메서드·성공 상태 코드는 그대로이고, 사용자 데이터 경로는 **로그인 필수**다. 익명 요청은 401, CSRF 헤더 오류는 403, 다른 사용자의 리소스는 404다. 신규 경로는 `/auth/register`, `/auth/login`, `/auth/logout`, `/auth/me` (`V7_SPEC.md` §28).

| 메서드 | 경로 | 동작 |
|---|---|---|
| GET | `/todos` | 전체 목록 |
| POST | `/todos` | 생성, 201 반환 |
| PUT | `/todos/{id}` | 수정, 없으면 404 |
| DELETE | `/todos/{id}` | 삭제, 204 반환, 없으면 404 |
| GET | `/` | index.html |

### 6. UI
- **버전 표시**: `Version 6.0.0` (6.0.0 개발을 시작하면서 미리 바꿨다. v5 당시 `Version 5.0.0`, v4 당시 `Version 4.0.0`). (7.0.0) Phase 4에서 `Version 7.0.0`으로 바꾸고 B22를 함께 갱신한다.
- (7.0.0) **로그인 화면·헤더**: 로그인하지 않으면 로그인·가입 화면만 보인다. 헤더에 사용자명과 [로그아웃]을 둔다 (`V7_SPEC.md` §30).
- **태그 입력**: 추가 폼과 수정 폼에 쉼표로 구분하는 텍스트 입력을 둔다 (예: `학교, 과제`).
- **태그 표시**: 각 항목에 태그를 칩(chip)으로 보여준다.
- **태그 필터**:
  - 태그 select에 "전체 태그"와 지금 존재하는 태그 목록을 넣는다. 태그 칩을 클릭해도 그 태그로 필터된다.
  - 검색, 상태 필터와 **AND**로 조합한다.
- **수정 폼**:
  - Edit을 누르면 그 항목 자리에 인라인 폼이 열린다. 필드는 제목, 설명, 우선순위 select, 마감일 date, 태그다.
  - [저장]을 누르면 PUT을 보내고, 성공하면 폼을 닫고 목록을 새로 불러온다.
  - [취소]를 누르면 변경을 버리고 원래 표시로 돌아간다.
  - 수정 폼은 한 번에 하나만 열린다.
  - 저장이 실패하면 폼을 열어둔 채 `#error`에 이유를 보여준다.
  - `prompt()`는 사용하지 않는다.
- **진행률 통계**:
  - 전체 / 완료 / 진행 중 / 기한 초과 개수와 완료율(%)을 보여준다.
  - 필터와 상관없이 **전체 목록** 기준으로 센다.
  - 완료율은 `round(완료 ÷ 전체 × 100)`이고, 전체가 0이면 0%다.
  - 기한 초과는 미완료이면서 마감일이 오늘보다 이전인 항목이다.
- **D-day**:
  - 미완료이고 마감일이 있는 항목에만 표시한다.
  - 남은 일수가 N > 0이면 `D-N`, 0이면 `오늘 마감`, 지났으면 `D+N`이다.
  - 날짜 계산은 **Asia/Seoul** 날짜 기준이다 (6.0.0, §20). v5까지는 브라우저 로컬 날짜였다.
- **생성/완료 시각**: 각 항목에 생성일을 보여주고, 완료된 항목에는 완료일도 보여준다. `null`이면 표시하지 않는다.
- **선택 상태 표시**: 지금 선택된 상태 필터 버튼을 시각적으로 구분한다.

### 7. 기존 기능 유지 (회귀 금지)
CRUD, 검색(제목·설명), 상태 필터, 기본순/우선순위순/마감일순 정렬, 기한 초과 강조, 삭제 확인(`confirm`), 오류 메시지 표시는 지금처럼 동작해야 한다.

## Scope — 5.0.0 신규

### 8. 단계와 순서
| 단계 | 기능 | 난이도 |
|---|---|---|
| 0 | 기반 정비 (이 문서, README, 의존성 분리, 테스트 기반, `toPayload`) | 하 |
| 1 | 즐겨찾기 | 하 |
| 2 | 삭제 실행 취소 | 중 |
| 3 | 일괄 작업 | 중 |
| 4 | 하위 할 일 | 중상 |
| 5 | 반복 일정 | 상 |
| 6 | 작업 템플릿 | 중 |
| 7 | 데이터 내보내기 | 하 |
| 8 | 캘린더 뷰 | 중상 |

### 9. 공통 규칙
- `PUT /todos/{id}`는 v4처럼 **전체 교체**다. PATCH식 부분 수정은 하지 않는다.
- 프론트는 모든 PUT 본문을 `toPayload(todo, overrides)`로 만든다. 클라이언트 필드 목록 `TODO_FIELDS`는 한 곳에만 정의하고, 새 클라이언트 필드는 여기에 추가한다.
- 서버가 관리하는 필드(`created_at`, `completed_at`, `subtasks`, `next_spawned`)는 POST/PUT 본문에 있어도 무시하고 기존 값을 유지한다.
- 새 id는 "현재 목록 + 삭제 취소 버퍼"의 최대 id + 1이다 (복구 시 id 충돌 방지).
  - (7.0.0) 할 일·템플릿 id는 PostgreSQL 전역 시퀀스로 매기고 재사용하지 않는다. 이전 직후 시퀀스를 최대 id에 맞춘다. 기본 목록 순서는 `position` 열이다(권장안, `V7_SPEC.md` §27.3·§27.5).
- (7.0.0) 모든 사용자 데이터 조회·변경은 현재 사용자 조건(`user_id`)을 거치는 `db.py` 헬퍼로만 한다. 엔드포인트에서 소유자 조건 없는 조회를 쓰지 않는다.
- `/todos/bulk`, `/todos/restore`, `/todos/export` 같은 고정 경로는 `/todos/{todo_id}` 라우트보다 먼저 선언한다.
- 완료 전이(false → true / true → false)는 서버 함수 하나(`apply_completion`)에서 처리하고 PUT·bulk가 함께 쓴다.
- 통계(전체/완료/진행 중/기한 초과/완료율)는 v4처럼 **상위 할 일** 기준이다.

### 10. 데이터 모델 추가 (Todo)
| 필드 | 타입 | 기본값 | 누가 정하나 | 단계 |
|---|---|---|---|---|
| `favorite` | bool | `false` | 클라이언트 | 1 |
| `subtasks` | `list[{id:int, title:str, completed:bool}]` | `[]` | **서버** (전용 API로만 변경) | 4 |
| `recurrence` | `"none" \| "daily" \| "weekly" \| "monthly"` | `"none"` | 클라이언트 | 5 |
| `next_spawned` | bool | `false` | **서버** | 5 |

- 새 필드가 없는 v1~v4 데이터는 기본값으로 읽는다.

### 11. 즐겨찾기 (1단계)
- 등록·해제는 `PUT /todos/{id}`의 `favorite`로 한다 (새 경로 없음).
- UI: 항목마다 ☆/★ 토글, `★ 즐겨찾기만` 필터 버튼. 검색·상태·태그 필터와 **AND**로 조합한다.

### 12. 삭제 실행 취소 (2단계)
- DELETE한 항목은 서버 메모리 버퍼에 30초 동안(최대 100건, 일괄 작업 한도와 같다) 보관한다. 넘치면 오래된 것부터 버린다. 재시작하면 사라진다.
- `POST /todos/restore` `{ids:[int](1~100개)}` → 원래 id·`created_at`·내용 그대로 복구, 200. 버퍼에 없는 id가 있으면 404이고 아무것도 복구하지 않는다.
- (7.0.0, 권장안 D-7) 버퍼는 사용자별이고 PostgreSQL soft delete(`deleted_at`)로 둔다. 30초·사용자당 100건·원자적 복구는 같고, 재시작·다중 워커에서도 30초 안이면 복구된다. 다른 사용자의 지운 id는 404 (`V7_SPEC.md` §27.4).
- UI: 삭제 확인(`confirm`)은 **유지**한다. 삭제 성공 후 토스트 "N개 삭제됨 [실행 취소]"를 5초 동안 보여준다. 새 삭제가 일어나면 토스트를 교체한다.

### 13. 일괄 작업 (3단계)
- `POST /todos/bulk` `{ids:[int](1~100개, 중복 제거), action:"complete"|"uncomplete"|"delete"}` → 200 `{updated:[Todo], deleted:[id]}`.
- 없는 id가 하나라도 있으면 404이고 파일은 바뀌지 않는다. 시각 규칙(§3)과 반복 규칙(§15)을 그대로 따른다.
  - (7.0.0) 다른 사용자의 id도 "없는 id"로 본다. 한 트랜잭션으로 처리해 실패하면 DB가 바뀌지 않는다.
- 일괄 삭제는 복구 버퍼(§12)를 그대로 쓴다. 버퍼·복구 요청 한도(100건)가 일괄 작업 한도와 같아서, 한 번에 지운 항목은 모두 실행 취소할 수 있다.
- UI: 항목마다 선택 체크박스(완료 체크박스와 별개), 툴바(선택 개수, 보이는 항목 전체 선택, 완료/미완료/삭제). 필터를 바꾸면 보이지 않는 항목은 선택에서 빠진다. 일괄 삭제도 `confirm` → 실행 취소 토스트.

### 14. 하위 할 일 (4단계)
- `POST /todos/{id}/subtasks` `{title}` → 201, `PUT /todos/{id}/subtasks/{sid}` `{title, completed}` → 200, `DELETE /todos/{id}/subtasks/{sid}` → 204. 없는 todo/sid는 404. 응답은 갱신된 Todo(삭제 제외).
- 하위 제목은 trim 후 1~100자, 상위 할 일당 최대 20개. sid는 그 할 일 안에서 최대 sid + 1.
- 상위 할 일의 `completed`와 **연동하지 않는다**. 진행률 `완료/전체 (N%)`만 보여주고, 하위가 0개면 진행률을 숨긴다.
- 조회는 별도 경로 없이 `GET /todos` 응답의 `subtasks`로 한다.
- `PUT .../subtasks/{sid}`는 전체 교체라 `title`, `completed`(bool)가 모두 필수다. 빠지면 422.
- 상위 PUT·bulk·삭제·복구는 `subtasks`를 그대로 유지한다.
- UI: 하위 목록은 기본으로 접혀 있다. 펼친 상태는 다시 그려도 유지되고 새로고침하면 접힌다. 진행률은 접혀 있어도 보인다. 제목은 [수정] 버튼 → 인라인 입력으로 고치며(Enter 저장, Esc 취소) 한 번에 하나만 연다. 삭제는 상위 할 일과 같은 `confirm()`을 거치고, 취소하면 요청을 보내지 않는다(하위 삭제는 실행 취소가 없다).

### 15. 반복 일정 (5단계)
- `recurrence`가 `none`이 아니면 `due_date`가 필수다 (없으면 422).
- 반복 항목이 완료로 바뀌면(PUT, bulk complete, `completed:true` POST) 서버가 다음 일정을 하나 만든다.
  - 다음 마감일: daily +1일, weekly +7일, monthly +1개월 (없는 날은 그 달 마지막 날: 1/31 → 2/28).
  - 기준은 항상 **직전 마감일**이다. 월말로 당겨진 날짜는 그대로 이어진다(1/31 → 2/28 → 3/28).
  - 마감일이 지난 뒤 완료해도 한 주기만 더한다(오늘 이후로 건너뛰지 않는다). 다음 일정이 곧바로 기한 초과일 수 있다.
  - 다음 일정은 목록 끝에 붙고, id는 `next_id()`로 매긴다. bulk 응답의 `updated`에는 넣지 않는다(목록을 다시 불러와 확인).
    - (7.0.0) id는 시퀀스, 순서는 그 사용자의 `max(position) + 1`이다. 원본과 같은 사용자 소유다.
  - 복사: title, description, priority, tags, favorite, recurrence, subtasks(모두 미완료, sid 새로).
  - 원본은 `next_spawned=true`가 되어, 완료를 취소했다가 다시 완료해도 또 만들지 않는다.
- UI: 추가 폼·수정 폼에 반복 select(`반복 없음/매일/매주/매월`), 반복 항목에만 `🔁 매주` 같은 배지. 다음 일정 생성 여부는 따로 표시하지 않는다.

### 16. 작업 템플릿 (6단계)
- 저장소 `task_templates.json` (환경변수 `TEMPLATE_FILE`). 필드: `id, name(1~50), title, description, priority, tags, recurrence, subtasks: list[str]`. 검증은 Todo와 같은 규칙을 공유한다.
  - (7.0.0) 저장소는 PostgreSQL `task_templates` 테이블(사용자별)이다. 이름 중복 409는 같은 사용자 안에서만 검사한다. 아래의 파일·Git 제외·Docker 규칙은 v6 데이터와 이전 원본에 계속 적용한다.
- `GET/POST /task-templates`, `PUT/DELETE /task-templates/{id}` (201/200/204/404).
- 재사용은 **추가 폼에 값을 채우는 방식**이다. 생성은 기존 `POST /todos` + 하위 할 일 POST로 한다 (apply API 없음).
- UI: 템플릿 select, [불러오기], [현재 폼을 템플릿으로 저장](이름 입력), 인라인 수정, 삭제(`confirm`). `prompt()`를 쓰지 않는다.
- 이름은 trim 후 1~50자이고, 대소문자를 구분하지 않고 겹치면 POST·PUT 모두 **409**다(자기 자신으로 바꾸는 것은 허용).
- `subtasks`는 제목 목록이며 하위 할 일과 같은 규칙(trim 후 1~100자, 최대 20개)이다.
- 템플릿에는 `recurrence`만 있고 마감일이 없으므로 '반복이면 마감일 필수'는 템플릿에 적용하지 않는다. 할 일을 만들 때(POST /todos) 검사한다.
- id는 템플릿 목록 안에서 최대 id + 1이다(할 일 id와 별개). 파일은 서버 시작 때 만들지 않고 처음 저장할 때 만든다.
- **Git 제외**: `fastapi-app/task_templates.json`은 사용자 데이터라 `.gitignore`에 넣고 커밋하지 않는다. 다른 경로를 쓰려면 `TEMPLATE_FILE` 환경변수로 지정한다.
- **Docker**: 이미지에는 템플릿 파일을 넣지 않는다. 컨테이너에서는 `TEMPLATE_FILE=/app/data/task_templates.json`(데이터 볼륨 안)에 처음 저장할 때 만들어지고, 컨테이너를 다시 만들어도 유지된다 (아래 "배포 설정").
- 이름 중복(409)은 저장된 목록을 봐야 알 수 있으므로, 404 처리처럼 `check_template_name()`에서 검사한다(아래 코딩 규칙의 '엔드포인트 안 수동 검증 금지'의 허용된 예외).
- 템플릿으로 만든 할 일의 id는 일반 추가와 같은 `next_id()`를 거친다(복구 대기 id와도 겹치지 않는다).
- 추가 폼에 '하위 작업 (한 줄에 하나)' 여러 줄 입력란을 둔다. 템플릿을 불러오면 여기에 채워지고, 템플릿 없이도 쓸 수 있다. 추가하면 POST /todos 뒤 하위 할 일을 차례로 POST한다. 할 일이 만들어진 뒤에는 하위 추가가 실패해도 폼을 비운다(중복 추가 방지).
- 불러오기는 제목·설명·우선순위·반복·태그·하위 작업을 채우고 마감일은 건드리지 않는다. 인라인 수정 폼은 모든 필드를 고치며 한 번에 하나만 열린다.

### 17. 데이터 내보내기 (7단계)
- `GET /todos/export?format=json|csv`, `Content-Disposition: attachment; filename=todos-YYYYMMDD.{json|csv}`. 다른 format은 422.
- JSON은 `GET /todos`와 같은 내용. CSV는 stdlib `csv`, UTF-8 BOM, 고정 헤더, tags는 `;`로 연결, subtasks는 `완료/전체`. `= + - @`로 시작하는 값은 앞에 `'`를 붙인다.
- 항상 **전체 목록**을 내보낸다. UI는 다운로드 링크 2개 (`<a download>`, `api()`를 거치지 않는 유일한 예외).
- `format`은 필수다. 없거나 `json`·`csv`(소문자)가 아니면 422.
- 파일명 날짜는 **한국 시간(UTC+9 고정 오프셋)** 기준이다(tzdata 의존성 없이 계산, 서버 시간대 설정과 무관).
- CSV 헤더는 영문 필드명 `id,title,description,completed,priority,due_date,tags,favorite,recurrence,subtasks,created_at,completed_at` 순서로 고정한다. 서버 내부용 `next_spawned`는 CSV에서 뺀다(JSON에는 그대로 있다).
- CSV 값은 JSON과 같은 표기다: `true`/`false`, `daily` 등, ISO 시각, 없는 값은 빈 칸. 쉼표·따옴표·줄바꿈은 `csv` 모듈이 따옴표로 감싼다.
- 수식 방지 `'`는 CSV에만 붙이고 JSON 값은 바꾸지 않는다.
- 읽기 전용이다: `todo.json`, 템플릿 파일, 삭제 복구 버퍼를 바꾸지 않는다.
- (7.0.0) '전체 목록'은 로그인한 사용자의 전체 목록(지운 대기 항목 제외, `position` 순)이다. 형식·파일명·CSV 규칙은 그대로다.

### 18. 캘린더 뷰 (8단계)
- 서버 변경 없이 클라이언트에서 `allTodos`로 그린다.
- [목록]/[캘린더] 보기 전환, 월 그리드(일요일 시작), [이전]/[다음]/[오늘], 날짜 칸에 그날 마감 항목(최대 3개 + `+N`). 오늘 칸과 기한 초과를 강조한다.
- 검색·상태·태그·즐겨찾기 필터를 그대로 적용한다. 날짜를 누르면 그날 항목 목록을 보여준다.
- 처음 열면 이번 달(Asia/Seoul 날짜, 6.0.0 — v5까지는 브라우저 로컬 날짜)이다. 월 이동은 `new Date(year, month ± 1, 1)`로 연말·윤년을 처리한다. 다른 달로 가면 고른 날짜는 풀린다.
- 그리드 앞뒤의 다른 달 칸은 비워 둔다. `calendar-day` 칸은 그 달 날짜에만 있다.
- 칩(`calendar-chip`): 제목 앞에 ★(즐겨찾기)·🔁(반복), 완료는 취소선, 기한 초과는 빨간색, 우선순위 높음은 굵게. 칸 안 순서는 지금 고른 정렬을 따른다. 칩을 눌러도 그 날짜가 골라진다.
- 그날 목록은 목록 보기와 **같은 항목**(완료 체크, ☆, Edit, Delete·실행 취소, 하위 작업, 선택 체크박스)을 재사용한다. 날짜를 고르기 전에는 비어 있고 안내 문구를 보여준다. 캘린더 전용 추가·수정 기능은 없다.
- 일괄 작업의 '보이는 항목'은 캘린더 보기에서 그날 목록이다. 보기를 바꾸거나 날짜를 바꿔 안 보이게 된 항목은 선택에서 빠진다.
- 보기 상태(목록/캘린더, 보던 달)는 새로고침하면 목록 보기로 돌아간다.

## Scope — 6.0.0 신규

### 19. 단계와 순서
| 단계 | 기능 |
|---|---|
| 1 | 통계 대시보드 + 날짜 기준 Asia/Seoul 통일 |
| 2 | 2열 칸반 보드 |
| 3 | 마감일 알림 센터 (앱 내부) |
| 4 | UI/UX — 테마, 375px 반응형, 접근성 |
| 5 | 품질·배포 준비 (전체 테스트, Docker 빌드, 문서·릴리스 노트) |

- 저장 구조(`todo.json`, `task_templates.json`)는 바꾸지 않는다. 6.0.0은 새 저장 필드가 없다.
- 운영 배포·Jenkins Job 실행은 별도 승인 후에 한다.

### 20. 날짜 기준 (1단계)
- '오늘'과 시각의 날짜는 **Asia/Seoul**이다. 서버는 `KST`(UTC+9 고정 오프셋, 한국은 서머타임이 없어 tzdata 없이 같다), 브라우저는 같은 오프셋으로 계산하는 `kstDate()`/`todayStr()`를 쓴다. 브라우저 시간대와 무관하다.
- 적용 대상: D-day, 기한 초과(목록·통계 바·캘린더), 캘린더의 오늘·처음 달·[오늘], 생성/완료일 표시, 통계 대시보드.
- 시각은 계속 UTC로 저장한다. 시간대가 없는 예전 시각은 UTC로 본다(서버·브라우저 같음).

### 21. 통계 대시보드 (1단계)
- `GET /stats` → 200. 쿼리 파라미터 없음, **읽기 전용**(저장 파일·복구 버퍼를 바꾸지 않는다). 대시보드의 **유일한 집계 원천**이고 화면은 받은 값을 그리기만 한다.
- 집계는 상위 할 일 기준이다(하위 할 일은 세지 않는다). 필터와 상관없이 전체 목록이다.
  - (7.0.0) 로그인한 사용자의 데이터만 집계한다. 응답 형식은 그대로다.
- 응답: `today`, `timezone`(`"Asia/Seoul"`), `total`, `completed`, `active`, `overdue`, `completion_rate`, `daily_completions`(오늘 포함 최근 30일, 오래된 날부터 `{date, count}`), `completions_7d`, `completions_30d`, `trend_excluded`, `active_by_priority`(`높음/보통/낮음` 순서 고정), `due_compliance`(`{eligible, on_time, rate}`).
- 완료율 = 완료 ÷ 전체 × 100, 소수 1자리, 사사오입(ROUND_HALF_UP). 전체 0개면 `0.0`.
- 완료 추이: `completed_at`이 있는 완료 항목만, 완료 시각의 Asia/Seoul 날짜로 센다. `completed_at`이 없는 완료 항목 수는 `trend_excluded`로 보여준다. (`completed_at` 형식이 깨진 데이터는 v5처럼 모델 검증에서 막힌다.)
- 기한 초과: 미완료이고 `due_date` < Asia/Seoul 오늘.
- 마감일 준수율: `due_date`와 `completed_at`이 모두 있는 완료 항목 중 완료일(Asia/Seoul) ≤ `due_date`인 비율. 분모가 0이면 `rate: null`, 화면은 '데이터 없음'.
- 기존 진행률 통계 바(§6)는 **그대로**다: 브라우저 계산, 정수 %, 체크하면 즉시 갱신.
- UI: 보기 전환에 [통계]. 숫자 카드 5개(완료율 `25.0%` 형식), 일별 완료 막대(최근 7일/30일 전환, 막대마다 툴팁), 같은 값의 표 요약(`표로 보기`), 집계 제외 수, 우선순위별 미완료 막대, 준수율, 기준일. 차트 라이브러리·CDN 없이 HTML/CSS로 그린다. 통계 보기에서는 항목 목록과 일괄 작업 바를 숨기고, 목록이 바뀌면(`load()`) 대시보드를 다시 불러온다.

### 22. 칸반 보드 (2단계)
- 열은 '진행 중'(`completed=false`)과 '완료' 2개다. status 필드를 새로 만들지 않는다.
- 카드: 제목, 우선순위, 마감일, 태그, 하위 작업 진행률, 반복 배지, 즐겨찾기.
- 드래그 앤 드롭 + 키보드·터치용 '완료로 이동/진행 중으로 이동' 버튼. 상태 변경은 기존 `PUT /todos/{id}` + `toPayload`로 한다(다른 필드 보존). 완료 시각·반복 생성은 서버 규칙(§3, §15)을 따른다.
- 실패하면 이전 상태로 되돌리고 `#error`를 보여준다. 처리 중인 카드는 중복 요청하지 않는다. 성공하면 `load()`로 목록·캘린더·통계·알림을 함께 갱신한다.
- **새 API 없음.** 서버와 저장 구조는 바뀌지 않는다. 화면의 `TODO_FIELDS`는 서버 `TodoIn` 필드와 같아야 한다(테스트로 확인).
- 보기 전환에 [칸반](목록·캘린더·칸반·통계 순서). 칸반 보기에서는 항목 목록과 일괄 작업 바를 숨긴다(카드는 일괄 선택 대상이 아니다). 다른 보기로 가면 카드를 DOM에서 비운다.
- 카드 순서는 지금 고른 정렬을 따른다. 검색·태그·즐겨찾기 필터는 그대로 적용하고, 상태 필터가 '진행 중'/'완료'이면 반대쪽 열에 '상태 필터로 숨김'을 보여준다. 열 제목 옆에 카드 수 `(N)`.
- 카드 표시: ☆/★ 토글(기존 PUT), 제목(완료는 취소선), `우선순위 X`, `마감 YYYY-MM-DD`, D-day, 기한 초과는 왼쪽 빨간 테두리와 `⚠ 기한 초과` 글자(색만으로 구분하지 않는다), 반복 배지, 하위 진행률, 태그 칩(누르면 태그 필터). 카드 편집·삭제는 목록 보기에서 한다.
- 드래그: 다른 열에서 끌어온, 요청 중이 아닌 카드만 놓을 수 있다(같은 열에 놓으면 요청 없음). 이동 버튼은 항상 보이며 `aria-label`은 `<제목> 완료로 이동`/`<제목> 진행 중으로 이동`. 버튼으로 옮기면 포커스가 옮긴 카드의 버튼을 따라간다. 결과는 `role="status"` 영역에 알린다.
- 낙관적 업데이트: 누르는 즉시 열을 옮기고 카드를 '처리 중'(버튼 비활성, 드래그 불가)으로 둔다.
- 560px 이하에서는 열을 세로로 쌓는다.

### 23. 마감일 알림 센터 (3단계)
- 앱을 열었을 때/실행 중에 보이는 앱 내부 알림만 한다. 미완료이고 마감일이 있는 항목만 대상이다: 오늘+1 '내일 마감', 오늘 '오늘 마감', 오늘 이전 '기한 초과'.
- 알림 키 `(todo_id, due_date, category)`로 중복을 막는다. 읽음 상태는 localStorage(이 브라우저만)에 두고, 지금 유효하지 않은 키는 정리한다(삭제 포함).
- 배지는 읽지 않은 현재 유효 알림 수. 알림에서 해당 할 일로 이동한다. 자정과 탭 재진입 때 다시 계산한다(주기 폴링 없음).
- **서버 변경·새 API·저장 필드 없음.** `allTodos`로 화면에서 계산한다(`computeNotifications(todos, today)`). 필터와 상관없이 전체 목록 기준이고, 날짜는 §20(Asia/Seoul)이다. 그래서 목록·칸반·캘린더의 기한 초과, 통계 바·대시보드와 같은 기준이다.
- 키 문자열은 `<id>|<due_date>|<overdue|today|tomorrow>`. 표시 순서는 기한 초과 → 오늘 → 내일, 같은 분류 안에서는 마감일·id 순.
- 읽음 저장: localStorage `todo.v6.notifications.read` = 읽은 키 배열. 서버·Docker 볼륨에는 저장하지 않는다(브라우저에 있으므로 새로고침·컨테이너 재생성과 무관하게 유지되고, 다른 브라우저·기기와는 공유하지 않는다).
  - (7.0.0, D-8) 키는 사용자별 `todo.v7.notifications.read.<userId>`다. v6 키는 읽지도 지우지도 않는다. 정리 규칙은 그 사용자 키 안에서만 적용한다. 서버에 저장하지 않는 것은 그대로다.
  - 목록을 받은 뒤에만 정리한다(불러오기 실패 시 읽음 기록을 지우지 않는다). 값이 깨졌거나 저장소를 쓸 수 없으면 모두 읽지 않음으로 시작하고, 그 화면 안에서는 읽음이 유지된다.
  - 완료·삭제·마감일 변경·날짜가 지나 분류가 바뀌면 이전 키는 지워지고, 새 분류는 새 알림(읽지 않음)이다. 삭제 후 실행 취소로 되살린 항목도 새 알림이다. 이미 기한 초과인 항목은 날이 바뀌어도 같은 키라 읽음이 유지된다.
- UI: 제목 아래 🔔 버튼(읽지 않은 수 배지, `aria-label` "알림, 읽지 않은 알림 N개", `aria-expanded`), 펼치는 패널(분류 표시, 제목, `마감 YYYY-MM-DD (D-day)`, 읽지 않은 알림은 굵게 + `새 알림` 글자), [모두 읽음], [닫기], 없으면 '알림이 없습니다.'
  - 열면 첫 알림으로 포커스, Esc·[닫기]는 종으로 포커스를 돌려준다. 읽지 않은 수가 바뀌면 `role="status"` 영역에 '읽지 않은 마감일 알림 N개'를 알린다.
  - 알림을 누르면 읽음으로 하고 패널을 닫은 뒤 목록 보기로 가서 그 항목을 강조·포커스한다. 지금 필터에 가려져 있으면 검색·상태·태그·즐겨찾기 필터를 푼다(보이면 그대로 둔다).
- 자정: 다음 Asia/Seoul 자정에 타이머 하나를 걸어 날짜가 바뀌면 알림·D-day·기한 초과·캘린더·통계 바를 다시 그리고(통계 보기면 `/stats`도), 탭 복귀(`visibilitychange`)·창 포커스 때도 날짜를 확인한다.

### 24. UI/UX (4단계)
- 라이트/다크/시스템 테마(기본 시스템), 선택은 localStorage에 저장한다. 색은 `:root` CSS 변수(토큰)로 정의한다.
- 375px 너비에서 가로 스크롤 없이 핵심 조작이 된다. 칸반 열은 좁은 화면에서 세로로 쌓는다.
- `aria-label`, focus visible, 색 외의 상태 표시, 키보드 조작. 기존 v5 기능·버튼을 없애거나 숨기지 않는다.
- 구현 세부:
  - 테마 select(`시스템/라이트/다크`)는 제목 옆에 둔다. localStorage `todo.v6.theme` = `system|light|dark`, 모르는 값·저장소 불가는 시스템이다. 저장할 수 없어도 그 화면에서는 고른 테마가 유지된다.
  - `<head>`의 인라인 스크립트가 그리기 전에 `<html data-theme>`를 정한다(시스템이면 속성 없음 → `prefers-color-scheme`). 다크 토큰은 `@media (prefers-color-scheme: dark) :root:not([data-theme="light"])`와 `:root[data-theme="dark"]` 두 곳에 같은 값으로 둔다. `color-scheme`으로 기본 입력·버튼도 테마를 따른다.
  - 모든 색은 `:root` 토큰이다(그림자 제외). 화면에 보이는 글자는 두 테마 모두 4.5:1 이상, 차트 막대·포커스 표시는 3:1 이상이다(비활성 컨트롤은 WCAG 예외).
  - 다른 탭에서 바꾼 테마와 알림 읽음 표시는 `storage` 이벤트로 이 탭에도 반영한다.
  - 키보드 포커스는 `:focus-visible` 3px 윤곽선. 색 외 표시: 목록·칸반 `⚠ 기한 초과` 글자, 캘린더 기한 초과 칩 앞 `⚠`, 완료는 취소선, 선택된 필터·보기는 `aria-pressed`.
  - 성공 메시지는 `#status-message`(`role="status"`)에 4초 보여준다(추가·수정·체크·일괄 완료/미완료·복구·템플릿·테마). 실패는 기존 `#error`(`role="alert"`)이고, 이때 성공 메시지는 지운다.
  - 560px 이하: 칸반 열 세로 배치, 목록 항목의 Edit/Delete는 다음 줄 오른쪽. 툴바·필터·보기·캘린더 이동 버튼은 줄바꿈된다.
  - 알림 패널이 열린 채 다시 그려지면(자정 등) 포커스를 같은 할 일의 알림 → 같은 자리의 알림 → [닫기] 순으로 돌려준다.

## 배포 설정 (현재 명세)
| 항목 | 값 |
|---|---|
| 서비스 / 컨테이너 이름 | `fastapi-app` / `FastApi-app` (이전 `FastApi-app4`) |
| 포트 | `5001:8000` (이전 `5004:8000`), 컨테이너 내부 8000 고정 |
| 데이터 볼륨 | `fastapi-data` → `/app/data`, 볼륨 이름은 `fastapi_todos_fastapi-data`로 고정 |
| 데이터 경로 | `TODO_FILE=/app/data/todo.json`, `TEMPLATE_FILE=/app/data/task_templates.json` (compose `environment`) |
| 그 밖의 포트 | SonarQube `9000`, Prometheus `127.0.0.1:7070`, Grafana `3000` — 바꾸지 않는다 |

- 이미지에는 데이터 파일을 넣지 않는다. Dockerfile은 `appuser` 소유의 빈 `/app/data`만 만들고, 새 볼륨은 이 소유자를 이어받는다.
- 볼륨이 비어 있으면 `main.py`가 시작할 때 `todo.json`을 `[]`로 만든다. 템플릿 파일은 처음 저장할 때 만든다.
- 볼륨 이름을 고정했으므로 Compose 프로젝트 이름이 달라도 같은 데이터를 쓴다. 그래도 컨테이너 이름이 고정이라, Jenkins와 수동 배포는 **같은 Compose 프로젝트 이름**(`COMPOSE_PROJECT_NAME`)을 써야 이름 충돌이 없다.
- 데이터 이전·백업·복구 절차는 README의 "배포와 데이터 관리"를 따른다. 이전한 데이터를 검증하기 전에는 이전 컨테이너와 백업을 지우지 않는다.
- Prometheus는 `fastapi-app:8000`(서비스명·내부 포트)으로 수집하므로 외부 포트와 무관하다.

### 배포 설정 변경 예정 (7.0.0 — 아직 적용하지 않음)
- 위 표가 **현재 운영 명세**다. v7의 compose·Jenkins 변경안(postgres 서비스, `DATABASE_URL`·`APP_ENV`·`COOKIE_SECURE`·`REGISTRATION_ENABLED`, `alembic upgrade head` 단계)은 `V7_PLAN.md` §P10에 있고, Phase 6 이후 별도 승인을 받아 적용한다.
- 운영 HTTPS가 확인되기 전에는 v7(로그인 기능)을 운영에 배포·공개하지 않는다(`V7_PLAN.md` §P7).
- 전환 후에도 JSON 볼륨 `fastapi_todos_fastapi-data`는 지우거나 쓰지 않는다(롤백용). 전환·백업·롤백 절차는 `V7_PLAN.md` §P6.
- 개발용 PostgreSQL은 개발 PC의 로컬 PostgreSQL 18.6(`127.0.0.1:5433`, DB `todo_dev`·`todo_test`)을 쓴다. Docker를 쓸 수 있는 PC에서는 운영 compose와 분리된 `docker-compose.dev.yml`(프로젝트 `fastapi-v7dev`, `127.0.0.1` 포트)을 선택으로 쓸 수 있다. 운영 서버·운영 compose에는 개발 DB를 두지 않는다.

## Out of Scope
- ~~DB 도입 (SQLite 등), 사용자 인증~~ → (7.0.0) PostgreSQL·사용자 인증은 범위 안이다. SQLite 등 PostgreSQL 외 DB는 계속 범위 밖이다.
- (7.0.0) OAuth·소셜 로그인, 이메일 인증·비밀번호 재설정 메일, 2단계 인증, 비밀번호 변경, 계정 삭제 UI, 관리자 역할, 데이터 공유·협업, 비동기 ORM, v6 롤백 데이터와 v7 데이터의 자동 병합 (`V7_SPEC.md` §34)
- 프론트엔드 프레임워크(React 등)와 빌드 도구
- 서버 측 검색·필터·정렬 쿼리 파라미터 (내보내기의 `format`은 예외), PATCH API
- 이메일·푸시·브라우저 종료 중 알림, 백그라운드 스케줄러, 서버 측 알림 상태·다중 기기 동기화 (6.0.0의 다크 모드와 앱 내부 알림 센터는 범위 안이다)
- 칸반 3열 이상과 새 status 필드, 외부 차트 라이브러리
- "배포 설정"에 적힌 것 외의 배포 구성 변경 (다른 서비스의 포트·컨테이너 이름·볼륨 변경 등)
- 검색 대상에 태그 포함
- 동시 쓰기 잠금과 원자적 저장 — (7.0.0) PostgreSQL 트랜잭션과 사용자 단위 행 잠금으로 처리하므로 v7에서는 범위 안이다. 파일 저장의 잠금은 계속 하지 않는다.
- 삭제 항목의 영구 휴지통(재시작 후 복구), 하위 할 일 ↔ 상위 완료 자동 연동, 하위 할 일 순서 변경
  - (7.0.0) soft delete로 재시작 뒤에도 **30초 안**이면 복구되지만, 30초가 지나면 완전히 지우므로 영구 휴지통은 계속 범위 밖이다.
- 반복 규칙 확장(격주, 요일 지정, 종료일, 횟수), 템플릿 즉시 생성 API
- 데이터 가져오기(import), 캘린더 드래그로 날짜 변경, 주간/일간 보기

## Constraints
- 런타임 의존성(`requirements.txt`)은 fastapi, starlette, uvicorn과 `/metrics`용 prometheus-fastapi-instrumentator·prometheus-client뿐이다. 새로 추가하지 않는다. 테스트 도구는 `requirements-dev.txt`에만 넣는다.
  - (7.0.0, D-1) `sqlalchemy`, `psycopg[binary]`, `alembic`, `argon2-cffi`를 추가한다. 이 밖의 런타임 의존성은 계속 추가하지 않는다.
- 파일 구성을 유지한다: `main.py` 1개, `index.html` 1개. 외부 CDN이나 스크립트를 불러오지 않는다.
  - (7.0.0, D-2) 서버는 `main.py` + `db.py` + `auth.py` + `migrate_json.py` + `alembic/`로 한다. `index.html` 1개와 외부 CDN 금지는 그대로다.
- 기존 API 경로, 메서드, 상태 코드는 v3/v4와 호환되어야 한다. 새 경로만 추가한다.
  - (7.0.0, D-11) 예외: 사용자 데이터 경로는 익명 요청에 401, CSRF 오류에 403, 로그인·가입 시도 초과에 429를 돌려준다. 다른 호환성 변경은 `V7_SPEC.md` §28.4에 적고 승인을 받는다.
- 테스트는 실제 `todo.json`, `task_templates.json`을 건드리면 안 된다.
  - 저장 경로는 환경변수 `TODO_FILE`, `TEMPLATE_FILE`로 바꿀 수 있게 한다.
  - API 테스트는 `tmp_path` + monkeypatch, UI 테스트는 `conftest.py`가 임시 파일로 띄운 uvicorn을 쓴다.
  - `conftest.py`가 세션 종료 시 실제 `todo.json`, `task_templates.json`의 해시(없으면 없는 상태)가 그대로인지 확인한다.
  - (7.0.0) DB 테스트는 `TEST_DATABASE_URL`의 PostgreSQL 테스트 DB에서만 돈다. 고정 대상 `todo_test_user@127.0.0.1:5433/todo_test`(18.x)가 아니면 테스트 세션을 시작하지 않고, 접속 후 실제 서버와 스키마 내용(R-19 게이트: 알려진 리비전 상태·소유자·연결 수 1)을 확인한 뒤에만 스키마를 지운다. 게이트가 있어도 `todo_test` 실행은 실행마다 승인받는다. 운영·개발 DB와 PostgreSQL 16(5432)에 연결하지 않는다. 대상 변경은 사용자 승인 사항이다.
  - (7.0.0) 마이그레이션 왕복(downgrade)은 테스트 DB에서 pytest로만 한다. 다른 DB의 downgrade는 `V7_PLAN.md` §P1.2 승인 절차를 따른다. 비밀번호는 `pgpass.conf`에만 두고 읽거나 출력하지 않는다.
- 배포 서버 테스트(`test_integration.py`)는 `integration` 마커로 분리하고 기본 실행에서 제외한다.
  - (7.0.0) 외부 서버에 POST·DELETE를 보내므로 `pytest -m integration`을 포함해 **명시적 승인 없이 실행하지 않는다.**
- (7.0.0) 운영 데이터 사본(백업 JSON, `pg_dump`, 개발 DB)은 커밋하지 않고, 출력·로그·보고에 내용(제목·설명·태그)을 넣지 않는다. 테스트 데이터는 합성 값만 쓴다(`V7_SPEC.md` §32).
- (7.0.0) 비밀번호·해시·세션 토큰·CSRF 토큰은 로그·응답(정해진 필드 외)에 남기지 않는다. DB 비밀번호는 저장소에 넣지 않는다.
  - (Phase 2-D) 시도 제한용 비밀키 `AUTH_FAILURE_KEY`(base64url, 32바이트 이상)도 같은 비밀값이다: 저장소·`.env.dev.example`·로그·예외·응답에 값을 넣지 않고, 운영은 필수(없으면 시작 거부), 모든 워커가 같은 값을 쓴다. 공개 개발용 대체 키는 `APP_ENV`가 `development`·`test`로 명시된 경우에만 쓴다. `auth_failures`에는 평문 username 대신 이 키의 HMAC 값만 저장한다.
- `tests/`와 `requirements-dev.txt`는 Docker 이미지에 넣지 않는다 (`.dockerignore`).
- 사용자 입력은 계속 `textContent`로 출력한다. 사용자 데이터에 `innerHTML`을 쓰지 않는다.

## Coding Conventions
- **Python**:
  - PEP 8, 타입 힌트를 쓴다.
  - 검증은 Pydantic(`Field`, `Literal`, `field_validator`, `model_validator`)으로 하고, 엔드포인트 안에 수동 if 검증을 넣지 않는다.
  - 기존처럼 짧은 한국어 주석을 붙인다.
- **JS**:
  - `const`/`let`과 2칸 들여쓰기를 쓴다.
  - 기존 `api()`/`run()` 헬퍼로 모든 요청을 처리한다 (내보내기 다운로드 링크만 예외).
  - 우선순위·반복 종류·클라이언트 필드 목록 같은 상수는 한 곳에만 정의한다.
- **테스트용 선택자**: UI 테스트 대상 요소에는 `data-testid`를 붙인다.
  - v4: `todo-item`, `todo-tag`, `tag-filter`, `edit-form`, `edit-save`, `edit-cancel`, `stat-total`, `stat-completed`, `stat-active`, `stat-overdue`, `stat-rate`, `dday`
  - v5: `favorite-toggle`, `favorite-filter`, `undo-toast`, `undo-button`, `bulk-select`, `bulk-bar`, `bulk-select-all`, `bulk-complete`, `bulk-uncomplete`, `bulk-delete`, `subtask-toggle`, `subtask-list`, `subtask-item`, `subtask-add`, `subtask-progress`, `recurrence-badge`, `template-panel`, `template-select`, `template-load`, `template-edit`, `template-delete`, `template-name`, `template-save`, `template-edit-form`, `export-csv`, `export-json`, `view-toggle`, `calendar`, `calendar-day`, `calendar-chip`
  - v6: `view-stats`, `dash-total`, `dash-completed`, `dash-active`, `dash-overdue`, `dash-rate`, `dash-trend`, `dash-trend-summary`, `dash-trend-table`, `dash-excluded`, `dash-priority`, `dash-compliance`, `dash-basis`, `kanban`, `kanban-column`(`data-status="active"|"completed"`), `kanban-count`, `kanban-card`(`data-id`), `kanban-move`, `kanban-status`, `notification-bell`, `notification-badge`, `notification-panel`, `notification-item`(`data-category`, `data-todo-id`), `notification-read-all`, `notification-live` `theme-select`, `status-message`. 목록 항목(`todo-item`)에는 `data-id`가 있다. 칸반 카드 안의 태그·배지·진행률·D-day·즐겨찾기는 목록과 같은 testid(`todo-tag`, `recurrence-badge`, `subtask-progress`, `dday`, `favorite-toggle`)를 쓴다.
  - v7: `auth-panel`, `login-form`, `auth-mode-toggle`, `auth-username`, `auth-password`, `auth-submit`, `auth-error`, `current-user`, `logout`
- (7.0.0) **Python DB**: SQLAlchemy 2.x 스타일(`DeclarativeBase`, `Mapped[]`, `select()`)을 쓴다. 요청마다 세션 1개·트랜잭션 1개이고(예외: 로그인 경로는 선기록 Tx1 → 트랜잭션 밖 Argon2 → 성공 Tx2 또는 보상 Tx로 나눈다 — Argon2 동안 DB 연결·잠금을 잡지 않기 위해, 2026-11-06 승인, `V7_SPEC.md` §29.5), 사용자 데이터 조회는 `db.py`의 소유자 조건 헬퍼로만 한다. 스키마 변경은 Alembic 리비전으로만 한다(앱 시작 시 `create_all` 금지).
- **커밋**: Conventional Commits 형식이다 (`feat:`, `fix:`, `test:`, `docs:`, `chore:`).
  - 기능 커밋 예: `feat: add favorites for Version 5.0.0`

## Definition of Done (DoD)

### 0. 기반 정비 (5.0.0 0단계)
- [x] 0-1. CLAUDE.md를 5.0.0 명세로 고치고 Out of Scope에서 일괄 작업·서브태스크를 뺀다. README를 만든다.
- [x] 0-2. `requirements.txt`에는 fastapi, starlette, uvicorn만 남기고 테스트 도구는 `requirements-dev.txt`로 옮긴다.
- [ ] 0-3. `.dockerignore`에 `tests/`, `requirements-dev.txt`가 있고, 이미지 안에 두 경로가 없다 (`docker run --rm <image> ls`).
- [x] 0-4. 루트 `.gitignore`를 추가하고, 커밋된 `__pycache__/*.pyc`를 추적에서 뺀다.
- [x] 0-5. `pytest`는 integration을 제외하고 통과한다. `pytest -m integration`으로 배포 서버 테스트만 따로 돈다.
- [x] 0-6. 아래 v4 기준 A1~A22, B1~B22가 `test_api.py`, `test_ui.py`로 자동화되어 통과한다.
- [x] 0-7. 모든 PUT이 `toPayload`를 거친다 (체크박스 토글, 수정 폼).
- [x] 0-8. 화면 버전 표시를 `Version 5.0.0`으로 바꾸고 B22와 `/` 응답 테스트를 함께 갱신한다.

### 1. 즐겨찾기
- [x] 1-1. API: 기본값 `false`, PUT으로 바꿀 수 있다. 필드가 없는 v4 데이터도 GET 200, `false`. `"yes"` → 422.
- [x] 1-2. UI: ☆ 클릭 → ★, 새로고침 후에도 유지. 즐겨찾기 필터는 검색·상태·태그와 AND. Edit → 저장해도 `favorite`이 유지된다.

### 2. 삭제 실행 취소
- [x] 2-1. API: DELETE → restore 하면 같은 id·`created_at`·내용이 돌아온다. 두 번 restore하면 404. 없던 id는 404. 최대 id를 지운 뒤 POST한 새 id는 지운 id와 다르다. 30초가 지나면 404.
- [x] 2-2. UI: confirm 수락 → 토스트 → [실행 취소] → 항목과 통계가 복구된다. confirm 취소 시 토스트 없음. 5초 뒤 토스트가 사라진다.

### 3. 일괄 작업
- [x] 3-1. API: complete/uncomplete가 시각 규칙(A14~A16)을 지킨다. delete 후 restore로 한 번에 복구된다(100건도 원래 id·내용·순서 그대로, 101건 restore는 422). 없는 id가 섞이면 404이고 파일은 그대로다. 빈 ids, 잘못된 action → 422.
- [x] 3-2. UI: 선택 개수 표시, 전체 선택은 보이는 항목만, 일괄 완료 후 통계 즉시 갱신, 일괄 삭제 → confirm → 토스트 → 실행 취소로 전부 복구.

### 4. 하위 할 일
- [x] 4-1. API: 추가 201, 수정 200, 삭제 204, 없는 todo/sid 404, 공백·101자 제목 422, 21번째 추가 422. 상위 PUT 본문의 `subtasks`는 무시된다. 상위 `completed`는 하위와 무관하다.
- [x] 4-2. UI: 추가/체크/제목 수정/삭제(confirm 수락 시만)가 반영되고 새로고침 후에도 유지된다. 진행률 `1/2 (50%)`, 0개면 숨김. 상위 통계는 바뀌지 않는다.

### 5. 반복 일정
- [x] 5-1. API: 반복 + 마감일 없음 → 422. daily/weekly/monthly 완료 시 다음 마감일 항목 생성. 1/31 monthly → 2/28(윤년 2/29). 완료 취소 후 재완료해도 추가 생성 없음. 하위는 미완료로 복사. bulk complete와 `completed:true` POST에서도 동작.
- [x] 5-2. UI: 반복 select(추가·수정 폼), 배지, 체크하면 다음 일정이 목록에 나타난다.

### 6. 작업 템플릿
- [x] 6-1. API: CRUD 상태 코드(201/200/204/404), 이름 1~50자, 태그 규칙은 A8~A10과 같다. 테스트는 `TEMPLATE_FILE`을 `tmp_path`로 둔다.
- [x] 6-2. UI: 현재 폼 → 템플릿 저장 → select에 표시, 불러오기 → 추가 폼과 반복 select가 채워지고 추가하면 하위 할 일까지 생성된다. 수정·삭제(confirm) 동작, `prompt()` 없음.

### 7. 데이터 내보내기
- [x] 7-1. API: JSON은 `GET /todos`와 같고 파일명이 `.json`. CSV는 BOM, 고정 헤더, 행 수 = 항목 수, 한글·쉼표·줄바꿈 값이 `csv.reader`로 복원된다. `=SUM(1)` 제목은 `'`로 무력화된다. `format=xml` → 422.
- [x] 7-2. UI: 버튼 클릭 시 다운로드되는 파일명과 내용이 맞다 (`expect_download`).

### 8. 캘린더 뷰
- [x] 8. UI (시계 고정): 이번 달 그리드와 오늘 강조, 마감 항목이 해당 `calendar-day[data-date]`에 표시, 이전/다음 달 이동, 마감일 없는 항목 미표시, 필터 반영, 날짜 클릭 시 그날 목록, 목록 보기로 돌아오면 원래 화면, 통계 불변.

### 6.0.0 단계별 DoD
- [x] 1-1. API `/stats`: 빈 목록, 완료율 1자리·사사오입, 기한 초과(KST 자정), 추이 자정 경계·7/30일 창 끝, 완료일 누락(`trend_excluded`), 시간대 없는 시각(UTC), 우선순위 분포, 준수율(당일·분모 0), 하위 할 일 무시, v1~v3 데이터, 읽기 전용.
- [x] 1-2. 날짜 기준: 브라우저 시간대가 달라도(LA) D-day·기한 초과·캘린더 오늘·처음 달·생성/완료일이 Asia/Seoul 날짜. 기존 v5 날짜 테스트(B15~B17, B21, 캘린더) 통과.
- [x] 1-3. UI 대시보드: 카드·추이(7/30일)·표 요약·제외 수·우선순위·준수율('데이터 없음')이 `/stats`와 같고, 기존 통계 바는 정수 %, 필터 무관, 변경 후 갱신, 실패 시 `#error`.
- [x] 2-1. API(칸반 계약): `TODO_FIELDS` = `TodoIn` 필드, `toPayload`식 PUT으로 옮겨도 다른 필드·하위 할 일·`created_at` 보존, 완료 시각 규칙, 반복 항목은 다음 일정 1회만, 없는 id 404.
- [x] 2-2. UI 칸반: 2열·카드 내용, 드래그 양방향·같은 열 무시, 키보드 이동과 포커스, 실패 롤백(드래그·버튼), 동시 클릭 PUT 1회, 반복 다음 카드, 필터·정렬, 다른 보기(목록·캘린더·통계)와 일치, 375px 터치(세로 배치, 가로 스크롤 없음).
- [x] 3-1. API: 라우트가 v5 + `/stats` 그대로(알림 경로 없음), 응답·저장 필드에 알림 필드 없음, 본문에 섞인 읽음 값은 저장되지 않음.
- [x] 3-2. UI 알림: 분류·순서·배지, 경계(연말·윤년·월말, 완료·마감일 없음·이틀 뒤 제외, 중복 키), 읽음 유지(새로고침), 재렌더 중복 없음, 완료·삭제 정리와 실행 취소, 마감일 변경 시 새 알림, 자정 넘김(가짜 시계), 탭 복귀, 항목으로 이동(가린 필터 해제), 키보드·스크린리더, 빈 상태, 깨진/막힌 저장소, 불러오기 실패 시 읽음 보존, 목록·칸반·캘린더·통계 바·대시보드와 같은 기준, 375px 터치.
- [x] 4. 테마(시스템 기본·선택 유지·그리기 전 적용·저장소 불가·탭 간 반영), 모든 보기 라이트/다크 명도 대비, 375px 모든 보기 가로 스크롤 없음, 키보드 조작·포커스 표시, 색 외 상태 표시, 성공/실패 메시지, 자정 재렌더 시 알림 포커스 유지.
- [ ] 5. 전체 pytest, Docker 이미지 빌드·실행, README/CLAUDE.md/릴리스 노트.
  - [x] 5-1. 전체 pytest 332 passed, 3 deselected. 런타임 의존성(`requirements.txt`)만 설치한 환경에서 `main.py`+`templates/`로 실행해 `/`·`/stats`·`/metrics`·POST·내보내기와 v3/v5 데이터 읽기, 빈 데이터 폴더의 `todo.json` 생성 확인.
  - [x] 5-2. `RELEASE_NOTES.md`, README 진행 상황·"6.0.0 배포 전 확인" 절차.
  - [ ] 5-3. 로컬 Docker 이미지 빌드, 이미지 내용(0-3), 격리된 Compose 프로젝트에서 실행·재생성 후 데이터 유지(C2, C5). 로컬 Docker 데몬이 꺼져 있어 미검증.

### 7.0.0 단계별 DoD (상세 기준은 `V7_PLAN.md` 각 Phase)
- [x] 0. 분석·설계: v6 기준 `332 passed, 3 deselected` 확인(2026-11-05), `V7_SPEC.md`·`V7_PLAN.md` 작성, 이 문서에 v7 변경 표시.
- [x] 1. 개발용 PostgreSQL(로컬 18.6 `127.0.0.1:5433`, Docker compose는 선택), `db.py`·ORM, Alembic `0001` 왕복·모델 일치, 테스트 대상 외 DB 거부, 기존 테스트 유지. (2026-11-05 완료. **v7.0.0 전체는 미완료** — Phase 2~6이 남았고 앱은 아직 JSON 저장을 쓴다)
  - [x] 1-A. (Docker 없이) ORM 6개 테이블, `db.py` 연결·세션, Alembic 설정·`0001`, 개발 compose·예시 env 작성(미실행), 테스트 DB 보호, 오프라인 스키마 테스트(`tests/test_db_schema.py`), 기존 테스트 유지.
  - [x] 1-A 보강. 테스트 DB 보호를 고정 대상(`todo_test_user@127.0.0.1:5433/todo_test`, 18.x)으로 좁히고 접속 후 서버 확인을 `DROP SCHEMA` 앞에 둠, 비테스트 DB downgrade 차단(확인값 2개 + `V7_PLAN.md` §P1.2 승인 절차), 제약 위반 테스트 SAVEPOINT, 오프라인 거부 사례 테스트.
  - [x] 1-B. (로컬 PostgreSQL 18.6, `127.0.0.1:5433`) `todo_test`에서 `tests/test_db_postgres.py` 실행: 서버 확인, upgrade·downgrade 왕복, autogenerate 비교, 제약 동작, UTC 세션, 시퀀스 이동, cascade. (선택·승인 후) `todo_dev`에 `alembic upgrade head`. Docker 개발 compose 기동·이미지 태그 확정은 Docker를 쓸 수 있는 환경에서 하는 선택 항목이다(미실행).
    - 근거 (2026-11-05): PostgreSQL 통합 11 passed, 전체 pytest **424 passed, 3 deselected**(v6 332 + DB 오프라인 81 + PostgreSQL 11). `todo_dev`에 `0001` upgrade head 성공 — 테이블 7·IDENTITY 시퀀스 4·인덱스 14·CHECK 5·FK 5, `alembic_version` = `0001`, 나머지 테이블 0행.
    - 후속 사항(`V7_PLAN.md` R-16~R-20): Windows 로캘과 C.UTF-8 차이(`lower(name)` 검증), 5433 `listen_addresses`·`pg_hba.conf`·방화벽 점검, `todo_test` public 스키마 소유자·ACL 변경, 테스트 재실행 전 사전 점검 기준, Python 3.13/Docker 검증.
- [ ] 2. 회원가입·로그인·로그아웃, Argon2id, 쿠키 속성, 14일 만료, CSRF·Origin, **로그인 시도 제한(DB 기반, 다중 워커)**, 운영 시작 가드, 로그인 UI.
  - [x] 2-B. DB 없는 순수 인증 함수(`auth.py`)와 단위 테스트(`tests/test_auth_unit.py` 191 passed, 전체 604 passed·1 skipped·3 deselected, 2026-11-05, 로컬 커밋 `b4d3ad4`). API·화면에는 아직 연결하지 않았다.
  - [x] 2-A. `auth_failures` 모델·Alembic `0002`, PostgreSQL 테스트 삭제 전 스키마 게이트(R-19, 연결 수 교차 확인). DB 없는 회귀 669 passed·1 skipped·3 deselected, `todo_test` PostgreSQL 16 passed·PostgreSQL 포함 회귀 685 passed·3 deselected(2026-11-05). `todo_dev`는 2026-11-06 Alembic 0002 적용 완료(2-E 직전 별도 승인, 세션 한정 타임아웃, 적용 후 8/5/18/16·모델과 차이 없음·사용자 데이터 0행, `V7_PLAN.md` §P2 2-A).
  - [x] 2-C. `db.py` 로그인 세션 저장·조회·삭제·만료 정리(SHA-256만 저장, 14일 고정, 재로그인은 제시된 세션 하나만 교체, commit 은 호출자), 엔진 `hide_parameters`. DB 없는 회귀 749 passed·1 skipped·3 deselected, `todo_test` PostgreSQL 27 passed·PostgreSQL 포함 회귀 776 passed·3 deselected, 종료 후 `todo_test` 0002·사용자 데이터 0행. Phase 2-C 구현·검증 완료(2026-11-06). `require_user`는 2-E. R-27(오류 DETAIL의 `token_hash`)은 미해결.
  - [x] 2-D. 시도 제한(D-12 채택, 선기록 방식, HMAC username_key, IPv6 /64, Argon2 슬롯, 503) 순수·DB 함수 — **구현·검증 완료(2026-11-06), 로컬 커밋 `7e3b80e`**. DB 없는 `tests/test_auth_limits.py` 128 passed, `todo_test` PostgreSQL 단독 **38 passed**, PostgreSQL 포함 전체 회귀 **915 passed·3 deselected**(실패 0·skip 0), 종료 후 `todo_test` 0002·R-19 통과·사용자 데이터 7개 표 0행. 정책은 `V7_SPEC.md` §29.5(변경 기록 포함), 수치·횟수는 `V7_PLAN.md` §P2. HTTP 연결(429·500·503 응답, 가입 409 집계, 시작 가드 등)은 2-E이고 미완료다. R-26·R-27·R-31은 열려 있다.
  - [x] 2-E. `/auth/register`·`/auth/login`·`/auth/logout`·`/auth/me`, 시작 가드(lifespan), 로그인 Tx1 → 연결 없는 Argon2 → Tx2·보상 Tx, 가입 409 commit(SAVEPOINT, `uq_users_username`만 409), 429·500·503·`Retry-After`, 인증 422 입력값 제거, `request.client.host` IP — **구현·검증 완료(2026-11-06), 로컬 커밋 `bbe5227`**. DB 없는 `tests/test_auth_routes.py` 87 passed, `todo_test` PostgreSQL 단독 **50 passed**(HTTP 12개 추가), PostgreSQL 포함 전체 회귀 **1015 passed·3 deselected**(실패 0·skip 0), 종료 후 `todo_test` 0002·R-19 통과·사용자 데이터 7개 표 0행. 실제 PostgreSQL 오류 DETAIL의 응답·앱 로그 비노출 확인. 계약은 `V7_SPEC.md` §28.1, 수치는 `V7_PLAN.md` §P2. R-12·R-19·R-26·R-27·R-30·R-31은 열려 있다.
  - [x] 2-F. 다중 워커 간 시도 제한 공유 검증(테스트만 추가, 운영 코드·워커 수 설정 변경 없음) — **검증 완료(2026-11-06), 로컬 커밋 대기**. 독립 uvicorn 단일 워커 프로세스 2개(A1~A4: 사용자·IP 제한 공유, 동시 10개 401 정확히 5·429 정확히 5, 세션·CSRF 공유)와 실제 `uvicorn --workers 2`(B1~B3: 워커 2개 확인, 요청 분산 관찰, 서로 다른 워커의 실패 합산). `todo_test` PostgreSQL 단독 **55 passed**, PostgreSQL 포함 전체 회귀 **1020 passed·3 deselected**, 종료 후 사용자 데이터 0행·테스트용 프로세스 잔존 0. 수치·실행 이력은 `V7_PLAN.md` §P2. R-12·R-19·R-28·R-31은 열려 있다.
  - [ ] 2-G(로그인 UI) — `V7_PLAN.md` §P2. Phase 2 전체는 미완료, 원격 push는 보류.
- [ ] 3. 15개 데이터 경로 인증 필수, 익명 401·타 사용자 404 매트릭스, bulk·restore 격리, 템플릿 이름 사용자별, soft delete·position, 동시 요청.
- [ ] 4. v6 기능 전체를 DB·인증 위에서 통과(기존 테스트는 픽스처만 교체, 변경 목록은 `V7_PLAN.md` §P8.3), 사용자별 알림 키, 두 사용자 UI 격리, `Version 7.0.0`.
- [ ] 5. `migrate_json.py` import·verify·export-v6·diff, 백업 사본 리허설 2회, 원본 해시 불변, `pg_dump` 복원 검증, 내용 미출력.
- [ ] 6. 전체 pytest, 이미지 빌드·격리 Compose 실행, OWASP 재점검, README·릴리스 노트, compose·Jenkins 변경안. 운영 공개는 HTTPS 게이트(`V7_PLAN.md` §P7)와 별도 승인 후.

## 회귀 기준 (v4.0.0) — 5.0.0의 모든 단계에서 계속 통과해야 한다

### A. API — pytest (`tests/test_api.py`)
**생성 / 검증**
- [x] A1. 정상 POST → 201. 응답에 `id`, `tags`, `created_at`(파싱 가능한 ISO 8601)이 있고 `completed_at`은 `null`이다.
- [x] A2. `title: "   "` → 422
- [x] A3. `title: "  과제  "` → 저장된 값은 `"과제"`
- [x] A4. `title` 101자 → 422
- [x] A5. `priority: "긴급"` → 422. `높음`, `보통`, `낮음`은 각각 201
- [x] A6. `due_date: "2026-02-30"`, `"2026/10/05"`, `"내일"` → 각각 422
- [x] A7. `due_date: ""`와 `due_date: null` → 201, 저장된 값은 `null`
- [x] A8. `tags: ["학교", " 과제 ", "학교"]` → `["학교", "과제"]`
- [x] A9. `tags: ["Work", "work"]` → `["Work"]`
- [x] A10. 빈 태그 `[""]`, 21자 태그, 쉼표가 든 태그 `["a,b"]`, 태그 11개 → 각각 422
- [x] A11. 클라이언트가 보낸 `created_at`, `completed_at`은 무시된다
- [x] A12. `completed: true`로 생성하면 `completed_at`이 채워진다

**수정 / 시각 규칙**
- [x] A13. PUT 후에도 `created_at`이 바뀌지 않는다
- [x] A14. 미완료 → 완료 PUT이면 `completed_at`이 채워진다
- [x] A15. 완료 → 미완료 PUT이면 `completed_at`이 `null`이 된다
- [x] A16. 완료 상태 그대로 제목만 수정하면 `completed_at`이 유지된다
- [x] A17. PUT에도 A2~A10과 같은 검증이 적용된다
- [x] A18. 없는 id에 PUT/DELETE → 404

**호환 / 회귀**
- [x] A19. v3 형식 데이터(`priority`, `due_date`, `tags`, 시각 필드 없음, `due_date: ""`)를 담은 파일로 GET → 200, 기본값이 채워진다
- [x] A20. CRUD 전체 흐름: 생성 → 조회 → 수정 → 삭제 → 조회 시 없음
- [x] A21. 새 id는 기존 최대 id + 1이다
- [x] A22. 테스트를 실행한 뒤에도 실제 `fastapi-app/todo.json` 내용이 바뀌지 않는다

### B. UI — Playwright (`tests/test_ui.py`)
**태그**
- [x] B1. 추가 폼에 `학교, 과제`를 넣고 추가하면 항목에 `todo-tag` 칩 2개가 보인다
- [x] B2. `tag-filter`에서 태그를 고르면 그 태그가 있는 항목만 보인다
- [x] B3. 태그 칩을 클릭해도 같은 필터가 적용된다
- [x] B4. 태그 필터 + 검색어 + 상태 필터가 AND로 함께 적용된다

**수정 폼**
- [x] B5. Edit을 누르면 `edit-form`이 열리고, 5개 필드에 현재 값이 채워져 있다
- [x] B6. 값을 바꾸고 `edit-save`를 누르면 폼이 닫히고 목록에 변경이 반영된다 (새로고침 후에도 유지)
- [x] B7. 값을 바꾸고 `edit-cancel`을 누르면 원래 값이 그대로 보인다
- [x] B8. 제목을 공백으로 저장하면 폼이 열린 채 `#error`가 보인다
- [x] B9. 수정하는 동안 `prompt` 대화상자가 한 번도 뜨지 않는다 (dialog 이벤트로 확인)
- [x] B10. 다른 항목의 Edit을 누르면 이전 수정 폼은 닫힌다

**통계**
- [x] B11. 항목 4개(완료 1, 기한 초과 1)일 때:
  - `stat-total`=4, `stat-completed`=1, `stat-active`=3, `stat-overdue`=1, `stat-rate`=25%
- [x] B12. 체크박스를 토글하면 통계가 즉시 갱신된다
- [x] B13. 필터를 바꿔도 통계 값은 바뀌지 않는다
- [x] B14. 항목이 0개면 완료율이 0%다

**D-day / 시각**
- [x] B15. 브라우저 시계를 고정하고 확인한다 (Playwright clock):
  - 마감일이 +3일이면 `D-3`, 오늘이면 `오늘 마감`, -2일이면 `D+2`
- [x] B16. 완료된 항목과 마감일 없는 항목에는 `dday`가 없다
- [x] B17. 항목에 생성일이 보이고, 완료하면 완료일이 나타나고, 완료를 취소하면 사라진다

**회귀**
- [x] B18. 추가, 체크 토글, 삭제(confirm 수락/취소) 동작
- [x] B19. 검색(제목·설명)과 상태 필터 3종 동작. 선택된 필터 버튼이 시각적으로 구분된다
- [x] B20. 우선순위순 정렬(높음 → 보통 → 낮음)과 마감일순 정렬(마감일 없는 항목은 맨 뒤)
- [x] B21. 기한 초과 미완료 항목에 `overdue` 스타일과 `⚠ 기한 초과`가 표시된다
- [x] B22. 페이지에 현재 버전(`Version 6.0.0`)이 표시된다 (v5 당시 `Version 5.0.0`, v4 당시 `Version 4.0.0`)

### C. 공통
- [ ] C1. `pytest`가 모두 통과한다 (A, B, 5.0.0 단계별 테스트 전체).
- [ ] C2. `docker compose up --build` 후 `http://localhost:5001`에서 화면이 뜨고 5.0.0 기능 8개가 동작한다.
- [ ] C3. Out of Scope 항목이 구현되지 않았고, 기존 API 경로와 상태 코드가 v3/v4와 같다.
- [ ] C4. 단계별로 Conventional Commits 커밋이 되어 있다.
- [ ] C5. `docker compose up -d --force-recreate fastapi-app`로 컨테이너를 다시 만들어도 할 일과 템플릿이 유지된다.
