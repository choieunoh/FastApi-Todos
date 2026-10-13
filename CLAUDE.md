# CLAUDE.md — FastAPI To-Do List

## Purpose
FastAPI와 바닐라 JS로 만든 To-Do 웹 앱이다.
이 문서는 Version 5.0.0 "할 일 관리 기능 확장"의 개발 명세다.
구현과 리뷰는 이 문서를 기준으로 하며, 문서에 없는 기능은 추가하지 않는다.
5.0.0은 기능별 단계(0~8)로 나누어 구현한다. 각 단계는 앞 단계의 테스트를 모두 통과한 상태에서 시작한다.

## Current Version
**4.0.0** — CRUD, 검색, 상태 필터, 우선순위·마감일, 정렬, 기한 초과 표시, 태그, 단일 수정 폼, 생성/완료 시각, 진행률 통계, D-day, 서버 검증

## Target Version
**5.0.0** — 즐겨찾기, 삭제 실행 취소, 일괄 작업, 하위 할 일, 반복 일정, 작업 템플릿, 데이터 내보내기(CSV·JSON), 캘린더 뷰

## Technology Stack
| 구분 | 기술 |
|---|---|
| Backend | Python 3.13 (Docker), FastAPI ≥ 0.142, Starlette ≥ 1.7, Pydantic v2, Uvicorn ≥ 0.54 |
| Frontend | `fastapi-app/templates/index.html` 단일 파일 (HTML + CSS + 바닐라 JS, 빌드 없음) |
| Storage | `fastapi-app/todo.json`, `fastapi-app/task_templates.json` (JSON 파일, 후자는 5.0.0 6단계) |
| Test | pytest, FastAPI `TestClient`(httpx2), UI는 Playwright(pytest-playwright) |
| Deploy | Docker (`python:3.13-slim`), docker-compose (포트 `5004:8000`) |

## Project Structure
```
FastApi_Todos/
├── CLAUDE.md
├── README.md
├── .gitignore
├── docker-compose.yml
└── fastapi-app/
    ├── main.py                 # API + 모델 + 저장 로직
    ├── templates/index.html    # UI 전체
    ├── todo.json               # 데이터
    ├── requirements.txt        # 런타임 의존성 (fastapi, starlette, uvicorn 만)
    ├── requirements-dev.txt    # 테스트 의존성
    ├── pyproject.toml          # pytest 설정 (integration 마커)
    ├── Dockerfile, .dockerignore, sonar-project.properties
    └── tests/
        ├── conftest.py         # 실제 todo.json 보호, UI 테스트용 임시 서버
        ├── test_api.py         # TestClient (A, 5.0.0 API)
        ├── test_ui.py          # Playwright (B, 5.0.0 UI)
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

### 5. API (경로와 상태 코드는 v3와 같다)
| 메서드 | 경로 | 동작 |
|---|---|---|
| GET | `/todos` | 전체 목록 |
| POST | `/todos` | 생성, 201 반환 |
| PUT | `/todos/{id}` | 수정, 없으면 404 |
| DELETE | `/todos/{id}` | 삭제, 204 반환, 없으면 404 |
| GET | `/` | index.html |

### 6. UI
- **버전 표시**: `Version 5.0.0` (5.0.0 개발을 시작하면서 미리 바꿨다. v4 당시에는 `Version 4.0.0`)
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
  - 날짜 계산은 브라우저 로컬 날짜 기준이다.
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
- UI: 삭제 확인(`confirm`)은 **유지**한다. 삭제 성공 후 토스트 "N개 삭제됨 [실행 취소]"를 5초 동안 보여준다. 새 삭제가 일어나면 토스트를 교체한다.

### 13. 일괄 작업 (3단계)
- `POST /todos/bulk` `{ids:[int](1~100개, 중복 제거), action:"complete"|"uncomplete"|"delete"}` → 200 `{updated:[Todo], deleted:[id]}`.
- 없는 id가 하나라도 있으면 404이고 파일은 바뀌지 않는다. 시각 규칙(§3)과 반복 규칙(§15)을 그대로 따른다.
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
  - 복사: title, description, priority, tags, favorite, recurrence, subtasks(모두 미완료, sid 새로).
  - 원본은 `next_spawned=true`가 되어, 완료를 취소했다가 다시 완료해도 또 만들지 않는다.
- UI: 추가 폼·수정 폼에 반복 select(`반복 없음/매일/매주/매월`), 반복 항목에만 `🔁 매주` 같은 배지. 다음 일정 생성 여부는 따로 표시하지 않는다.

### 16. 작업 템플릿 (6단계)
- 저장소 `task_templates.json` (환경변수 `TEMPLATE_FILE`). 필드: `id, name(1~50), title, description, priority, tags, recurrence, subtasks: list[str]`. 검증은 Todo와 같은 규칙을 공유한다.
- `GET/POST /task-templates`, `PUT/DELETE /task-templates/{id}` (201/200/204/404).
- 재사용은 **추가 폼에 값을 채우는 방식**이다. 생성은 기존 `POST /todos` + 하위 할 일 POST로 한다 (apply API 없음).
- UI: 템플릿 select, [불러오기], [현재 폼을 템플릿으로 저장](이름 입력), 인라인 수정, 삭제(`confirm`). `prompt()`를 쓰지 않는다.

### 17. 데이터 내보내기 (7단계)
- `GET /todos/export?format=json|csv`, `Content-Disposition: attachment; filename=todos-YYYYMMDD.{json|csv}`. 다른 format은 422.
- JSON은 `GET /todos`와 같은 내용. CSV는 stdlib `csv`, UTF-8 BOM, 고정 헤더, tags는 `;`로 연결, subtasks는 `완료/전체`. `= + - @`로 시작하는 값은 앞에 `'`를 붙인다.
- 항상 **전체 목록**을 내보낸다. UI는 다운로드 링크 2개 (`<a download>`, `api()`를 거치지 않는 유일한 예외).

### 18. 캘린더 뷰 (8단계)
- 서버 변경 없이 클라이언트에서 `allTodos`로 그린다.
- [목록]/[캘린더] 보기 전환, 월 그리드(일요일 시작), [이전]/[다음]/[오늘], 날짜 칸에 그날 마감 항목(최대 3개 + `+N`). 오늘 칸과 기한 초과를 강조한다.
- 검색·상태·태그·즐겨찾기 필터를 그대로 적용한다. 날짜를 누르면 그날 항목 목록을 보여준다.

## Out of Scope
- DB 도입 (SQLite 등), 사용자 인증
- 프론트엔드 프레임워크(React 등)와 빌드 도구
- 서버 측 검색·필터·정렬 쿼리 파라미터 (내보내기의 `format`은 예외), PATCH API
- 다크 모드, 알림
- Docker volume 등 배포 구성 변경, 포트·컨테이너 이름 변경
- 검색 대상에 태그 포함
- 동시 쓰기 잠금과 원자적 저장
- 삭제 항목의 영구 휴지통(재시작 후 복구), 하위 할 일 ↔ 상위 완료 자동 연동, 하위 할 일 순서 변경
- 반복 규칙 확장(격주, 요일 지정, 종료일, 횟수), 템플릿 즉시 생성 API
- 데이터 가져오기(import), 캘린더 드래그로 날짜 변경, 주간/일간 보기

## Constraints
- 런타임 의존성(`requirements.txt`)을 추가하지 않는다. 테스트 도구는 `requirements-dev.txt`에만 넣는다.
- 파일 구성을 유지한다: `main.py` 1개, `index.html` 1개. 외부 CDN이나 스크립트를 불러오지 않는다.
- 기존 API 경로, 메서드, 상태 코드는 v3/v4와 호환되어야 한다. 새 경로만 추가한다.
- 테스트는 실제 `todo.json`, `task_templates.json`을 건드리면 안 된다.
  - 저장 경로는 환경변수 `TODO_FILE`, `TEMPLATE_FILE`로 바꿀 수 있게 한다.
  - API 테스트는 `tmp_path` + monkeypatch, UI 테스트는 `conftest.py`가 임시 파일로 띄운 uvicorn을 쓴다.
  - `conftest.py`가 세션 종료 시 실제 `todo.json`의 해시가 그대로인지 확인한다.
- 배포 서버 테스트(`test_integration.py`)는 `integration` 마커로 분리하고 기본 실행에서 제외한다.
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
  - v5: `favorite-toggle`, `favorite-filter`, `undo-toast`, `undo-button`, `bulk-select`, `bulk-bar`, `bulk-select-all`, `bulk-complete`, `bulk-uncomplete`, `bulk-delete`, `subtask-toggle`, `subtask-list`, `subtask-item`, `subtask-add`, `subtask-progress`, `recurrence-badge`, `template-panel`, `template-select`, `export-csv`, `export-json`, `view-toggle`, `calendar`, `calendar-day`
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
- [ ] 6-1. API: CRUD 상태 코드(201/200/204/404), 이름 1~50자, 태그 규칙은 A8~A10과 같다. 테스트는 `TEMPLATE_FILE`을 `tmp_path`로 둔다.
- [ ] 6-2. UI: 현재 폼 → 템플릿 저장 → select에 표시, 불러오기 → 추가 폼과 반복 select가 채워지고 추가하면 하위 할 일까지 생성된다. 수정·삭제(confirm) 동작, `prompt()` 없음.

### 7. 데이터 내보내기
- [ ] 7-1. API: JSON은 `GET /todos`와 같고 파일명이 `.json`. CSV는 BOM, 고정 헤더, 행 수 = 항목 수, 한글·쉼표·줄바꿈 값이 `csv.reader`로 복원된다. `=SUM(1)` 제목은 `'`로 무력화된다. `format=xml` → 422.
- [ ] 7-2. UI: 버튼 클릭 시 다운로드되는 파일명과 내용이 맞다 (`expect_download`).

### 8. 캘린더 뷰
- [ ] 8. UI (시계 고정): 이번 달 그리드와 오늘 강조, 마감 항목이 해당 `calendar-day[data-date]`에 표시, 이전/다음 달 이동, 마감일 없는 항목 미표시, 필터 반영, 날짜 클릭 시 그날 목록, 목록 보기로 돌아오면 원래 화면, 통계 불변.

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
- [x] B22. 페이지에 현재 버전(`Version 5.0.0`)이 표시된다 (v4 당시 기준은 `Version 4.0.0`)

### C. 공통
- [ ] C1. `pytest`가 모두 통과한다 (A, B, 5.0.0 단계별 테스트 전체).
- [ ] C2. `docker compose up --build` 후 `http://localhost:5004`에서 화면이 뜨고 5.0.0 기능 8개가 동작한다.
- [ ] C3. Out of Scope 항목이 구현되지 않았고, 기존 API 경로와 상태 코드가 v3/v4와 같다.
- [ ] C4. 단계별로 Conventional Commits 커밋이 되어 있다.
