# FastAPI To-Do List

FastAPI와 바닐라 JS로 만든 To-Do 웹 앱입니다. 데이터는 JSON 파일에 저장합니다.
**6.0.0** 릴리스 후보입니다(화면 표시는 `Version 6.0.0`, 아직 배포 전). 변경 내용은 [RELEASE_NOTES.md](RELEASE_NOTES.md), 개발 명세는 [CLAUDE.md](CLAUDE.md)에 있습니다.

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

## 실행

### Docker
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

## 배포와 데이터 관리
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
├── todo.json             # 로컬 실행용 데이터 (Docker는 볼륨 /app/data 사용)
├── requirements.txt      # 런타임 의존성
├── requirements-dev.txt  # 테스트 의존성
└── tests/                # pytest (API, UI, 통합)
scripts/load-data.sh      # 백업 → 데이터 볼륨 이전·복구
```
