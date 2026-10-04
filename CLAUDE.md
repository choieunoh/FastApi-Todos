# CLAUDE.md — FastAPI To-Do List

## Purpose
FastAPI와 바닐라 JS로 만든 To-Do 웹 앱이다.
이 문서는 Version 4.0.0 "Todo 관리 UX 및 데이터 모델 개선"의 개발 명세다.
구현과 리뷰는 이 문서를 기준으로 하며, 문서에 없는 기능은 추가하지 않는다.

## Current Version
**3.0.0** — CRUD, 검색, 상태 필터(전체/진행 중/완료), 우선순위·마감일, 우선순위순/마감일순 정렬, 기한 초과 표시

## Target Version
**4.0.0** — 태그, 단일 수정 폼, 생성/완료 시각, 진행률 통계, D-day, 서버 검증 강화

## Technology Stack
| 구분 | 기술 |
|---|---|
| Backend | Python 3.13, FastAPI 0.141.1, Pydantic v2, Uvicorn 0.53.0 |
| Frontend | `fastapi-app/templates/index.html` 단일 파일 (HTML + CSS + 바닐라 JS, 빌드 없음) |
| Storage | `fastapi-app/todo.json` (JSON 파일) |
| Test | pytest, FastAPI `TestClient`(httpx), UI는 Playwright(pytest-playwright) |
| Deploy | Docker (`python:3.13-slim`), docker-compose (포트 `5004:8000`) |

## Project Structure
```
FastApi_Todos-deploy/
├── CLAUDE.md
├── docker-compose.yml
└── fastapi-app/
    ├── main.py               # API + 모델 + 저장 로직
    ├── templates/index.html  # UI 전체
    ├── todo.json             # 데이터
    ├── requirements.txt      # 런타임 의존성
    ├── requirements-dev.txt  # 테스트 의존성 (4.0.0 신규)
    ├── Dockerfile
    └── tests/                # 4.0.0 신규
        ├── test_api.py
        └── test_ui.py
```

## Scope

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
- **버전 표시**: `Version 4.0.0`
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

## Out of Scope
- DB 도입 (SQLite 등), 사용자 인증
- 프론트엔드 프레임워크(React 등)와 빌드 도구
- 서버 측 검색·필터·정렬 쿼리 파라미터, PATCH API
- 일괄 작업, 서브태스크, 다크 모드, 알림
- Docker volume 등 배포 구성 변경, 포트·컨테이너 이름 변경
- 검색 대상에 태그 포함
- 동시 쓰기 잠금과 원자적 저장

## Constraints
- 런타임 의존성(`requirements.txt`)을 추가하지 않는다. 테스트 도구는 `requirements-dev.txt`에만 넣는다.
- 파일 구성을 유지한다: `main.py` 1개, `index.html` 1개. 외부 CDN이나 스크립트를 불러오지 않는다.
- API 경로, 메서드, 상태 코드는 v3와 호환되어야 한다.
- 테스트는 실제 `todo.json`을 건드리면 안 된다.
  - 저장 경로는 환경변수 `TODO_FILE`로 바꿀 수 있게 한다 (기본값 `fastapi-app/todo.json`).
  - 테스트에서는 `tmp_path`를 쓴다.
- `tests/`와 `requirements-dev.txt`는 Docker 이미지에 넣지 않는다 (`.dockerignore`에 추가).
- 사용자 입력은 계속 `textContent`로 출력한다. 사용자 데이터에 `innerHTML`을 쓰지 않는다.

## Coding Conventions
- **Python**:
  - PEP 8, 타입 힌트를 쓴다.
  - 검증은 Pydantic(`Field`, `Literal`, `field_validator`)으로 하고, 엔드포인트 안에 수동 if 검증을 넣지 않는다.
  - 기존처럼 짧은 한국어 주석을 붙인다.
- **JS**:
  - `const`/`let`과 2칸 들여쓰기를 쓴다.
  - 기존 `api()`/`run()` 헬퍼로 모든 요청을 처리한다.
  - 우선순위 목록과 순서 같은 상수는 한 곳에만 정의한다.
- **테스트용 선택자**: UI 테스트 대상 요소에는 `data-testid`를 붙인다.
  - `todo-item`, `todo-tag`, `tag-filter`, `edit-form`, `edit-save`, `edit-cancel`
  - `stat-total`, `stat-completed`, `stat-active`, `stat-overdue`, `stat-rate`, `dday`
- **커밋**: Conventional Commits 형식이다 (`feat:`, `fix:`, `test:`, `docs:`, `chore:`).
  - 기능 커밋 예: `feat: add tags for Version 4.0.0`

## Definition of Done (DoD)

### A. API — pytest (`tests/test_api.py`)
**생성 / 검증**
- [ ] A1. 정상 POST → 201. 응답에 `id`, `tags`, `created_at`(파싱 가능한 ISO 8601)이 있고 `completed_at`은 `null`이다.
- [ ] A2. `title: "   "` → 422
- [ ] A3. `title: "  과제  "` → 저장된 값은 `"과제"`
- [ ] A4. `title` 101자 → 422
- [ ] A5. `priority: "긴급"` → 422. `높음`, `보통`, `낮음`은 각각 201
- [ ] A6. `due_date: "2026-02-30"`, `"2026/10/05"`, `"내일"` → 각각 422
- [ ] A7. `due_date: ""`와 `due_date: null` → 201, 저장된 값은 `null`
- [ ] A8. `tags: ["학교", " 과제 ", "학교"]` → `["학교", "과제"]`
- [ ] A9. `tags: ["Work", "work"]` → `["Work"]`
- [ ] A10. 빈 태그 `[""]`, 21자 태그, 쉼표가 든 태그 `["a,b"]`, 태그 11개 → 각각 422
- [ ] A11. 클라이언트가 보낸 `created_at`, `completed_at`은 무시된다
- [ ] A12. `completed: true`로 생성하면 `completed_at`이 채워진다

**수정 / 시각 규칙**
- [ ] A13. PUT 후에도 `created_at`이 바뀌지 않는다
- [ ] A14. 미완료 → 완료 PUT이면 `completed_at`이 채워진다
- [ ] A15. 완료 → 미완료 PUT이면 `completed_at`이 `null`이 된다
- [ ] A16. 완료 상태 그대로 제목만 수정하면 `completed_at`이 유지된다
- [ ] A17. PUT에도 A2~A10과 같은 검증이 적용된다
- [ ] A18. 없는 id에 PUT/DELETE → 404

**호환 / 회귀**
- [ ] A19. v3 형식 데이터(`priority`, `due_date`, `tags`, 시각 필드 없음, `due_date: ""`)를 담은 파일로 GET → 200, 기본값이 채워진다
- [ ] A20. CRUD 전체 흐름: 생성 → 조회 → 수정 → 삭제 → 조회 시 없음
- [ ] A21. 새 id는 기존 최대 id + 1이다
- [ ] A22. 테스트를 실행한 뒤에도 실제 `fastapi-app/todo.json` 내용이 바뀌지 않는다

### B. UI — Playwright (`tests/test_ui.py`)
**태그**
- [ ] B1. 추가 폼에 `학교, 과제`를 넣고 추가하면 항목에 `todo-tag` 칩 2개가 보인다
- [ ] B2. `tag-filter`에서 태그를 고르면 그 태그가 있는 항목만 보인다
- [ ] B3. 태그 칩을 클릭해도 같은 필터가 적용된다
- [ ] B4. 태그 필터 + 검색어 + 상태 필터가 AND로 함께 적용된다

**수정 폼**
- [ ] B5. Edit을 누르면 `edit-form`이 열리고, 5개 필드에 현재 값이 채워져 있다
- [ ] B6. 값을 바꾸고 `edit-save`를 누르면 폼이 닫히고 목록에 변경이 반영된다 (새로고침 후에도 유지)
- [ ] B7. 값을 바꾸고 `edit-cancel`을 누르면 원래 값이 그대로 보인다
- [ ] B8. 제목을 공백으로 저장하면 폼이 열린 채 `#error`가 보인다
- [ ] B9. 수정하는 동안 `prompt` 대화상자가 한 번도 뜨지 않는다 (dialog 이벤트로 확인)
- [ ] B10. 다른 항목의 Edit을 누르면 이전 수정 폼은 닫힌다

**통계**
- [ ] B11. 항목 4개(완료 1, 기한 초과 1)일 때:
  - `stat-total`=4, `stat-completed`=1, `stat-active`=3, `stat-overdue`=1, `stat-rate`=25%
- [ ] B12. 체크박스를 토글하면 통계가 즉시 갱신된다
- [ ] B13. 필터를 바꿔도 통계 값은 바뀌지 않는다
- [ ] B14. 항목이 0개면 완료율이 0%다

**D-day / 시각**
- [ ] B15. 브라우저 시계를 고정하고 확인한다 (Playwright clock):
  - 마감일이 +3일이면 `D-3`, 오늘이면 `오늘 마감`, -2일이면 `D+2`
- [ ] B16. 완료된 항목과 마감일 없는 항목에는 `dday`가 없다
- [ ] B17. 항목에 생성일이 보이고, 완료하면 완료일이 나타나고, 완료를 취소하면 사라진다

**회귀**
- [ ] B18. 추가, 체크 토글, 삭제(confirm 수락/취소) 동작
- [ ] B19. 검색(제목·설명)과 상태 필터 3종 동작. 선택된 필터 버튼이 시각적으로 구분된다
- [ ] B20. 우선순위순 정렬(높음 → 보통 → 낮음)과 마감일순 정렬(마감일 없는 항목은 맨 뒤)
- [ ] B21. 기한 초과 미완료 항목에 `overdue` 스타일과 `⚠ 기한 초과`가 표시된다
- [ ] B22. 페이지에 `Version 4.0.0`이 표시된다

### C. 공통
- [ ] C1. `pytest`가 모두 통과한다 (A, B 전체)
- [ ] C2. `docker compose up --build` 후 `http://localhost:5004`에서 화면이 뜨고 CRUD가 동작한다
- [ ] C3. Out of Scope 항목이 구현되지 않았고, API 경로와 상태 코드가 v3와 같다
- [ ] C4. 기능 단위로 Conventional Commits 커밋이 되어 있다
