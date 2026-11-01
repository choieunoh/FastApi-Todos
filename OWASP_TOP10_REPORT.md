# OWASP Top 10 보안 분석 보고서 — FastApi_Todos

| 항목 | 내용 |
|---|---|
| 분석 대상 | `FastApi_Todos` (브랜치 `feat/version-5.0.0`, 작업 트리 기준, 앱 버전 4.0.0 → 5.0.0 0단계) |
| 분석일 | 2026-10-13 |
| 기준 | **OWASP Top 10:2025** (A01~A10) |
| 분석 방법 | 정적 코드 검토 + 로컬 `TestClient`를 사용한 동작 확인. 배포 서버, 실제 Docker 이미지와 네트워크 구성은 직접 점검하지 않았다 |
| 코드 변경 | 없음 (보고서만 작성) |

## 0. 검토 범위

| 구분 | 파일 |
|---|---|
| 백엔드 | `fastapi-app/main.py` |
| 프론트엔드 | `fastapi-app/templates/index.html` |
| 컨테이너 | `fastapi-app/Dockerfile`, `fastapi-app/.dockerignore`, `docker-compose.yml` |
| 의존성 | `fastapi-app/requirements.txt`, `fastapi-app/requirements-dev.txt` |
| 설정 | `fastapi-app/sonar-project.properties`, `fastapi-app/pyproject.toml`, `.gitignore` |
| 데이터와 테스트 | `fastapi-app/todo.json`, `fastapi-app/tests/*` (보안 관점에서만 참고) |

### 구분 기준
- **[확인됨]**: 코드나 설정에서 문제를 직접 확인했고, 필요한 경우 로컬에서 재현했다.
- **[잠재]**: 지금 코드에서는 결함이 드러나지 않지만 배포 환경, 운영 방식이나 앞으로 구현할 기능에 따라 위험해질 수 있다.
- **[확인 불가]**: 이번 분석 범위나 환경에서는 판단할 근거가 없다.

### 위험도 기준
- **높음**: 인증 없이 원격에서 바로 데이터 변조·삭제나 서비스 중단이 가능하다.
- **중간**: 특정 조건(네트워크 노출, 운영 실수 등)이 갖춰지면 피해가 생긴다.
- **낮음**: 정보 노출이나 방어 계층 부족 수준이다.
- **정보**: 참고 사항이다.

> 참고: `CLAUDE.md`는 사용자 인증, 동시 쓰기 잠금, 원자적 저장을 **Out of Scope**로 정해 두었다. 이 보고서는 명세 위반 여부가 아니라 **보안 위험**을 평가하므로 그런 항목도 위험으로 기록한다. 개선 여부는 명세를 고쳐서 결정해야 한다.

---

## 1. 요약

| # | OWASP 2025 항목 | 결과 | 주요 내용 | 최고 위험도 |
|---|---|---|---|---|
| A01 | Broken Access Control | **[확인됨]** | 모든 API에 인증·인가가 없음. 누구나 전체 CRUD 가능 | 높음 |
| A02 | Security Misconfiguration | **[확인됨]** | `/docs`·`/redoc`·`/openapi.json` 공개, 보안 헤더 없음, 포트가 모든 인터페이스에 바인딩됨, SonarQube 설정 | 중간 |
| A03 | Software Supply Chain Failures | **[확인됨]/[잠재]** | 하한(`>=`)만 지정, 잠금 파일·해시 없음, 베이스 이미지 digest 미고정 | 중간 |
| A04 | Cryptographic Failures | **[확인됨]/[잠재]** | HTTP 평문 통신, 개인정보로 보이는 데이터가 Git에 평문으로 커밋됨 | 중간 |
| A05 | Injection | **[해당 결함 미발견]** + [잠재] | XSS는 `textContent`로 방어함. 7단계 CSV 수식 주입은 아직 구현 전 | 낮음 |
| A06 | Insecure Design | **[확인됨]** | 요청 크기·개수·속도 제한 없음, 매 요청마다 파일 전체를 읽고 씀, 동시 쓰기 시 유실 | 중간 |
| A07 | Authentication Failures | **[확인됨 — 인증 기능 자체 없음]** | 인증 메커니즘이 없어 "약한 인증"은 평가할 수 없음. A01과 같은 원인 | 높음 (A01과 중복) |
| A08 | Software or Data Integrity Failures | **[확인됨]/[잠재]** | `todo.json` 무결성 검증이 없고, 이미지·패키지 서명 검증이 없음 | 낮음~중간 |
| A09 | Security Logging and Alerting Failures | **[확인됨]** | 변경·삭제 감사 로그가 없음. uvicorn 접근 로그만 있음 | 중간 |
| A10 | Mishandling of Exceptional Conditions | **[확인됨]** | `todo.json` 손상 시 모든 API가 500, 비원자적 저장으로 손상 가능 | 중간~높음 |

---

## 2. 항목별 분석

### A01:2025 — Broken Access Control

#### A01-1. 모든 API에 인증·인가가 없음 — **[확인됨] / 위험도: 높음**
- **위치**: `fastapi-app/main.py:91-140` (모든 라우트), `docker-compose.yml:7-8` (`"5004:8000"`)
- **원인**: 라우트에 `Depends` 기반 인증이나 API 키 확인 같은 접근 제어가 전혀 없다. 데이터는 사용자 구분 없이 하나의 `todo.json`에 저장된다.
- **악용 가능 상황**:
  - 포트 5004에 닿을 수 있는 사람은 누구나 `GET /todos`로 전체 데이터를 읽을 수 있다.
  - 같은 방식으로 `PUT`이나 `DELETE /todos/{id}`를 보내 다른 사람의 항목을 바꾸거나 지울 수 있다.
  - id가 1부터 차례로 매겨지므로(`main.py:99`), `for id in 1..N: DELETE` 같은 반복으로 전체를 지우는 데 몇 초면 충분하다.
  - `tests/test_integration.py:9`의 기본값을 보면 서버가 공인 IP(`163.239.77.78:5014`)에 배포된 것으로 보이므로 실제로 노출됐을 가능성이 있다. 다만 실제 접근 가능 여부는 **확인하지 않았다**.
- **개선 방안**:
  1. 단기: 리버스 프록시(Nginx 등)에서 Basic 인증을 걸거나 IP 허용 목록을 둔다. 또는 `docker-compose.yml`의 포트를 `"127.0.0.1:5004:8000"`처럼 로컬에만 바인딩한다.
  2. 중기: FastAPI 의존성(`Depends`)으로 API 키나 세션 인증을 붙인다. 다중 사용자라면 항목에 소유자 필드를 두고 객체 단위로 인가 검사를 한다.
  3. 명세상 인증은 Out of Scope이므로, 공개 네트워크 배포를 금지하는 문구를 README에 넣는 것도 방법이다.

#### A01-2. CSRF — **[해당 결함 미발견, 조건부]**
- **근거(로컬 확인)**:
  - `Content-Type: text/plain`이나 `application/x-www-form-urlencoded`로 보낸 `POST /todos`는 **422**를 반환했다. HTML 폼으로 보내는 단순 요청으로는 위조할 수 없다는 뜻이다.
  - CORS 미들웨어가 없어 다른 출처의 preflight(`OPTIONS`)는 **405**를 받았고 `Access-Control-Allow-Origin`도 없었다. 그래서 다른 사이트의 `fetch`로 JSON을 보내거나 `PUT`/`DELETE`를 보낼 수 없다.
- **잠재 위험**: 나중에 `CORSMiddleware(allow_origins=["*"])`를 추가하거나 쿠키 기반 인증을 도입하면 CSRF 방어를 다시 설계해야 한다.

---

### A02:2025 — Security Misconfiguration

#### A02-1. API 문서 엔드포인트가 기본으로 공개됨 — **[확인됨] / 위험도: 낮음~중간**
- **위치**: `fastapi-app/main.py:18` — `FastAPI(title="To-Do List API")`
- **원인**: FastAPI는 기본으로 `/docs`, `/redoc`, `/openapi.json`을 켠다. 로컬에서 세 경로 모두 **200**을 받았다.
- **악용 가능 상황**: 공격자가 모든 경로, 스키마, 검증 규칙을 바로 알 수 있고, Swagger UI에서 곧바로 변경 요청을 보낼 수 있다. A01과 겹치면 공격이 더 쉬워진다. 또 `/docs`는 외부 CDN(jsdelivr)에서 Swagger UI 스크립트를 불러온다(A03과 관련).
- **개선 방안**: 운영 환경에서는 `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)`로 끄거나 환경변수로 켜고 끌 수 있게 한다.

#### A02-2. 보안 응답 헤더가 없음 — **[확인됨] / 위험도: 낮음**
- **위치**: `fastapi-app/main.py:138-140` (`/` 응답), 미들웨어 없음
- **원인**: `GET /` 응답에는 `content-type`, `etag`, `last-modified` 등만 있었다. `Content-Security-Policy`, `X-Frame-Options`(또는 CSP `frame-ancestors`), `X-Content-Type-Options`, `Referrer-Policy`가 없다.
- **악용 가능 상황**:
  - 다른 사이트가 이 페이지를 `<iframe>`으로 띄워 클릭재킹을 할 수 있다(예: 체크박스나 Edit 버튼을 클릭하도록 유도). 삭제는 `confirm()`을 거치므로 피해는 제한적이다.
  - CSP가 없어서 XSS 결함이 새로 생기면 막아 줄 2차 방어선이 없다.
- **개선 방안**: 미들웨어나 리버스 프록시에서 다음 헤더를 붙인다.
  - `Content-Security-Policy: default-src 'self'; frame-ancestors 'none'`
  - `X-Content-Type-Options: nosniff`
  - `Referrer-Policy: no-referrer`

  현재 `index.html`은 인라인 `<script>`·`<style>`을 쓰므로 nonce나 hash 기반 CSP가 필요하다. 명세상 외부 스크립트는 쓰지 않는다.

#### A02-3. 컨테이너 포트가 모든 인터페이스에 바인딩됨 — **[확인됨] / 위험도: 중간**
- **위치**: `docker-compose.yml:7-8` (`"5004:8000"`), `docker-compose.yml:20` (`"9000:9000"`), `fastapi-app/Dockerfile` 마지막 줄 (`--host 0.0.0.0`)
- **원인**: 호스트 IP를 지정하지 않으면 Docker가 `0.0.0.0`에 바인딩한다. 그러면 호스트 방화벽(ufw 등)을 우회해 외부에 노출될 수도 있다. 컨테이너 안의 `0.0.0.0` 자체는 정상이다.
- **악용 가능 상황**: 인증 없는 API(A01)와 SonarQube 웹 UI(9000)가 외부 네트워크에 그대로 열린다.
- **개선 방안**: 필요하지 않으면 `"127.0.0.1:5004:8000"`, `"127.0.0.1:9000:9000"`으로 제한하고, 외부 공개는 TLS를 종단하는 리버스 프록시를 거치게 한다. 단, CLAUDE.md는 "포트 변경"을 Out of Scope로 정했으므로 명세 협의가 필요하다.

#### A02-4. SonarQube 컨테이너 설정 — **[잠재] / 위험도: 중간**
- **위치**: `docker-compose.yml:15-30`
- **원인**:
  - `SONAR_ES_BOOTSTRAP_CHECKS_DISABLE=true`로 Elasticsearch 부트스트랩 점검을 끈다.
  - 9000 포트가 외부에 열려 있다.
  - SonarQube는 기본 관리자 계정이 `admin/admin`이다. 이 계정을 바꿨는지는 **확인할 수 없었다**.
- **악용 가능 상황**: 기본 비밀번호가 그대로라면 분석 결과와 소스 코드 스냅샷이 노출되고 토큰이 탈취될 수 있다.
- **개선 방안**: 관리자 비밀번호를 바꾸었는지 확인한다. 포트는 로컬에만 바인딩하고, 개발용이 아닌 환경에서는 부트스트랩 점검을 켠다.
- **양호한 점**: `read_only: true`, `tmpfs` 사용, 이미지 태그를 정확한 버전으로 고정함.

#### A02-5. 앱 컨테이너 하드닝 — **[확인됨, 대체로 양호] / 위험도: 정보**
- **양호**:
  - 비 root 사용자(`USER appuser`)로 실행하고, 코드는 root 소유라 앱이 수정할 수 없다.
  - `no-new-privileges`와 `cap_drop: ALL`을 적용했다.
  - `.dockerignore`로 `tests/`, `.env`, `requirements-dev.txt`를 이미지에서 제외한다.
- **보완 여지**:
  - 앱 컨테이너에는 `read_only: true`가 없다. 쓰기가 필요한 곳은 `todo.json`뿐이다.
  - 메모리·CPU 제한(`mem_limit`, `pids_limit`)이 없다. A06의 자원 고갈과 이어진다.

---

### A03:2025 — Software Supply Chain Failures

#### A03-1. 의존성 버전이 하한만 지정되고 잠금 파일이 없음 — **[확인됨] / 위험도: 중간**
- **위치**: `fastapi-app/requirements.txt:1-3` (`fastapi>=0.142.0`, `starlette>=1.7.0`, `uvicorn[standard]>=0.54.0`), `fastapi-app/requirements-dev.txt`
- **원인**: 상한, 정확한 버전(`==`), 해시(`--hash`)가 없다. `uvicorn[standard]`는 `uvloop`, `httptools`, `websockets`, `watchfiles` 같은 간접 의존성도 함께 끌어온다.
- **악용 가능 상황**:
  - 빌드할 때마다 다른 버전이 설치되므로 재현할 수 없다.
  - 상위 패키지나 간접 의존성이 탈취되어 악성 릴리스가 나오면 다음 `docker build`에 그대로 들어간다.
  - 호환성이 깨지는 버전이 들어올 수 있다.
- **개선 방안**: `pip-compile --generate-hashes`나 `uv lock`으로 잠금 파일을 만들고 `pip install --require-hashes -r requirements.lock`로 설치한다. Dependabot이나 Renovate로 갱신을 관리한다.

#### A03-2. 베이스 이미지가 digest로 고정되지 않음 — **[확인됨] / 위험도: 낮음**
- **위치**: `fastapi-app/Dockerfile:2` — `FROM python:3.13-slim`
- **원인**: 태그는 바뀔 수 있다(mutable).
- **개선 방안**: `python:3.13-slim@sha256:<digest>`로 고정하고 주기적으로 갱신한다. 이미지 스캔(Trivy 등)을 CI에 추가한다.

#### A03-3. 알려진 취약점(CVE) 점검 — **[확인 불가]**
- 로컬 설치 버전은 fastapi 0.142.2, starlette 1.7.0, uvicorn 0.54.0이다.
- `requirements.txt` 주석이 언급하는 CVE(`CVE-2026-48710`)와 `requirements-dev.txt`의 `CVE-2025-71176`이 실제로 존재하는지, 패치 버전이 맞는지는 이번 분석에서 **검증하지 않았다**.
- `pip-audit`이나 OSV 스캔은 실행하지 않았다. 따라서 "알려진 취약점 없음"이라고 단정할 수 없다.
- **권장**: CI에서 `pip-audit -r requirements.txt`와 컨테이너 이미지 스캔을 실행한다.

#### A03-4. 프론트엔드 외부 의존성 — **[해당 없음]**
- `index.html`은 외부 CDN이나 스크립트를 불러오지 않는다(`<script src>`, `<link>` 없음).
- 단, FastAPI 기본 `/docs`는 jsdelivr CDN을 쓴다(A02-1).

---

### A04:2025 — Cryptographic Failures

#### A04-1. 평문 HTTP로 서비스함 — **[확인됨] / 위험도: 중간**
- **위치**:
  - `fastapi-app/Dockerfile` CMD (TLS 설정 없음)
  - `docker-compose.yml:8`
  - `fastapi-app/tests/test_integration.py:9` (`http://163.239.77.78:5014`)
  - `fastapi-app/sonar-project.properties` (`sonar.host.url=http://localhost:9000`, 로컬이므로 위험 낮음)
- **원인**: TLS를 종단하는 구성이 없다.
- **악용 가능 상황**: 같은 네트워크(공용 Wi-Fi, 학내망 등)에 있는 공격자가 할 일 내용을 엿보거나 응답을 변조할 수 있다. 응답의 `index.html`에 스크립트를 주입할 수도 있다.
- **개선 방안**: 리버스 프록시(Nginx, Caddy 등)에서 TLS를 적용하고 HSTS를 설정한다.

#### A04-2. 개인정보로 보이는 데이터가 저장소에 평문으로 커밋됨 — **[확인됨] / 위험도: 낮음~중간**
- **위치**: `fastapi-app/todo.json` (Git 추적 중, `git ls-files`로 확인)
- **원인**: 실제 데이터 파일이 버전 관리에 들어 있다. 현재 내용에 학번 형식의 숫자와 실명으로 보이는 값이 있다. 이 보고서에서는 해당 값을 옮겨 적지 않았다.
- **악용 가능 상황**: 저장소가 공개되면 개인정보가 노출된다. 또 Docker 이미지에 `COPY todo.json`으로 들어가므로 이미지를 배포하면 함께 배포된다.
- **개선 방안**:
  - `todo.json`을 `.gitignore`에 추가하고 `git rm --cached`로 추적에서 뺀다. 이미 커밋된 이력을 지우려면 `git filter-repo`가 필요하다.
  - 이미지에는 빈 `[]` 파일만 넣거나 앱 기동 시 생성하게 한다. 지금도 `main.py:15-16`이 없으면 생성한다.
  - 저장 시 암호화는 앱 성격상 과할 수 있지만, 민감 정보를 저장하지 않는다는 운영 원칙은 필요하다.

#### A04-3. 비밀정보 관리 — **[해당 결함 미발견]**
- 코드와 설정에서 하드코딩된 비밀번호, 토큰, 키를 찾지 못했다. `sonar-project.properties`는 토큰을 적지 않는다고 명시하고 있다. `.env`는 `.gitignore`와 `.dockerignore`에 모두 들어 있다.
- Git **이력 전체**의 비밀정보 스캔(gitleaks 등)은 하지 않았으므로 이력에 대해서는 **확인 불가**다.

---

### A05:2025 — Injection

#### A05-1. XSS (DOM 기반 / 저장형) — **[해당 결함 미발견]**
- **근거**:
  - `index.html`은 사용자 데이터를 `el()` 헬퍼(`Object.assign(document.createElement(tag), props)`, 196-198행)의 `textContent`와 `value` 속성으로만 출력한다. 출력 위치는 342행(제목·설명), 249-250행(태그 select), 192행(오류 메시지)이다.
  - 파일 전체에서 `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval`, `new Function`은 쓰이지 않는다(grep으로 확인).
  - 태그 칩은 `button()`의 `textContent`로 만들고, `dataset`은 고정 문자열이다.
- **잠재 위험**:
  - `el()`은 props를 그대로 대입한다. 나중에 누군가 `el("div", { innerHTML: todo.title })`처럼 쓰면 바로 XSS가 된다.
  - 앞으로 캘린더 뷰(8단계)나 토스트(2단계)를 구현할 때도 같은 원칙을 지켜야 한다. CSP(A02-2)가 2차 방어가 된다.

#### A05-2. SQL / 명령 / 경로 주입 — **[해당 없음]**
- DB를 쓰지 않고 셸 명령을 실행하지 않는다.
- 파일 경로는 서버 상수와 환경변수 `TODO_FILE`(`main.py:12`)로만 정해지며 요청 값은 경로에 들어가지 않는다.
- `todo_id`는 FastAPI가 `int`로 검증한다.

#### A05-3. CSV 수식 주입 (7단계 예정 기능) — **[잠재] / 위험도: 낮음**
- **위치**: 아직 구현 전이다(`CLAUDE.md` §17).
- **내용**: 명세에는 `= + - @`로 시작하는 값 앞에 `'`를 붙이도록 되어 있다. OWASP 권고는 탭(`\t`)과 CR(`\r`)로 시작하는 값도 포함하고, 각 필드(title, description, tags, 하위 할 일 제목)에 모두 적용하라고 한다. 구현 시 이를 반영하고 테스트하는 것을 권장한다.

---

### A06:2025 — Insecure Design

#### A06-1. 요청 크기·데이터 총량·요청 빈도 제한 없음 → 자원 고갈 — **[확인됨] / 위험도: 중간**
- **위치**:
  - `fastapi-app/main.py:96-108` (`POST /todos`)
  - `main.py:68-75` (매 요청마다 파일 전체를 읽고 씀)
  - `main.py:30, 41-55` (`tags` 리스트 길이를 파싱 전에 제한하지 않음)
- **원인**:
  1. 항목 수에 상한이 없다. 항목 하나는 최대 약 1KB(제목 100자, 설명 500자, 태그 10×20자)이므로 반복 POST로 파일이 계속 커진다.
  2. 모든 요청이 `todo.json` 전체를 `json.loads`한 뒤 Pydantic으로 검증하고 다시 직렬화해서 쓴다. 파일이 커질수록 요청마다 CPU·메모리·디스크 I/O가 선형으로 늘어난다.
  3. `tags`는 중복을 제거한 **뒤에** 개수를 검사한다(`main.py:53`). 로컬에서 `"a"` 20만 개짜리 tags를 보냈더니 **201**이 반환되고 `["a"]`로 저장됐다. 큰 요청 본문이 끝까지 파싱·처리된다는 뜻이다.
  4. uvicorn과 앱 모두 요청 본문 크기 제한과 rate limit이 없고, 컨테이너 자원 제한도 없다(A02-5).
- **악용 가능 상황**: 인증이 없으므로(A01) 누구나 수십만 건의 POST나 수십 MB짜리 본문을 보내 디스크와 메모리를 고갈시키고 응답을 지연시킬 수 있다.
- **개선 방안**:
  - `tags: list[str] = Field(default=[], max_length=…)`처럼 리스트 원소 수를 파싱 단계에서 먼저 제한한다. 중복 제거 전 기준이므로 넉넉하게 잡는다.
  - 리버스 프록시에서 `client_max_body_size`와 rate limit을 건다.
  - 전체 항목 수에 상한을 두고, 컨테이너에 `mem_limit`과 `pids_limit`을 설정한다.

#### A06-2. 동시 쓰기 시 데이터 유실 (경쟁 조건) — **[확인됨(코드 구조)] / 위험도: 중간**
- **위치**: `fastapi-app/main.py:96-135` — 모든 쓰기가 `load_todos()` → 수정 → `save_todos()` 순서로 동작한다.
- **원인**: 동기(`def`) 엔드포인트는 스레드풀에서 동시에 실행되는데, 잠금이 없다(명세상 Out of Scope).
- **악용 가능 상황**: 두 요청이 같은 시점에 파일을 읽으면 나중에 쓴 쪽이 먼저 쓴 변경을 덮어쓴다. 같은 id가 두 번 발급될 수도 있다(`main.py:99`). 공격자가 동시 요청을 반복하면 데이터를 의도적으로 손상시킬 수 있다. 이번에는 직접 재현하지 않았다.
- **개선 방안**: 프로세스 안 `threading.Lock`으로 읽기-수정-쓰기 구간을 감싸고, 장기적으로는 SQLite 같은 트랜잭션 저장소를 쓴다(명세 변경 필요).

#### A06-3. 삭제 실행 취소 버퍼 (2단계 예정) — **[잠재] / 위험도: 낮음**
- 명세(§12)는 메모리 버퍼를 30초, 최대 50건으로 제한한다. 이 제한을 지키지 않고 구현하면 메모리 고갈 경로가 생기므로, 구현 시 두 제한을 모두 테스트하는 것을 권장한다.
- `/todos/bulk`(3단계)의 `ids` 1~100개 제한도 같다.

---

### A07:2025 — Authentication Failures

#### A07-1. 인증 메커니즘 부재 — **[확인됨] / 위험도: 높음 (A01과 같은 원인)**
- **위치**: 프로젝트 전체. 로그인, 세션, 토큰 관련 코드가 없다.
- 비밀번호 정책, 세션 고정, 자격 증명 대입(credential stuffing), MFA 같은 이 항목의 세부 위험은 **평가할 대상이 없다**. "인증이 안전하다"는 뜻이 아니라 **인증이 없다**는 뜻이며, 그 영향은 A01-1에 기록했다.
- **개선 방안**: A01-1과 같다. 인증을 도입한다면 비밀번호 해시(argon2/bcrypt), 로그인 시도 제한, 안전한 쿠키 속성(`HttpOnly`, `Secure`, `SameSite`)을 함께 설계해야 한다.

---

### A08:2025 — Software or Data Integrity Failures

#### A08-1. 데이터 파일 무결성 검증 없음 — **[확인됨] / 위험도: 낮음**
- **위치**: `fastapi-app/main.py:68-70` — `load_todos()`가 `todo.json`을 그대로 신뢰한다.
- **원인**: 파일을 바꾸면 그대로 반영된다. Pydantic 검증은 다시 하므로 형식이 맞지 않으면 500(A10)이 되고, 형식이 맞으면 그대로 반영된다.
- **악용 가능 상황**: 호스트나 컨테이너의 파일에 쓸 수 있는 공격자는 데이터를 조작할 수 있다. 다만 그 정도 권한이면 이미 큰 침해이므로 추가 위험은 낮다.
- **양호한 점**: `pickle` 같은 위험한 역직렬화를 쓰지 않고 JSON과 Pydantic만 쓴다. 클라이언트가 보낸 `created_at`, `completed_at`, `id`는 무시된다(`TodoIn`에 해당 필드 없음, `main.py:24-30`). 대량 할당(mass assignment)은 막혀 있다.

#### A08-2. 빌드·배포 파이프라인 무결성 — **[잠재] / 위험도: 낮음~중간**
- 패키지 해시 검증이 없고(A03-1) 이미지 digest도 고정하지 않았다(A03-2). 이미지 서명(cosign 등)도 없다.
- `sonar-project.properties`는 Jenkins 파이프라인을 언급하지만, Jenkinsfile과 CI 설정은 저장소에 없어 **확인 불가**다.

---

### A09:2025 — Security Logging and Alerting Failures

#### A09-1. 감사 로그와 경보 없음 — **[확인됨] / 위험도: 중간**
- **위치**: `fastapi-app/main.py` 전체 — `logging` 사용이 없다.
- **원인**: 생성, 수정, 삭제 이벤트를 기록하지 않는다. uvicorn 기본 접근 로그(메서드, 경로, 상태 코드, 클라이언트 IP)만 표준 출력으로 나간다. 컨테이너 로그 보존 기간과 수집 여부는 **확인 불가**다.
- **악용 가능 상황**: A01을 악용한 대량 삭제나 A06의 대량 POST가 일어나도 누가 언제 무엇을 바꿨는지 추적하거나 복구할 근거가 부족하다. 422나 404가 폭증해도 경보가 없다.
- **개선 방안**:
  - `logging`으로 변경 이벤트(동작, id, 시각, 클라이언트 IP)를 구조화해 남긴다. 항목 내용 같은 민감정보는 남기지 않는다.
  - 로그를 컨테이너 밖으로 보관한다(로그 드라이버, 수집기 등).
  - 비정상 요청량에 대한 경보 기준을 둔다.

---

### A10:2025 — Mishandling of Exceptional Conditions

#### A10-1. 데이터 파일 손상 시 서비스 전체가 중단됨 — **[확인됨] / 위험도: 중간~높음**
- **위치**: `fastapi-app/main.py:68-70`, `main.py:73-75`
- **원인**:
  1. `load_todos()`는 `json.loads`나 `TodoItem(**t)`가 실패해도 예외를 처리하지 않는다. 로컬에서 파일 내용을 `{broken`으로 바꾸고 `GET /todos`를 보냈더니 `JSONDecodeError`가 처리되지 않고 올라왔다. 운영 서버에서는 500이다.
  2. 모든 엔드포인트가 `load_todos()`를 거치므로 GET, POST, PUT, DELETE가 **모두** 실패한다. 스스로 복구할 수 없다.
  3. `save_todos()`는 `write_text`로 원본을 바로 덮어쓴다(비원자적 저장). 쓰는 도중 프로세스 종료, 디스크 부족(A06-1과 연계), 동시 쓰기(A06-2)가 일어나면 파일이 잘리거나 섞여 1번 상태가 된다.
- **악용 가능 상황**: 대량 요청으로 디스크를 채우거나 동시 쓰기를 유도해 파일이 손상되면 지속적인 서비스 거부가 된다. 데이터가 날아갈 수도 있다.
- **개선 방안**:
  - 같은 폴더의 임시 파일에 쓴 뒤 `os.replace()`로 바꿔치기한다(원자적 저장, 명세상 Out of Scope).
  - 저장 전에 백업 파일을 남긴다.
  - 읽기 실패 시 원인을 로그로 남기고, 503과 명확한 오류를 반환한다.

#### A10-2. 기동 시 파일 생성 실패 처리 — **[잠재] / 위험도: 낮음**
- **위치**: `fastapi-app/main.py:15-16` — 모듈을 import할 때 `TODO_FILE.write_text("[]")`를 실행한다.
- **내용**: 디렉터리에 쓰기 권한이 없거나 `TODO_FILE`이 잘못된 경로면 앱이 아예 기동하지 않는다. Dockerfile에서는 `todo.json`을 `appuser` 소유로 복사하므로 지금은 문제가 없다. 그러나 앞서 권고한 대로 `read_only: true`를 적용하거나 `todo.json`을 이미지에서 빼면 이 경로를 함께 정리해야 한다.

#### A10-3. 오류 응답의 정보 노출 — **[해당 결함 미발견]**
- 처리되지 않은 예외는 Starlette 기본 핸들러가 스택트레이스 없이 `Internal Server Error`로 응답한다. `debug=True` 설정도 없다.
- 422 응답에는 Pydantic 검증 상세(입력값 포함)가 들어가고 프론트엔드가 이를 `#error`에 `textContent`로 표시한다(`index.html:181, 192`). 요청자 자신이 보낸 값이므로 노출 위험은 낮다.

---

## 3. 우선순위별 개선 권고

| 우선순위 | 조치 | 관련 항목 | 명세 영향 |
|---|---|---|---|
| 1 | 외부 노출 차단: 포트를 `127.0.0.1`에 바인딩하거나 리버스 프록시에서 인증·IP 제한·TLS 적용 | A01, A02-3, A04-1, A07 | 배포 구성 변경(Out of Scope) — 협의 필요 |
| 2 | `todo.json`을 Git 추적에서 빼고 이미지에서 실제 데이터 제외. 이력 정리 검토 | A04-2 | 없음 |
| 3 | 원자적 저장(`os.replace`)과 읽기 실패 처리, 프로세스 내 잠금 | A10-1, A06-2 | Out of Scope 항목 — 명세 변경 필요 |
| 4 | 요청 본문·리스트 길이·항목 수 제한, rate limit, 컨테이너 자원 제한 | A06-1, A02-5 | 일부 검증 규칙 추가 |
| 5 | 의존성 잠금 + 해시, 베이스 이미지 digest 고정, CI에서 `pip-audit`·이미지 스캔 | A03, A08-2 | `requirements.txt` 형식 변경 |
| 6 | 운영에서 `/docs`·`/openapi.json` 비활성화, 보안 헤더(CSP 등) 추가 | A02-1, A02-2 | 없음 |
| 7 | 변경 이벤트 감사 로그 | A09-1 | 없음 |
| 8 | 5.0.0 구현 시: CSV 수식 주입 방어 범위(`\t`, `\r` 포함), 삭제 버퍼·bulk 상한 테스트, `innerHTML` 금지 유지 | A05-3, A06-3, A05-1 | 명세 보완 권장 |

## 4. 분석 한계
- 배포 서버(`163.239.77.78:5014`)에는 접속하지 않았다. 실제 노출 여부와 프록시·방화벽 구성은 확인하지 않았다.
- Docker 이미지를 빌드하거나 스캔하지 않았다. `docker run --rm <image> ls` 검증(DoD 0-3)도 이번 범위에 들어가지 않는다.
- `pip-audit`, Trivy, gitleaks 같은 자동 스캐너를 실행하지 않았다. 의존성 CVE와 Git 이력 속 비밀정보는 **확인 불가**다.
- 동시성 문제(A06-2)는 코드 구조를 근거로 판단했고, 부하를 걸어 재현하지는 않았다.
- 5.0.0 1~8단계 기능은 아직 구현되지 않아 명세(`CLAUDE.md`)만 근거로 잠재 위험을 적었다.
