# FastAPI To-Do List

FastAPI와 바닐라 JS로 만든 To-Do 웹 앱입니다. 데이터는 JSON 파일에 저장합니다.
**5.0.0**을 단계별로 개발하고 있습니다 (화면 표시는 `Version 5.0.0`). 개발 명세는 [CLAUDE.md](CLAUDE.md)에 있습니다.

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
| 4~8 | 하위 할 일, 반복 일정, 작업 템플릿, 내보내기, 캘린더 | 예정 |

## 실행

### Docker
```bash
docker compose up --build fastapi-app
```
http://localhost:5004 에서 열립니다.

### 로컬
```bash
cd fastapi-app
pip install -r requirements.txt
uvicorn main:app --reload
```
http://localhost:8000 에서 열립니다. 데이터 파일 위치는 환경변수 `TODO_FILE`로 바꿀 수 있습니다 (기본값 `fastapi-app/todo.json`).

## API
| 메서드 | 경로 | 동작 |
|---|---|---|
| GET | `/todos` | 전체 목록 |
| POST | `/todos` | 생성 (201) |
| PUT | `/todos/{id}` | 전체 수정 (없으면 404). 즐겨찾기 등록·해제도 `favorite` 필드로 합니다 |
| DELETE | `/todos/{id}` | 삭제 (204, 없으면 404) |
| POST | `/todos/bulk` | `{"ids": [1, 2], "action": "complete" \| "uncomplete" \| "delete"}` — 1~100건 일괄 처리 (200, `{"updated": [...], "deleted": [...]}`). 없는 id가 하나라도 있으면 404이고 아무것도 바꾸지 않음 |
| POST | `/todos/restore` | `{"ids": [1, 2]}` (1~100건) — 30초 안에 지운 항목을 원래 id·생성 시각·자리로 복구 (200). 복구할 수 없는 id가 하나라도 있으면 404이고 아무것도 복구하지 않음 |
| GET | `/` | 화면 |

서버를 띄운 뒤 http://localhost:8000/docs 에서 요청·응답 스키마를 볼 수 있습니다.

## 테스트
```bash
cd fastapi-app
pip install -r requirements-dev.txt
playwright install chromium        # UI 테스트용 브라우저 (처음 한 번)

pytest                             # API + UI 테스트 (실제 todo.json은 건드리지 않음)
pytest tests/test_api.py           # API만
pytest tests/test_ui.py --headed   # UI를 브라우저 창으로 보면서 실행
pytest -m integration              # 배포 서버 대상 통합 테스트
                                   # 대상 주소: INTEGRATION_BASE_URL (기본 http://163.239.77.78:5014)
```
- API 테스트는 FastAPI `TestClient`로, 테스트마다 임시 `todo.json`을 씁니다.
- UI 테스트는 `tests/conftest.py`가 임시 데이터 파일로 uvicorn을 따로 띄우고 Playwright로 확인합니다.
- `tests/`와 `requirements-dev.txt`는 Docker 이미지에 들어가지 않습니다.

## 구조
```
fastapi-app/
├── main.py               # API, 모델, 저장 로직
├── templates/index.html  # 화면 (HTML + CSS + JS 한 파일)
├── todo.json             # 데이터
├── requirements.txt      # 런타임 의존성
├── requirements-dev.txt  # 테스트 의존성
└── tests/                # pytest (API, UI, 통합)
```
