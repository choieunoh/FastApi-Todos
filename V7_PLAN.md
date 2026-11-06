# V7_PLAN.md — FastAPI To-Do List 7.0.0 구현 계획

명세는 `V7_SPEC.md`(§25~§34)와 `CLAUDE.md`를 따른다. 이 문서는 단계·절차·백업·롤백·테스트·위험을 다룬다.
각 Phase가 끝나면 구현 내용, 변경 파일, 테스트 결과, 위험 요소를 보고하고 **승인을 받은 뒤** 다음 Phase로 간다.

## 운영 보호 원칙 (모든 Phase 공통)
다음은 사용자의 명시적 승인 없이는 하지 않는다.
- 운영 Docker 컨테이너 중지·삭제·재생성, 운영 볼륨 변경·삭제
- 기존 JSON 데이터 변경·삭제 (운영 볼륨, 저장소의 `todo.json`, Git 기록 포함)
- 운영 PostgreSQL 생성·변경, Jenkins 설정·배포 작업 변경, 운영 서버 배포
- Git 강제 푸시·파괴적 Git 명령 (history 재작성, `reset --hard`, stash 삭제 등)
- `test_integration.py` 실행 (외부 서버 `163.239.77.78:5014`에 POST·DELETE를 보낸다)
- 운영 볼륨에서 백업 사본을 꺼내는 작업 (읽기 전용이라도 운영 서버에서 명령을 실행하므로)

운영 HTTPS가 확인되기 전에는 v7을 운영에 공개하지 않는다(§P7).

## P0. 결정 현황
### 승인됨 (2026-11-05)
D-1 의존성 추가, D-2 파일 구성, D-3 CLAUDE.md 수정, D-4 username·비밀번호 8~128자, D-5 14일 고정 세션, D-8 사용자별 알림 키, D-9 비밀번호 변경 제외, D-10 지정 사용자 1명에게 이전, D-11 익명 401.

### 승인됨 (2026-11-06, Phase 2-D 구현 승인)
- **D-12 채택**: 수치는 권장안 그대로 — 로그인 (username, IP) 5회·username 50회·IP 20회 / 15분, 가입 IP 10회 / 1시간, 기록 보관 24시간. 저장은 DB `auth_failures` + username advisory lock.
  - 근거: 2-D 읽기 전용 설계 검토 3회(2026-11-06)에서 다중 워커 정확성(같은 username 직렬화)·연결 풀 고갈·우회 경로를 비교한 뒤 사용자가 승인했다. 2-A에서는 스키마에 한해 잠정 채택이었다.
  - 함께 승인된 정책(의미 변경 포함, `V7_SPEC.md` §29.5 변경 기록): 선기록 방식(Tx1 커밋 → 트랜잭션 밖 Argon2 → 성공 Tx2), 성공·보상 시 **자기 선기록 id만** 삭제, "실패 횟수" → "성공으로 확인되지 않은 시도 수", 429 미기록, k번째 기록 기준 `Retry-After`, `username_key` HMAC(`AUTH_FAILURE_KEY` 신설), IPv6 /64, Argon2 슬롯 2개·5초, 새 응답 코드 503(C-7), `lock_timeout` 2초, 로그인 경로의 요청당 트랜잭션 1개 원칙 예외(CLAUDE.md).
- D-6·D-7·D-13 등 아래 표의 다른 권장안 상태는 바꾸지 않았다.

### 권장안 — Phase 1 시작 전 승인 필요
| ID | 내용 | 권장 |
|---|---|---|
| D-6 | 목록 순서 보존 | `position` 열 (할 일·템플릿), 삭제 시 유지·복구 시 원래 값, 반복 다음 일정은 끝 (`V7_SPEC.md` §27.3) |
| D-7 | 삭제 실행 취소 저장 | PostgreSQL soft delete (`deleted_at`, 30초·사용자당 100건) (§27.4 비교표) |
| D-12 | 시도 제한 수치·저장 | DB `auth_failures` + username advisory lock. (username,IP) 5회 / username 50회 / IP 20회 per 15분, 가입 IP 10회/시간. **Phase 2에서 구현** (§29.5) — **2026-11-06 채택** (위 "승인됨 (2026-11-06)") |
| D-13 | 가입 공개 제어 | `REGISTRATION_ENABLED` 환경변수 — 운영 기본 `false`, 개발·테스트 `true` |
| D-14 | 저장소의 개인정보 값 | 아래 §P9 선택지 중 결정. 결정 전에는 아무것도 바꾸지 않는다 |
| D-15 | 리허설 사본 보관 기간 | 운영 전환 검증 완료 후 30일, 이후 삭제·기록 |
| D-16 | PostgreSQL 버전 | 18.x 최신 패치를 Phase 1에서 확인해 정확한 태그로 고정 |
| D-17 | `migrate_json.py` 서브커맨드 | `import`, `verify`, `export-v6`, `diff` (역내보내기·비교를 같은 파일에 둔다, 새 파일 없음) |

## P1. Phase 1 — PostgreSQL 개발 환경, SQLAlchemy, Alembic
**개발 DB 경로 (2026-11-05 변경)**: 이 VDI에서는 Docker Desktop을 쓸 수 없어(R-1), 기본 경로는 **Windows 로컬 PostgreSQL 18.6**(`127.0.0.1:5433`, 설치 `C:\Program Files\PostgreSQL(new)`)이다. 같은 PC의 PostgreSQL 16(5432)은 접근·변경하지 않는다.
- DB와 계정: 개발 `todo_dev`(소유자 `todo_dev_user`), 테스트 `todo_test`(소유자 `todo_test_user`). 서로의 DB에 CONNECT 권한이 없다(사용자 확인).
- 비밀번호는 `%APPDATA%\postgresql\pgpass.conf`(사용자가 메모장으로 작성)에만 둔다. URL·소스·로그·Git·채팅에 넣지 않고, Claude는 이 파일을 읽지 않는다(존재 여부만 확인).
- `docker-compose.dev.yml`은 Docker가 되는 PC용 **선택 구성**으로 남긴다. 테스트 보호 장치가 5433만 허용하므로 그 구성으로 테스트하려면 승인이 필요하다.

작업 (1-A 완료, 2026-11-05)
1. `docker-compose.dev.yml` (선택 구성, 운영 `docker-compose.yml`과 별개, 운영 서비스 없음)
   - 서비스 `postgres-dev` 1개, 프로젝트명 `fastapi-v7dev`, 컨테이너 `FastApi-pg-v7dev`, 포트 `127.0.0.1:15432:5432`, 볼륨 `fastapi_v7dev_pg-data`. `docker compose config`만 확인했고 실행하지 않았다.
2. `requirements.txt`: `sqlalchemy>=2.1.8`, `psycopg[binary]>=3.3.8`, `alembic>=1.20.1`, `argon2-cffi>=25.1.0`.
3. `db.py`: 엔진(`DATABASE_URL`, `pool_pre_ping`, 연결 `TimeZone=UTC`), 세션, `get_db`, ORM 모델 6개, 테스트 DB 보호·downgrade 보호 함수.
4. Alembic: `fastapi-app/alembic.ini`, `alembic/env.py`(`DATABASE_URL` 읽기, `db.Base.metadata`), 리비전 `0001_initial`.
5. 테스트 기반: `conftest.py`가 세션 시작 전에 `TEST_DATABASE_URL`을 검사하고, `test_db_postgres.py`가 접속 후 서버를 확인한 뒤 테스트마다 public 스키마를 다시 만든다(아래 P1.1).
6. `.gitignore`에 `.env.dev`, `*.dump`, `*.sql.gz` 추가. 이미지에 `db.py`·`alembic/`을 넣는 것은 Phase 3·6.

**이 Phase에서는 앱 엔드포인트 동작을 바꾸지 않는다.** 아직 `main.py`는 JSON 저장을 쓴다.

### P1.1 테스트 DB 보호 (F-1)
- 허용 대상은 하나다: `todo_test_user@127.0.0.1:5433/todo_test`, 서버 18.x (`db.TEST_DB_*`). 이 값을 바꾸거나 완화하려면 사용자 승인을 받는다.
- 1단계 URL (`db.check_test_database_url`, `conftest.pytest_configure`): DB 이름 `_test`, 쿼리 매개변수 없음, 포트 명시, 계정·호스트·포트·DB가 대상과 정확히 같음, `DATABASE_URL`과 다른 DB. 어긋나면 세션을 시작하지 않는다(DB 연결 전).
- 2단계 서버 (`db.verify_test_server`): 접속 후 `current_database()`, `current_user`, `inet_server_addr()`, `inet_server_port()`, `server_version_num`, `rolsuper`를 읽어 대상·18.x·비슈퍼유저인지 확인한다. `engine` 픽스처가 처음 한 번(실패 시 `pytest.exit`), `fresh_schema`가 **`DROP SCHEMA` 직전 같은 연결에서** 다시 확인한다.
- `DROP SCHEMA`는 `fresh_schema`에만 있고 확인 뒤에 온다(오프라인 테스트가 소스로 확인).

### P1.2 downgrade 보호와 승인 절차 (F-2)
- 모든 리비전의 `downgrade()` 첫 줄은 `db.guard_downgrade(...)`다(오프라인 테스트가 모든 리비전을 확인).
- 테스트 대상(P1.1의 URL·서버 확인을 모두 통과)에서는 그대로 허용한다 — 마이그레이션 왕복은 **pytest로 테스트 DB에서만** 한다.
- 그 밖의 DB(`todo_dev` 포함)는 확인값 **두 개**가 실제 서버 값과 모두 맞아야 한다. 하나만 있거나 다르면 아무것도 지우지 않고 멈춘다.
  - `alembic -x downgrade_confirm=<DB 이름> downgrade <리비전>`
  - 환경변수 `TODO_DOWNGRADE_CONFIRM=<DB 이름>@<서버 주소>:<포트>` (예: `todo_dev@127.0.0.1:5433`)
- 오프라인 `--sql`은 SQL만 출력하므로 막지 않는다. 그 SQL을 DB에 직접 실행하는 것도 아래 절차를 거친다.
- 승인 절차 (테스트 DB가 아닌 DB의 downgrade)
  1. 대상 DB·리비전·이유·지워지는 테이블을 사용자에게 보고한다.
  2. `pg_dump -Fc`로 백업하고, 백업 파일 크기와 `pg_restore --list` 결과를 확인한다(백업은 Git 제외).
  3. 사용자의 **명시적 승인**을 받는다(대상 DB 이름 포함). 승인은 그 한 번에만 유효하다.
  4. 같은 셸에서만 확인값 두 개를 설정해 실행하고, 끝나면 환경변수를 지운다.
  5. `alembic current`와 테이블 목록으로 결과를 확인해 보고한다.
- 개발 DB에서 하는 일은 `alembic current`, `alembic upgrade head`(각각 승인 후)뿐이다.

테스트·완료 기준
- [x] 기존 332 passed 유지 (DB 없이도 기존 테스트가 돈다) — 1-A
- [x] 보호 장치의 허용·거부 사례(URL·서버 값·downgrade 확인값) 오프라인 테스트 — 1-A 보강
- [x] (1-B, `todo_test`) `alembic upgrade head` → `downgrade base` → `upgrade head` 왕복
- [x] (1-B) 모델과 마이그레이션 차이 없음 (autogenerate 비교 결과 빈 값)
- [x] (1-B) CHECK·UNIQUE 제약 (priority, recurrence+due_date, (user_id, position), 템플릿 lower(name)), 위반은 SAVEPOINT 안에서 확인
- [x] (1-B) 실제 서버 확인 통과 (18.x, 127.0.0.1:5433, todo_test, todo_test_user, 비슈퍼유저)
- [x] (1-B) 연결 TimeZone UTC 확인 (TIMESTAMPTZ를 읽으면 UTC tzinfo), 시퀀스 이동, cascade
- [x] (1-B, 선택·승인 후) `todo_dev`에 `alembic upgrade head`

**1-B 완료 (2026-11-05) — 검증 근거**
- 서버: 로컬 PostgreSQL 18.6, `127.0.0.1:5433`. PostgreSQL 16(5432)에는 연결하지 않았다.
- 테스트: v6 기존 332 passed, DB 오프라인(`test_db_schema.py`) 81 passed, PostgreSQL 통합(`test_db_postgres.py`, `todo_test`) 11 passed, 전체 회귀(integration 제외) **424 passed, 3 deselected**.
- `todo_dev`: 읽기 전용 점검(빈 public, `alembic_version` 없음) → 오프라인 SQL(`upgrade head --sql`) 검토 → `alembic upgrade head` 1회 성공, `alembic current` = `0001 (head)`.
  - 사후 점검: 테이블 7개, IDENTITY 시퀀스 4개, 인덱스 14개, CHECK 5개, FK 5개. `alembic_version` = `0001`(1행), 나머지 테이블 0행.
- **Phase 1(PostgreSQL 기반)만 끝났다. v7.0.0 전체는 미완료다** — 인증(Phase 2), 사용자별 분리(3), v6 기능 연동(4), JSON 이전(5), 릴리스 준비(6)가 남았고, 앱(`main.py`)은 아직 JSON 저장을 쓴다.
- 1-B에서 남긴 후속 사항은 R-16~R-20(§P12)에 있다. Docker 개발 compose 기동은 선택 항목으로 남는다(R-1).

## P2. Phase 2 — 회원가입·로그인·로그아웃 + 시도 제한
작업
1. Alembic `0002_auth_failures`.
2. 인증 로직의 책임 분담 (2026-11-06 확정)
   - `auth.py`: DB·웹 프레임워크 없는 **순수 인증 함수** — Argon2id 해시·검증·rehash, 더미 해시, 토큰·SHA-256, CSRF 비교, Origin 판정, 쿠키 속성, 설정(`APP_ENV`·`COOKIE_SECURE`·`REGISTRATION_ENABLED`) (2-B).
   - `db.py`: **DB 세션 저장** — 생성·조회·삭제·만료 정리(SHA-256만 저장, commit 은 호출자) (2-C). 시도 제한 기록(`auth_failures` + advisory lock, 2-D).
   - (2-D, 2026-11-06 확정) 시도 제한의 순수 부분 — 판정·`Retry-After`(`limit_decision`), username HMAC(`FailureKey`·`load_failure_key`·`login_name`), IP 정규화(`normalize_client_ip`), Argon2 슬롯(`argon2_slot`·`verify_login_password`·`hash_password_limited`)은 `auth.py`. 잠금·검사·선기록·자기 id 삭제·정리(`begin_login_attempt`·`finish_login_success`·`cancel_login_attempt`·`begin_register_attempt`·`purge_old_auth_failures`)는 `db.py`이고 commit 하지 않는다.
   - `main.py`: **HTTP 계층** — `require_user` 의존성(쿠키 → `db.get_active_session` → 401), CSRF·Origin 검사 적용, 클라이언트 IP 결정, `APP_ENV=production` 시작 가드 연결 (2-E). (2-D 확정) 로그인 트랜잭션 구성(Tx1 → Argon2 → Tx2 또는 보상 Tx), 429·500·503과 `Retry-After` 응답, `load_failure_key` 시작 가드도 2-E.
3. `main.py`: `/auth/register`, `/auth/login`, `/auth/logout`, `/auth/me`.
4. `index.html`: 로그인·가입 화면, 헤더 사용자명·로그아웃, `api()`의 CSRF 헤더·401 처리.
   - 이 Phase에서는 데이터 API를 아직 막지 않는다(기존 테스트 유지). 화면은 로그인 후 앱을 보여준다.
5. Docker 포트 공개 시 원래 클라이언트 IP가 보이는지 격리 환경에서 확인(R-12).

테스트·완료 기준 (T-A)
- [ ] 가입: 길이·문자 규칙 422, 대소문자 무시 중복 409, 공백 정규화, 응답에 해시 없음, 해시가 `$argon2id$`로 시작 — (2-E 확인: 422·대소문자 무시 409·응답에 해시 없음. HTTP 수준의 공백 정규화·`$argon2id$` 접두 검사는 아직 없음)
- [x] 로그인 성공 200 + 쿠키, 없는 아이디와 틀린 비밀번호의 401 본문 동일 (2-E: DB 없는 테스트·실제 Argon2, PostgreSQL HTTP)
- [x] 쿠키 속성: HttpOnly, SameSite=Lax, Path=/; `COOKIE_SECURE=true`면 Secure + `__Host-` 이름 (2-E)
- [ ] 로그아웃 뒤 같은 쿠키로 `/auth/me` 401, 14일 지난 세션 401(시계 고정), 재로그인 시 토큰 교체 — (2-E 확인: 로그아웃 뒤 401·재로그인 시 제시된 세션만 교체. 14일 만료는 2-C DB 함수 수준만, HTTP 시계 고정 테스트는 아직 없음)
- [x] CSRF: 헤더 없음·틀림 403, 맞으면 통과. 로그인 Origin 불일치 403 (2-E: logout·`require_csrf`·Origin. 데이터 경로 적용은 Phase 3)
- [x] (2-D DB 함수 + 2-E HTTP) 시도 제한: 5회 실패 후 6번째 429 + `Retry-After`, 15분 뒤 해제, ~~성공 시 기록 삭제~~ → (2026-11-06 변경, `V7_SPEC.md` §29.5 변경 기록) 성공 시 **그 요청의 선기록만** 삭제(이전 실패는 창 만료로 해제), 없는 계정도 같은 규칙, 가입 IP 제한(201·409만 셈), 429는 기록하지 않음
- [x] (2-D, DB 함수 수준 — HTTP 경로가 같은 함수를 쓴다) 동시성: 같은 username에 동시 로그인 실패 요청 N개(스레드)를 보내도 실패 기록이 한도를 넘어 통과하지 않음 (같은 username은 정확히 한도만큼, IP 규칙은 근사)
- [x] (2-D 추가, 2-E HTTP 확인) `username_key`는 HMAC 30자(평문 없음), `AUTH_FAILURE_KEY` 운영 필수, Argon2 슬롯 혼잡·잠금 대기 초과 503(인증 없음), 슬롯 대기 초과 시 자기 선기록만 보상 삭제, 비정상 IP는 인증하지 않음
- [x] 다중 워커: `uvicorn --workers 2` 서버에 실패 요청을 나눠 보내도 한도 적용 (2-F, 2026-11-06: 독립 프로세스 2개 + 실제 `--workers 2`, 아래 2-F 블록)
- [x] `APP_ENV=production` + `COOKIE_SECURE` 미설정이면 시작 실패 (2-E: 실제 lifespan 테스트)
- [x] `REGISTRATION_ENABLED=false`면 가입 403 (2-E, 본문 검증 뒤·시도로 세지 않음)
- [ ] UI: 로그인·가입·로그아웃, 오류 표시, 429 안내, 375px, 키보드
- [x] 기존 332 passed 유지 (2-E 전체 회귀 1015 passed 안에 v6 테스트 전부 통과)

하위 단계 (2026-11-05 설계 검토, 각 단계 보고·승인 후 진행)
| 단계 | 내용 | 상태 |
|---|---|---|
| 2-A | Alembic `0002_auth_failures`, 모델, R-19 게이트 | **완료** (아래, `todo_dev` 0002 적용 완료 2026-11-06) |
| 2-B | `auth.py` DB 없는 순수 함수 + `tests/test_auth_unit.py` | **완료** (아래) |
| 2-C | `db.py` 세션 저장·조회·삭제·만료 정리, 엔진 `hide_parameters` (`require_user`는 2-E) | **완료** (아래). 재로그인 교체 정책(잠정): `replace_token`이 가리키는 세션 하나만 삭제(그 세션의 사용자와 새 로그인 사용자가 달라도 같음), 양쪽 사용자의 다른 세션은 유지, 형식이 틀린 토큰이면 삭제 없음 |
| 2-D | 시도 제한 (`auth_failures`, advisory lock, `Retry-After`, 클라이언트 IP) | **완료 — 로컬 커밋 `7e3b80e`** (아래, 2026-11-06 검증). 순수·DB 함수와 테스트(DB 없는 테스트·`todo_test` PostgreSQL 테스트). HTTP 연결은 2-E |
| 2-E | `/auth/*` 4개 라우트, `require_user`, `get_db` 연결, 시작 가드(`load_settings`), 외부 Origin 결정 | **완료 — 로컬 커밋 `bbe5227`** (아래, 2026-11-06 검증). 외부 Origin은 요청 스킴·`Host`로 정했고 허용 호스트 목록은 R-23(열림) |
| 2-F | 다중 워커 시도 제한 테스트 | **검증 완료 — 로컬 커밋 `53d7534`** (아래, 2026-11-06). 테스트만 추가, 운영 코드·워커 수 설정 변경 없음 |
| 2-G | 로그인·가입 UI, `api()` CSRF 헤더 (v6 화면 강제 로그인 게이트는 Phase 3까지 보류) | 미완료 |

**2-F 다중 워커 검증 완료 (2026-11-06, 로컬 커밋 `53d7534`)**
- 범위: 인증 기능의 재구현이 아니라 **다중 프로세스·다중 워커 사이의 시도 제한(과 세션·CSRF) 공유 검증**이다. 바꾼 파일은 `tests/test_db_postgres.py`뿐이다(운영 코드·마이그레이션·설정 변경 없음, 운영 워커 수는 §P10대로 기본 1개·별도 결정).
- 구성 A — 독립 uvicorn 단일 워커 프로세스 2개(포트 다름, 같은 `todo_test`, 같은 `AUTH_FAILURE_KEY`): A1 사용자((username, IP)) 실패 제한 공유, A2 동시 요청 10개 중 401 정확히 5·429 정확히 5, A3 IP 실패 제한(20) 공유, A4 세션·CSRF 공유. 모든 요청이 127.0.0.1이라 username만의 50회 규칙은 이 환경에서 따로 검증할 수 없다(2-D DB 함수 테스트로 검증).
- 구성 B — 실제 `uvicorn --workers 2`: B1 감독 프로세스 1개와 서로 다른 워커 2개 확인, B2 준비 확인 요청을 뺀 `GET /auth/me`의 워커 분산 확인, B3 서로 다른 워커가 기록한 로그인 실패가 PostgreSQL에서 합산되어 같은 제한 적용. 분산을 관찰하지 못하면 "[검증 미완료]"로 실패시켜 기능 결함과 구분한다(요청·재시도 상한 있음). 워커 식별은 uvicorn 로그(`Started server process [PID]`)와 임시 `--log-config` 접근 로그 형식의 `%(process)d`로만 한다 — 앱 코드에 디버그 헤더·엔드포인트를 넣지 않았다.
- 최종 전체 회귀 실행에서의 관찰값(그 실행에서만의 값이며 매번 같게 재현되지 않는다): B2 워커별 GET 6건·2건, B3 워커별 기록된 실패 4건·1건, B3 401 총 5건·429 총 3건.
- 서버 기동·정리: `subprocess.Popen` + Windows `CREATE_NEW_PROCESS_GROUP`, `python -m uvicorn main:app --host 127.0.0.1 --no-proxy-headers`. 자식 환경에는 검증된 `todo_test` URL을 `DATABASE_URL`로만 준다(`TEST_DATABASE_URL`·`PG*` 제거, 합성 키). 종료는 `CTRL_BREAK_EVENT` 정상 종료 → 감독 프로세스는 Popen 핸들로 종료 → 워커는 부모 PID·생성 시각·명령줄(`spawn_main(parent_pid=…)`)을 Windows CIM으로 확인한 것만 PID별 종료(`taskkill /T` 사용 안 함, 식별이 불확실하면 강제 종료하지 않음) → PID·포트·DB 연결 해제 확인. 확인하지 못하면 `pytest.exit`로 다음 `fresh_schema`를 막는다. 서버 로그에 DETAIL·Failing row·Traceback·키·비밀번호·토큰이 없는지 검사한다.
- 실행 이력(모두 승인 실행, 실행 전후 읽기 전용 R-19 점검 통과, 사후 사용자 데이터 7개 표 0행·테스트용 프로세스 잔존 0):
  - PostgreSQL 단독 2회: 모두 **55 passed, 0 failed**. 첫 실행 뒤 B2 집계에 준비 확인 요청이 섞여 있음을 발견해 집계 구간을 B2 요청으로 한정하고 재실행했다.
  - 전체 회귀 1회차: 823 passed, 1 failed, 3 deselected — 2-F 블록 주석에 쓴 `DROP SCHEMA` 문자열이 안전장치 테스트(`test_fresh_schema_verifies_server_before_drop`, 파일 전체 1회 조건)에 걸려 중단. 주석 한 줄 수정 후 DB 없는 표적 테스트 1 passed, `tests/test_db_schema.py` 전체 **146 passed**.
  - 전체 회귀 2회차(최종): **1020 passed, 0 failed, 3 deselected**(운영 IP 대상 통합 테스트 3개 제외). 해당 안전장치 테스트도 통과.
- 횟수: 스키마 초기화 55회·R-19 게이트 함수 호출 58회는 실행당 코드 구조상 예상값이다(직접 측정 아님). PostgreSQL 단독 `-v` 실행 로그의 마이그레이션 기록 56건은 `fresh_schema` 55회 + 왕복 테스트 내부의 추가 upgrade 1회다.
- 남은 위험: R-12·R-19·R-28·R-31(§P12) — 2-F 검증 완료는 이 위험들의 해결을 뜻하지 않는다.

**2-E 구현·검증 완료 (2026-11-06, 로컬 커밋 `bbe5227`)**
- 설계 A~F(2026-11-06 승인, 계약은 `V7_SPEC.md` §28.1 "Phase 2-E 구현"): 가입 409는 사전 조회 + SAVEPOINT(`uq_users_username`만 409, 다른 제약은 500)이고 409·429는 commit 뒤 응답, `finish_login_success`=False는 Tx2 rollback·500, rehash는 Tx2 전 트랜잭션 밖에서 계산하고 Tx2에서 기존 해시 조건부 UPDATE, 인증 경로 422는 입력값 제거·500은 고정 문구·로그는 사건·예외 종류·SQLSTATE만, 시작 가드는 lifespan, IP는 `request.client.host`만.
- `main.py`: `/auth/register`·`/auth/login`·`/auth/logout`·`/auth/me`, 의존성 `auth_config`(시작 가드 결과)·`auth_session_factory`(트랜잭션마다 새 Session)·`require_same_origin`·`require_user`·`require_csrf`(데이터 경로 적용은 Phase 3), 인증 경로 422 처리기, 공통 오류 처리. v6 데이터 경로는 바꾸지 않았다(JSON, 인증 없음).
- `db.py`: `username_exists`, `create_user`(SAVEPOINT, `UsernameTaken`), `update_password_hash_if_unchanged`, `is_lock_timeout` — commit 하지 않는다. `auth.py`·Alembic은 바꾸지 않았다.
- 테스트: 새 `tests/test_auth_routes.py` **87개**(DB 없음, 가짜 Session·db 함수, 실제 엔진 생성 차단). `tests/test_api.py` 라우트 목록 = v6 + 인증 4개, v6 422 기본 형식 유지 1개. `tests/test_db_schema.py` 계약 변경(2026-11-06 승인): "import main 으로 db·sqlalchemy 미로드" → "import main 만으로 엔진 생성·DB 연결·Alembic 명령 없음, v6 경로는 JSON" — 새 격리 프로세스에서 감시 장치(자체 작동 확인 포함)로 검사. `tests/conftest.py` UI 서버 `APP_ENV=test`. `tests/test_db_postgres.py` HTTP 흐름 **12개** 추가(PostgreSQL 테스트 38 → **50**): 가입·me·logout, 실패 5회 뒤 429, 자기 선기록만 삭제, 409 commit, 실제 UNIQUE 경쟁(SAVEPOINT 409), 실제 NOT NULL 위반 500(409 아님), 동시 같은 이름 가입(201·409), rehash, 재로그인 세션 교체, Tx2 잠금 경합 503, HashBusy 보상, 자기 선기록 없음 500.
- DB 없는 실행 결과(DB·`AUTH_FAILURE_KEY`·`APP_ENV` 환경변수 없음): `test_auth_routes.py` **87 passed**, 전체 회귀 **965 passed, 1 skipped, 3 deselected**(877 + 87 + 1; skipped 1은 `test_db_postgres.py`, 통과 아님).
- `todo_test` 실행 결과(승인 실행 각 1회, 2026-11-06, 실행 전후 읽기 전용 점검: 0002·표 8·시퀀스 5·인덱스 18·타입 16·manifest 일치·게이트 `0002`·다른 세션 0·준비된 트랜잭션 0·연결 수 1·사용자 데이터 7개 표 0행·비슈퍼유저·연결 여유 96):
  - PostgreSQL 단독 `pytest tests/test_db_postgres.py -m postgres -x -v -rP`: **50 passed**(실패 0·skip 0, 22.16초). 스키마 초기화 50회, R-19 게이트 함수 호출 53회.
  - PostgreSQL 포함 전체 회귀 `pytest -m "not integration" -x -q`: **1015 passed, 0 failed, 0 skipped, 3 deselected**(175.23초). 스키마 초기화 50회, R-19 게이트 함수 호출 53회.
  - 2-E 누적: 스키마 초기화 **100회**, R-19 게이트 함수 호출 **106회**. 게이트 호출 수는 별도 계측이 아니라 코드 구조(시작 1 + 테스트마다 지우기 전 1 × 50 + 테스트 안 읽기 전용 2)와 50개 모두 통과한 결과에 근거한 계산이다.
  - 두 실행 모두 사후 `alembic_version` 0002, manifest 일치, **사용자 데이터 표 7개 모두 0행**, 다른 세션·잠금·준비된 트랜잭션 0, 남은 pytest 프로세스 없음. 동시성·잠금 테스트의 연결 정리 시간 초과·남은 스레드 없음. `todo.json` 불변, `task_templates.json` 없음.
  - 실제 PostgreSQL UNIQUE·NOT NULL 위반에서 DETAIL·행 값·비밀번호·traceback이 HTTP 응답과 앱 로그에 없음을 테스트로 확인(단독 실행 로그 전체에서도 0건).
- 앱 시작에 `APP_ENV` 등 설정이 필요해져 현재 운영 compose·Dockerfile 실행 명령으로는 시작이 거부된다(v7은 Phase 6·HTTPS 게이트 전에는 배포하지 않는다, §P10).
- 남은 일: 2-F 다중 워커, 2-G 로그인 UI, Phase 3 데이터 경로 인증. 운영 전 결정: R-12(프록시 헤더), R-23(외부 Origin), R-24(요청 본문 크기). 남은 위험 R-19·R-26·R-27·R-30·R-31(§P12).

**2-D 구현·검증 완료 (2026-11-06, 로컬 커밋 `7e3b80e`)**
- 정책: §P0 "승인됨 (2026-11-06)"과 `V7_SPEC.md` §29.5(변경 기록 포함). 스키마·Alembic은 바꾸지 않았다(HMAC 30자는 VARCHAR(30), IPv6 /64는 VARCHAR(45)에 들어간다).
- `auth.py`: 한도·창 상수, `LimitRule`·`LimitDecision`·`limit_decision`, `FailureKey`(repr·직렬화에 값 없음)·`load_failure_key`·`LoginName`·`login_name`, `InvalidClientAddress`·`normalize_client_ip`, `HashBusy`·`argon2_slot`(`BoundedSemaphore(2)`, 5초)·`verify_login_password`·`hash_password_limited`. 기존 함수와 `load_settings`는 바꾸지 않았다(`AUTH_FAILURE_KEY` 검사는 별도 함수 — 시작 가드 연결은 2-E).
- `db.py`: `begin_login_attempt`(Tx1: `SET LOCAL lock_timeout '2s'` → READ COMMITTED 확인 → 잠금 → 세 규칙 → 선기록 → 해시 조회), `finish_login_success`·`cancel_login_attempt`(같은 잠금 → 자기 id만 삭제), `begin_register_attempt`, `purge_old_auth_failures`(SKIP LOCKED, 1000개씩), `lock_login_username`. commit 하지 않는다.
- 테스트: DB 없는 `tests/test_auth_limits.py`(판정 경계·키 설정·IP·슬롯 동시성·SQL 순서·바인드·자기 id 삭제·commit 없음). PostgreSQL 테스트 11개를 `test_db_postgres.py`에 추가했다(항목 수 27 → 38): HMAC 저장·해시 조회, (u,IP) 한도와 `Retry-After` 895초, username 50·IP 20, 가입 10회/1시간, 자기 id만 삭제, 24시간 정리, 트랜잭션 설정, 잠금 직렬화, 잠금 대기 초과(55P03) 차단, 같은 username 동시 10개 → 정확히 5개, 같은 IP 동시 25개 → 20~25개(근사).
- 실행 전 보강(테스트 파일만): 별도 NullPool 연결에 `connect_timeout` 10초, 작업 스레드는 비-daemon·자기 연결을 `finally`에서 닫음·예외 시 Barrier 해제, join 60초 뒤 살아 있는 스레드는 실패 처리(이후 PostgreSQL 테스트 중단), 동시성·잠금 테스트 4개는 연결을 닫은 뒤 R-19 게이트와 같은 조건(연결 수 1·다른 세션 0)으로 돌아올 때까지 최대 10초 읽기만 하고 기다린다(게이트·대상·매니페스트는 바꾸지 않음, 세션을 끊지 않음).
- DB 없는 실행 결과(DB·`AUTH_FAILURE_KEY`·`APP_ENV` 환경변수 없음): `test_auth_limits.py` **128 passed**(3회 반복 동일), 2-B·2-C·스키마 포함 545 passed, 전체 회귀 **877 passed, 1 skipped, 3 deselected**(749 + 128; skipped 1은 `test_db_postgres.py`, 통과 아님). 보강 후에도 같다.
- `todo_test` 실행 결과(승인 실행, 2026-11-06, PostgreSQL 18.6, 실행 전 읽기 전용 점검: 0002·표 8·시퀀스 5·인덱스 18·타입 16·게이트 통과·다른 세션 0·연결 수 1·사용자 데이터 0행·비슈퍼유저·연결 여유 96):
  - PostgreSQL 단독 `pytest tests/test_db_postgres.py -m postgres -x -v -rP`: **38 passed**(실패 0·skip 0, 14.02초). 스키마 초기화 38회, R-19 게이트 함수 호출 41회.
  - PostgreSQL 포함 전체 회귀 `pytest -m "not integration" -x -q`: **915 passed, 3 deselected**(실패 0·skip 0, 168.93초, 종료 코드 0). 스키마 초기화 38회, R-19 게이트 함수 호출 41회.
  - 누적: 스키마 초기화 **76회**, R-19 게이트 함수 호출 **82회**. 게이트 호출 수는 별도 계측이 아니라 코드 구조(시작 1 + 테스트마다 지우기 전 1 × 38 + 테스트 안 읽기 전용 2)와 38개 모두 통과한 결과에 근거한 계산이다.
  - 동시성·잠금 테스트 4개 통과, 연결 정리 대기 시간 초과·남은 스레드 없음. 종료 후 읽기 전용 점검(두 번 모두): `alembic_version` 0002, 표 8·시퀀스 5·인덱스 18·타입 16, 게이트 판정 `0002`, 다른 세션 0·연결 수 1, **사용자 데이터 표 7개 모두 0행**. `todo.json` 불변, `task_templates.json` 없음.
- 2-E로 넘기는 일(2-D 완료에 포함되지 않음): 실제 IP 결정·`InvalidClientAddress` 처리, 로그인 Tx1 → 트랜잭션 밖 검증 → Tx2/보상 Tx 구성, 429·500·503과 `Retry-After` 응답, 가입 409 집계(사용자 INSERT를 SAVEPOINT 안에서 하거나 중복을 먼저 조회 — 아니면 409가 시도 기록까지 되돌린다)·사용자 생성 헬퍼, `finish_login_success`가 False일 때의 처리(실패로 닫는 쪽 권장), rehash 저장, `load_failure_key` 시작 가드 연결, 라우트에서는 슬롯 함수(`verify_login_password`·`hash_password_limited`)만 쓰는지 검사. 2-F 다중 워커.

**2-C 구현·검증 완료 (2026-11-06)**
- `db.py`: `create_session`(토큰·CSRF 발급, SHA-256만 저장, 만료 = 생성 + 14일 고정, `replace_token`이 가리키는 세션 하나만 삭제), `get_active_session`(`expires_at > now`인 행만, 조회는 만료 행을 지우지 않음), `delete_session`(멱등, 형식이 틀린 토큰은 DB 호출 없음), `purge_expired_sessions`(모든 사용자, `expires_at <= now`), `is_valid_session_token`(43자·URL-safe·32바이트·정규 표기). 함수는 commit 하지 않는다(트랜잭션은 호출자 책임). 엔진 `hide_parameters=True`. `auth.py`·`main.py`는 바꾸지 않았다(`require_user`는 2-E).
- 테스트: DB 없는 `tests/test_auth_sessions.py` 80개, DB 없는 전체 회귀 **749 passed, 1 skipped, 3 deselected**(skipped 1은 `test_db_postgres.py`, 통과 아님). `todo_test`(승인 실행): PostgreSQL 테스트 **27 passed**(2-C 신규 11), PostgreSQL 포함 전체 회귀 **776 passed, 3 deselected**. 스키마 초기화 54회·R-19 게이트 56회 모두 통과.
- 테스트 격리 보완: 처음 실행(2-C3)에서 마지막 테스트가 `with conn.begin()` 정상 종료로 합성 데이터(users 1·sessions 3)를 커밋해 남겼다. 단일 트랜잭션 테스트 17개를 끝나면 항상 되돌리는 `rolled_back`으로 바꾸고 재검증했다(위 수치). 종료 후 `todo_test`: `alembic_version` 0002, 표 8·시퀀스 5·인덱스 18, **사용자 데이터 표 7개 모두 0행**, 게이트 판정 `0002`. `fresh_schema`·R-19 게이트는 바꾸지 않았다.
- 제한: 커밋 자체를 검사하는 테스트 2개(`test_session_rollback_leaves_nothing`, `test_logout_commit_is_seen_by_other_connection`)는 의도적으로 커밋한다. 단독(`-k`)이나 마지막 순서로 실행하면 `users` 1행이 남을 수 있다(기본 순서에서는 이후 `fresh_schema`가 지운다). 다음 PostgreSQL 실행 전 사전 점검은 실제 행 수를 확인한다.
- 남은 위험: R-19 잔여(§P12), R-26(세션 수 상한 없음), R-27(미해결, ③ 앱 로그·HTTP 응답은 2-E).

**2-B 완료 (2026-11-05, 로컬 커밋 `b4d3ad4`)**
- `auth.py`: `load_settings`·`AuthSettings`(APP_ENV·COOKIE_SECURE·REGISTRATION_ENABLED, production이면 Secure 필수), `normalize_username`, `check_password`, `hash_password`·`check_password_hash`(OK·MISMATCH·INVALID_HASH)·`verify_password`·`needs_rehash`·`verify_dummy`(더미 해시는 처음 쓸 때 한 번 생성), 세션·CSRF 토큰과 SHA-256, `csrf_matches`(bytes + `hmac.compare_digest`), 14일 만료, `WebOrigin`·`origin_allowed`(스킴·호스트·포트 비교), 쿠키 생성·삭제 인자. 표준 라이브러리와 argon2만 import, import 시 해시 계산 없음.
- 보안 보완(같은 날): R-25 Origin·Referer 제어 문자·비ASCII 거부(파싱 전 검사), R-24 검증 경로 입력 방어선(128자·UTF-8 512바이트 초과·인코딩 불가 입력은 Argon2에 넘기지 않고 계정 유무와 관계없이 같은 빠른 실패).
- 테스트: `tests/test_auth_unit.py` **191 passed**. 전체 회귀(DB 환경변수 없음, integration 제외) **604 passed, 1 skipped, 3 deselected** — skipped 1은 `test_db_postgres.py`(TEST_DATABASE_URL 없음, 통과 아님), deselected 3은 integration(미실행). 기존 413(v6 332 + DB 오프라인 81)은 그대로 통과.
- 2-E로 넘기는 사항: R-21(422 응답의 비밀번호 원문), R-22(APP_ENV 기본값), R-23(외부 Origin·Host 신뢰), R-24 중 남은 부분(HTTP 본문 크기 제한·인증 API 422 처리).

**2-A 완료 (2026-11-05)**
- 모델·마이그레이션: `db.AuthFailure`와 Alembic `0002_auth_failures`(down_revision `0001`, 단일 선형). upgrade는 `auth_failures`와 인덱스 3개만 만들고 0001 객체는 바꾸지 않는다. downgrade는 첫 줄 `db.guard_downgrade` 후 `auth_failures` 관련 객체만 지운다. 구조는 `V7_SPEC.md` §27.2. D-12는 이 스키마 구현에 한해 잠정 채택(정식 승인 상태는 그대로).
- R-19 게이트(`db.verify_test_schema`): `fresh_schema`가 같은 트랜잭션에서 서버 확인 → 게이트 → `lock_timeout 5s` → `DROP SCHEMA` 순서로 실행하고, `engine` 픽스처도 처음에 한 번 확인한다. 허용 상태는 빈 스키마·base·0001·0002(리비전별 고정 목록, 오프라인 SQL과 일치를 테스트로 강제). 객체 종류·소유자·리비전·DB/스키마 소유자·함수·타입·확장·트리거·이벤트 트리거·같은 역할의 다른 세션(`pg_stat_activity`)과 **모든 역할의 연결 수(`pg_stat_database.numbackends` = 1)**를 확인하고, 어긋나거나 확인할 수 없으면 지우지 않고 세션을 멈춘다. 데이터는 알려진 표의 행 수만 센다.
- 테스트: DB 없는 스키마 테스트 **146 passed**, DB 없는 전체 회귀 **669 passed, 1 skipped, 3 deselected**. `todo_test`(1회 승인 실행): PostgreSQL 테스트 **16 passed**, PostgreSQL 포함 전체 회귀 **685 passed, 3 deselected**. 스키마 초기화 32회·게이트 34회 모두 통과.
- 종료 후 `todo_test`: `alembic_version` 0002, 표 8·시퀀스 5·인덱스 18, 사용자 데이터 0행, 게이트 판정 `0002`.
- ~~`todo_dev`는 0001에 머문다. 0002 적용(2-A4)은 2-E 직전에 별도 승인으로 한다(Phase 1과 같은 절차).~~ → **2-A4 완료 (2026-11-06, 2-E 직전 별도 승인)**: `todo_dev`에 Alembic 0002를 1회 적용했다. 저장소 밖의 일회성 Python 래퍼가 Alembic CLI 진입점(`upgrade 0002`)을 호출하고, Alembic이 실제로 쓰는 연결에만 세션 한정 `lock_timeout=5s`·`statement_timeout=30s`를 기존 `-c TimeZone=UTC` 뒤에 덧붙였다(`PGOPTIONS`는 `db.make_engine`의 `options` 연결 인자에 가려 적용되지 않음을 확인해 쓰지 않았다). 그 연결의 첫 접속 훅에서 DDL 전에 대상·버전·두 타임아웃(설정 출처 `client`)·다른 세션 0을 확인했다. 적용 전 0001·7/4/14/14·사용자 데이터 0행, 적용 후 0002·표 8·시퀀스 5·인덱스 18·타입 16, 기존 열·제약·인덱스 불변, 모델과 차이 없음, 사용자 데이터 0행·`auth_failures` 0행. 서버·역할·DB 설정은 바꾸지 않았다.
- R-19 잔여 위험은 §P12 R-19 행에 있다.

## P3. Phase 3 — 사용자별 데이터 분리와 접근 권한
작업
1. Alembic `0003`이 필요하면 추가(Phase 1 스키마에 이미 있으면 생략).
2. 저장 계층 교체: `load_todos/save_todos/read_list/write_list`를 `db.py` 헬퍼로 바꾼다.
   - `active_todos(db, uid)`, `get_owned_todo(db, uid, id)`, `insert_todo`(position), `soft_delete`, `restore`, `purge_expired`, 템플릿 헬퍼.
   - 쓰기 요청은 `lock_user(db, uid)`(`SELECT ... FOR UPDATE`)로 시작.
3. 엔드포인트 15개에 `require_user` 적용. `next_id()`·`RECENTLY_DELETED` 제거(D-7 승인 시).
4. 순수 로직(`apply_completion`, `next_occurrence`, `add_months`, `compute_stats`, `todos_to_csv`)은 그대로 두고 입력만 DB에서 만든 `TodoItem` 목록으로 바꾼다.
5. 앱 시작 시 `todo.json` 생성 코드 제거. `TODO_FILE`·`TEMPLATE_FILE`은 런타임에서 쓰지 않는다(이전 도구만 쓴다).

테스트·완료 기준 (T-B)
- [ ] 15개 경로 × 익명 401 매트릭스
- [ ] 15개 경로 × 사용자 B가 A의 리소스 id로 요청 → 404, A의 데이터 불변
- [ ] 목록·stats·export(JSON·CSV)에 다른 사용자 데이터 없음, 템플릿 목록 분리
- [ ] bulk에 A·B id 혼합 → 404, 양쪽 모두 불변
- [ ] restore: B가 A의 지운 id → 404, A는 계속 복구 가능
- [ ] 템플릿 이름: A와 B가 같은 이름 가능, 같은 사용자 안 중복 409
- [ ] 본문의 `user_id`, `id`, `position`, `deleted_at` 무시
- [ ] soft delete: 지운 직후 GET·stats·export·bulk·PUT·하위 API에서 안 보임, 30초 안 복구, 30초 뒤 404, 100건 초과 시 오래된 것 정리, 재시작(엔진 재생성) 뒤에도 30초 안 복구
- [ ] position: 추가·반복 생성·삭제·복구 후 순서가 v6과 같은 시나리오 표로 확인
- [ ] 동시 요청: 같은 사용자 POST 병렬 → position 충돌 없음, bulk와 restore 병렬 → 일관
- [ ] position과 soft delete (`V7_SPEC.md` §27.3): max 계산에 지운 대기 행 포함(대기 행이 max일 때 추가 → 더 큰 번호, 복구 후 순서 유지), 완전 삭제 뒤 번호 재사용 허용, 반복 생성·템플릿 생성도 같은 규칙
- [ ] 사용자 행 잠금: 쓰기 경로가 모두 `lock_user`로 시작(라우트·헬퍼 테스트), 잠금 없이 병렬 생성하면 실패함을 보이는 대조 테스트, 생성·삭제·복구 병렬 실행 후 position 중복·누락 없음
- [ ] 라우트 목록 테스트: 데이터 경로 15개 모두 `require_user` 의존성 보유
- [ ] 템플릿 이름 중복(409) 기준: DB `lower(name)` 고유 인덱스와 앱 검사가 같은 결과인지 ASCII·한글·특수 문자(İ/ı, ß, 전각 등)로 확인 (R-16)

## P4. Phase 4 — 기존 v6 기능과 DB·인증 연동
작업
1. 기존 테스트 이전 (§P8): `client` 로그인 픽스처, `seed`를 DB 쓰기로, UI `page`에 로그인 쿠키.
2. 반복·하위 할 일·템플릿·내보내기·통계·칸반·알림이 DB와 인증 위에서 동작 확인.
3. 알림 읽음 키 `todo.v7.notifications.read.<userId>`.
4. 화면 버전 `Version 7.0.0`, B22·`/` 테스트 갱신.

테스트·완료 기준
- [ ] 기존 v4~v6 테스트 전부 통과 (의미가 바뀐 테스트는 §P8 목록대로만 변경)
- [ ] KST 규칙(§20) 회귀 없음: LA 시간대 브라우저 테스트, `/stats` 경계 테스트
- [ ] 두 사용자를 브라우저 컨텍스트 2개로 띄워 목록·칸반·캘린더·통계·알림·템플릿 격리
- [ ] 같은 브라우저에서 사용자 전환 시 알림 읽음 기록이 섞이거나 지워지지 않음
- [ ] 로그아웃 후 화면에 이전 사용자 데이터가 남지 않음, 세션 만료 시 401 → 로그인 화면

## P5. Phase 5 — JSON 데이터 이전 리허설 (개발 환경 전용)
### 5.1 도구 (`migrate_json.py`)
```
python migrate_json.py import    --todos <사본>/todo.json --templates <사본>/task_templates.json --owner <username> [--dry-run]
python migrate_json.py verify    --todos <사본>/todo.json --templates <사본>/task_templates.json --owner <username>
python migrate_json.py export-v6 --owner <username> --out <폴더>
python migrate_json.py diff      <v6 JSON 폴더 A> <v6 JSON 폴더 B 또는 --owner <username>>
```
- `DATABASE_URL`은 환경변수로만 받는다. 출력은 건수·id·필드 이름만(내용 미출력).
- `import`: 원본 sha256 기록 → v6 모델 검증 → 사전 거부 조건 → 한 트랜잭션 삽입 → `setval` → `data_migrations` 기록 → 원본 sha256 재확인.
- `verify`: 원본을 v6 규칙으로 읽은 결과와 DB의 그 사용자 데이터를 항목·필드 단위로 비교. 같은 시계에서 `compute_stats` 결과 비교. 차이가 있으면 실패 코드.

### 5.2 리허설 절차
1. **[승인 필요] 운영 백업 사본 확보** — 운영 서버에서 읽기만 한다(컨테이너·볼륨 변경 없음).
   ```bash
   export COMPOSE_PROJECT_NAME=fastapi_todos
   B=backups/v7-rehearsal-$(date +%Y%m%d-%H%M%S); mkdir -p "$B"; chmod 700 "$B"
   docker compose cp fastapi-app:/app/data/. "$B/"     # README "백업"과 같은 명령
   sha256sum "$B"/*.json | tee "$B/SHA256SUMS"; chmod 600 "$B"/*
   ```
   사본을 개발 환경으로 옮길 때는 암호화한다(`age`/`gpg`). 운영 서버의 사본은 그대로 둔다.
2. 개발 환경: `sha256sum -c SHA256SUMS`로 사본 무결성 확인.
3. 개발 DB(`todo_dev`)를 새로 만들고 `alembic upgrade head`, 소유자 계정 가입.
4. `import --dry-run` → 리포트 확인(건수, 순서가 id 순과 다른지, 시간대 없는 시각 수, 예전 형식 수, 실패 항목).
5. `import` → `verify` → `export-v6` → `diff`(원본 사본 vs 역내보내기, 차이 0이어야 함. 시간대 표기 정규화 후).
6. 앱을 개발 DB로 띄워 소유자로 로그인, 화면 확인(목록 순서, 하위 할 일, 반복 배지, 통계, 캘린더, 칸반, 알림, 템플릿, 내보내기).
7. `pg_dump -Fc` 결과를 만들고 빈 DB에 `pg_restore`해 다시 `verify` (백업 복원 가능성 확인).
8. DB를 지우고 3~7을 한 번 더 반복해 재현성 확인.
9. 결과 보고(건수·검증 결과·이상 항목 id). 보관 기간(D-15)이 끝나면 개발 DB·사본 삭제, 삭제 기록.

### 5.3 완료 기준 (T-C)
- [ ] v1·v3·v4·v5·v6 형식 합성 데이터 각각 이전·검증 통과
- [ ] id, position(배열 순서), created_at(null 포함), completed_at, next_spawned, 하위 할 일 sid·순서, 템플릿 id·순서 보존
- [ ] 시간대 없는 시각 → UTC, `due_date: ""` → null
- [ ] 깨진 항목 1개 → 아무것도 들어가지 않음, 실패 id 보고
- [ ] `--dry-run` 후 DB 변화 없음, 같은 원본 재이전 거부, id 충돌 거부, 소유자 없음 거부
- [ ] 이전 후 첫 POST id = 최대 id + 1, 새 항목 position = 끝
- [ ] 원본 사본 sha256 불변
- [ ] `export-v6` 결과가 v6 앱(`main.py` v6.0.0)에서 그대로 읽힘 (v6 모델 검증)
- [ ] 이전 전 v6 `/stats`와 이전 후 v7 `/stats`가 같은 시계에서 같음
- [ ] 출력에 제목·설명 등 내용이 없음

## P6. 운영 전환·백업·검증·롤백 절차 (문서 — 실제 실행은 Phase 6 이후 별도 승인)
### 6.1 보존 원칙
- 기존 JSON 볼륨 `fastapi_todos_fastapi-data`는 전환 후에도 **지우지도, 쓰지도 않는다.** v7 컨테이너에는 마운트하지 않는다(실수로 쓰는 것 방지).
- v6 이미지는 태그(`fastapi-todos:6.0.0`)로 보관한다.
- PostgreSQL 데이터는 새 볼륨 `fastapi_todos_pg-data`에 두고, 전환 직후·매 배포 전·롤백 직전에 `pg_dump -Fc`를 남긴다.
- 모든 백업에 `SHA256SUMS`를 두고, 최소 2곳(서버의 `backups/`, 서버 밖 암호화 사본)에 보관한다.

### 6.2 전환 순서
| 단계 | 내용 | 확인 |
|---|---|---|
| 0 | §P7 HTTPS 게이트 통과 확인 | 체크리스트 전부 ✅ |
| 1 | 쓰기 중단 공지 후 v6 백업: JSON 사본 + `docker commit` 스냅샷 + v6 이미지 태그 | SHA256SUMS |
| 2 | v6 컨테이너 중지 **[승인]** (마지막 백업 이후 쓰기가 생기지 않게) | 중지 후 다시 sha256 비교, 바뀌었으면 1 반복 |
| 3 | postgres 서비스 기동, `alembic upgrade head` | `alembic current` = head |
| 4 | 소유자 계정 생성 (`REGISTRATION_ENABLED=true`로 한 번 또는 관리 명령) | `/auth/me` |
| 5 | `import --dry-run` → `import` → `verify` | 차이 0 |
| 6 | 전환 직후 `pg_dump -Fc` (기준점 B0) | 복원 테스트 |
| 7 | v7 앱 기동 (`REGISTRATION_ENABLED=false`) | 로그인, 건수, 화면 기능, `/metrics` 수집 |
| 8 | 관찰 기간 7일: 매일 `pg_dump` + 소유자 `export-v6` 스냅샷 | 오류 로그, 메트릭 |
| 9 | 관찰 기간 종료 후에만 가입 공개 여부 결정 **[승인]** | |

### 6.3 롤백 단계
| 수준 | 상황 | 절차 | 데이터 |
|---|---|---|---|
| L0 | 5단계 전 실패 | v7 중지, v6 컨테이너 다시 시작 | JSON 볼륨 그대로 → 손실 없음 |
| L1 | 7단계 후, 사용자 쓰기 전 | v7 중지, v6 재시작 (원래 볼륨) | 손실 없음 (B0 = JSON 내용) |
| L2 | 사용자 쓰기 후 | §6.4 | v7 변경분을 역내보내기로 v6에 옮김 |

### 6.4 v7 신규 데이터와 v6 롤백 (L2)
문제: v6은 원래 JSON 볼륨만 읽으므로, v7에서 새로 만들거나 바꾼 데이터는 그대로 롤백하면 보이지 않는다. 다른 사용자의 데이터는 v6(단일 사용자)에 아예 담을 수 없다.

대응
1. **예방**: 관찰 기간에는 가입을 막아(D-13) 사용자를 소유자 1명으로 제한한다. 이 기간에 롤백하면 v6이 표현할 수 없는 데이터가 생기지 않는다.
2. **v7 쓰기 중단**: v7 컨테이너 중지 **[승인]** → 롤백 직전 `pg_dump -Fc`(B-rb) + SHA256SUMS. PostgreSQL 볼륨은 그대로 둔다.
3. **역내보내기**: `migrate_json.py export-v6 --owner <소유자>` → v6 형식 `todo.json`·`task_templates.json` (id·순서 보존, 활성 데이터만). `diff`로 원래 JSON 사본과 비교해 v7 기간의 추가·변경·삭제 id를 기록한다.
4. **새 볼륨에 적재**: 원래 볼륨에는 쓰지 않는다. 새 볼륨 `fastapi_todos_fastapi-data-rb-<시각>`에 `scripts/load-data.sh`로 적재하고, v6 컨테이너가 그 볼륨을 쓰도록 compose override로 띄운다 **[승인]**.
5. **다른 사용자 데이터**(가입을 열었던 경우): v6에서는 보이지 않지만 PostgreSQL과 B-rb에 그대로 남는다. 필요하면 사용자별 `export-v6` 파일을 만들어 안전하게 전달한다. 공지 문구: "롤백 기간 동안 보이지 않으며 삭제되지 않았습니다."
6. **재전환(v7 복귀)**: B-rb 시점 DB를 그대로 쓰고, 롤백 기간에 v6에서 바뀐 소유자 데이터는 `diff`(B-rb의 export-v6 vs 롤백 볼륨 현재 JSON)로 확인해 반영한다. 자동 병합은 범위 밖이다. 롤백 기간을 짧게 하고 그동안 쓰기를 최소화하도록 공지한다.
7. 모든 단계의 볼륨·덤프는 재전환 검증 후 보관 기간이 지나기 전까지 지우지 않는다.

## P7. 운영 공개 조건 (HTTPS 게이트)
다음이 모두 확인되기 전에는 v7을 운영에 배포·공개하지 않는다.
- [ ] 운영 도메인에 TLS 종단(리버스 프록시 또는 로드밸런서)이 있고 HTTP는 HTTPS로 리다이렉트
- [ ] 앱은 프록시 뒤에서만 접근 가능 (5001 직접 공개 여부 결정 — 현재 모든 인터페이스에 바인딩, OWASP A02-3)
- [ ] uvicorn `--proxy-headers --forwarded-allow-ips=<프록시 주소>`
- [ ] `APP_ENV=production`, `COOKIE_SECURE=true` (`__Host-` 쿠키, Secure)
- [ ] HSTS 헤더 (프록시 또는 앱)
- [ ] `REGISTRATION_ENABLED=false`
- [ ] DB 비밀번호가 저장소에 없음 (Jenkins Credentials 또는 서버의 권한 600 `.env`)
- [ ] PostgreSQL 포트 외부 미공개 (`ports` 없음)
- [ ] 시도 제한이 실제 클라이언트 IP로 동작 (R-12)

## P8. 기존 테스트 유지·변경 계획
### 8.1 기준
- v6.0.0: `332 passed, 3 deselected` (2026-11-05, 143.95초). deselected 3개는 `test_integration.py`.
- 목표: 기존 332개는 의미를 바꾸지 않고 **픽스처만 교체**해 통과. 새 테스트는 추가로 센다.

### 8.2 픽스처 교체
| 대상 | v6 | v7 |
|---|---|---|
| `test_api.py` `isolated_file` | `TODO_FILE`·`TEMPLATE_FILE`·`RECENTLY_DELETED` monkeypatch | `db` 픽스처(test DB TRUNCATE) + 로그인한 사용자 |
| `client = TestClient(app)` (모듈 전역) | 익명 | 로그인 쿠키 + `X-CSRF-Token` 기본 헤더를 가진 픽스처. 테스트 본문의 `client.post(...)` 형태는 유지 |
| `load_todos()/save_todos()/write_text` 직접 호출 (약 16곳) | 파일 | `seed_todos(user, [dict])`, `read_todos(user)` 헬퍼 (v6 모델로 검증 후 DB 삽입) |
| `monkeypatch(main, "now")` (6곳) | 동작 | 그대로 동작 (시각을 파이썬에서 넣음) |
| `conftest.py` `seed` (UI, 96곳) | 파일에 쓰기 | 같은 시그니처로 테스트 사용자 DB에 쓰기, `seed.templates` 유지 |
| UI `page` | 익명 | 테스트 사용자 세션 쿠키를 넣은 컨텍스트 |
| UI 서버 subprocess | `TODO_FILE`·`TEMPLATE_FILE` env | `DATABASE_URL`=test DB, `REGISTRATION_ENABLED=true` |
| A22 해시 보호 | 유지 | 유지 + 비 `_test` DB 거부 |

### 8.3 의미가 바뀌는 테스트 (변경 목록 — 이 외에는 바꾸지 않는다)
| 테스트 | 변경 |
|---|---|
| A19·v1~v4 데이터 호환 테스트 (파일에 예전 형식을 쓰고 GET) | 런타임은 JSON을 읽지 않으므로 **이전 테스트**로 옮김: 같은 입력 → `migrate_json import` → GET 결과 동일 |
| `test_v6_routes_are_v5_plus_stats_only` | `V7_ROUTES` = v6 경로 + `/auth/*` 4개 |
| B22, `/` 응답 버전 테스트 | `Version 7.0.0` |
| 알림 테스트 중 `todo.v6.notifications.read` 사용 1곳 | 사용자별 키 |
| `RECENTLY_DELETED` 직접 참조·30초 경과 테스트 | soft delete 기준(시계 고정)으로 같은 결과 확인 |
| A21 (최대 id + 1) | 단일 사용자·빈 DB에서 그대로 통과. 다중 사용자 시퀀스 동작은 새 테스트로 명시 |
| `test_integration.py` | 로그인 흐름으로 다시 작성. **실행은 명시적 승인 후** (대상 서버에 테스트 계정·데이터가 생긴다) |

### 8.4 새 테스트 묶음
- T-A 인증 (§P2), T-B 격리·soft delete·position (§P3), T-C 이전 (§P5), T-D Alembic (§P1), T-E UI 인증·다중 사용자 (§P2·§P4).
- 보안 회귀: 응답·로그에 해시·토큰 없음, 사용자 입력 `textContent` 출력 유지, CSV 수식 방지 유지.

### 8.5 실행 시간
DB 테스트로 늘어나는 시간은 테스트마다 TRUNCATE, UI 서버는 세션당 1개로 억제한다. Phase마다 소요 시간을 보고한다.

## P9. 개인정보 처리 (D-14)
확인된 사실은 `V7_SPEC.md` §32.1. 선택지:

| 선택지 | 내용 | 효과 | 위험·비용 |
|---|---|---|---|
| A. 그대로 둠 | 아무것도 바꾸지 않음 | 없음 | 공개 저장소면 계속 노출 |
| B. 앞으로만 정리 | 이후 커밋에서 `fastapi-app/todo.json`을 합성 예시로 바꾸거나 추적 해제(`git rm --cached` + `.gitignore`) | 최신 트리에서 사라짐 | 기록(`98e98f4` 등)에는 남음. `conftest.py` 해시 보호가 파일 존재를 전제하므로 테스트 조정 필요 |
| C. 기록 재작성 | `git filter-repo`로 모든 커밋에서 값 제거 후 강제 푸시 | 기록에서 제거 | **파괴적**. 모든 브랜치 해시 변경, 협업자·PR·포크·GitHub 캐시에는 남을 수 있음, 별도 명시적 승인 필수 |
| D. 저장소 공개 범위 점검 | 저장소가 공개인지 확인하고 필요하면 비공개 전환 | 노출 범위 축소 | GitHub 설정 변경 — 사용자가 직접 |

- 권장: D 확인 → B를 v7 작업 중 별도 커밋으로(승인 후). C는 공개 저장소이고 삭제가 꼭 필요할 때만 따로 결정.
- 로컬 stash `stash@{0}`(`d66b8fb`)는 push되지 않았다. 지우는 것도 파괴적 명령이라 사용자가 결정한다.
- 운영 데이터 사본 처리 원칙은 `V7_SPEC.md` §32.2.

## P10. Docker Compose·Jenkins 영향 (적용은 Phase 6 이후 승인)
**운영 `docker-compose.yml` 변경안**
- `postgres` 서비스: 버전 고정(D-16), `ports` 없음, 볼륨 `fastapi_todos_pg-data`(이름 고정), `pg_isready` healthcheck, `no-new-privileges`, 비밀번호는 `.env`/secret.
- `fastapi-app`: `DATABASE_URL`, `APP_ENV`, `COOKIE_SECURE`, `REGISTRATION_ENABLED`, (2-D) `AUTH_FAILURE_KEY`(비밀값 — Jenkins Credentials 또는 서버 권한 600 파일, 모든 워커 같은 값, R-28) 추가, `depends_on: postgres: condition: service_healthy`. `fastapi-data` 볼륨 마운트는 v7에서 제거(롤백용으로 볼륨 정의는 유지, 데이터 보존).
- 워커 수: soft delete·DB 시도 제한이라 다중 워커 가능. 기본은 1개 유지, 늘리는 것은 별도 결정.
- SonarQube·Prometheus·Grafana·node-exporter 변경 없음. Prometheus는 계속 `fastapi-app:8000`. (cAdvisor는 이 저장소 compose에 없음 — 운영에서 별도 실행 중인지 확인 필요.)

**Jenkins 변경안** (저장소에 Jenkinsfile 없음, README 예시 기준)
```groovy
environment {
    COMPOSE_PROJECT_NAME = 'fastapi_todos'
    POSTGRES_PASSWORD = credentials('fastapi-todos-pg-password')
}
stages {
    stage('Backup')  { steps { sh './scripts/backup-v7.sh' } }          // pg_dump -Fc + SHA256SUMS (Phase 6에서 작성 여부 결정)
    stage('Build')   { steps { sh 'docker compose build fastapi-app' } }
    stage('DB')      { steps { sh 'docker compose up -d postgres' } }
    stage('Migrate') { steps { sh 'docker compose run --rm --no-deps fastapi-app alembic upgrade head' } }
    stage('Deploy')  { steps { sh 'docker compose up -d --no-deps fastapi-app' } }
}
```
- 현재 `up -d --no-deps fastapi-app`만으로는 postgres가 뜨지 않는다(R-6).
- `down -v`, `volume prune` 금지 유지. JSON → DB 이전은 Jenkins가 아니라 §P6 수동 절차로 한 번만 한다.

## P11. Phase 6 — 전체 테스트·보안 점검·릴리스 준비
- 전체 pytest(integration 제외), Alembic 왕복, 이미지 빌드(`alembic/`·`db.py`·`auth.py` 포함, `tests/` 제외 확인).
- Python 3.13(Docker 이미지)과 C.UTF-8 PostgreSQL에서 DB 테스트·`lower(name)`·정렬 관련 테스트 재실행 (R-16, R-20). 로컬 1-B 검증은 Python 3.14·Windows 로캘 기준이다.
- 격리 Compose 프로젝트(`fastapi-v7test`, `127.0.0.1` 포트, 테스트 볼륨)에서 postgres+앱 실행, 컨테이너 재생성 후 데이터 유지(C5), `/metrics` 확인.
- OWASP 재점검: A01 접근 제어(격리 매트릭스), A07 인증 실패(시도 제한·세션), CSRF, 보안 헤더, 의존성 CVE.
- 문서: README(실행·DB·이전·백업·롤백), CLAUDE.md DoD 체크, `RELEASE_NOTES.md` 7.0.0, Jenkins 변경안.
- `test_integration.py` 재작성. 실행은 승인 후.

## P12. 위험 목록
| ID | 위험 | 대응 |
|---|---|---|
| R-1 | 이 VDI에서 Docker 사용 불가 → PG 테스트 불가 | 로컬 PostgreSQL 18.6(127.0.0.1:5433)으로 대체 (§P1). PostgreSQL 16(5432)은 보호 장치가 거부 |
| R-2 | DB 테스트로 실행 시간 증가 | TRUNCATE, 세션 단위 서버, 소요 시간 보고 |
| R-3 | psycopg가 시간대 오프셋을 붙여 응답 형식 변경 | 연결 TimeZone UTC, 형식 회귀 테스트 |
| R-4 | 운영 데이터 형식 미확인 (검증 실패·순서 불일치) | 사본 리허설, dry-run 리포트 |
| R-5 | soft delete 조회 누락으로 지운 항목 노출 | 헬퍼 강제, 전 경로 테스트 |
| R-6 | Jenkins `--no-deps`로 DB 없이 배포 | 파이프라인 변경 (승인 후) |
| R-7 | 운영 HTTPS 미확인 | §P7 게이트, `APP_ENV` 시작 가드 |
| R-8 | `/metrics` 외부 노출 (기존) | 범위 밖, 프록시 단계에서 차단 검토 |
| R-9 | 롤백 시 v7 데이터 미표시 | §P6.4 (가입 제한·역내보내기·새 볼륨) |
| R-10 | 통합 테스트 대상 `:5014`와 운영 `:5001` 불일치 | 대상 서버 확인 후 재작성 |
| R-11 | 저장소에 개인정보로 보이는 값 | §P9 결정 |
| R-12 | Docker NAT·프록시로 모든 요청이 같은 IP → IP 제한이 전역 제한이 됨 | Phase 2 격리 환경 확인, 프록시 헤더 신뢰 설정 (2-E, 2026-11-06) 앱은 ASGI `request.client.host`만 쓰고 `X-Forwarded-*`를 읽지 않는다. 그러나 uvicorn은 기본으로 프록시 헤더를 켜고 `--forwarded-allow-ips` 기본값(127.0.0.1)에서 온 연결의 `X-Forwarded-For`·`X-Forwarded-Proto`를 믿는다 — 같은 호스트의 로컬 프로세스나 로컬 리버스 프록시 구성에 따라 클라이언트 IP·스킴이 바뀔 수 있다. 현재 Dockerfile 실행 명령에는 이 설정이 명시되어 있지 않다. Docker NAT로 모든 요청이 같은 IP로 집계되는지 확인과 함께 **Phase 6에서 결정 (열림)** (2-F, 2026-11-06) 다중 워커 테스트는 `--no-proxy-headers` 구성에서만 실행했다(모든 요청 127.0.0.1). uvicorn 기본 프록시 헤더 설정과 실제 프록시 신뢰 범위는 검증하지 않았다 — Phase 6에서 결정 (열림) |
| R-13 | 사용자 잠금(`FOR UPDATE`)으로 같은 사용자 동시 요청 지연 | 트랜잭션 짧게 유지, 사용자 단위라 다른 사용자에 영향 없음 |
| R-14 | Argon2 해싱 CPU·메모리 비용 (가입·로그인 폭주) | 시도 제한이 검증 전에 차단, 비밀번호 최대 128자 |
| R-15 | 이미지 크기·빌드 시간 증가 (psycopg binary, argon2) | Phase 6에서 측정 보고 |
| R-16 | 로컬 `todo_dev`·`todo_test`는 Windows libc 로캘(`Korean_Korea.949`, `todo_test`는 2-A3 읽기 전용 점검에서 확인), 운영 예정 환경은 `C.UTF-8` → `lower(name)` 고유 인덱스·문자열 정렬이 일부 문자에서 다를 수 있음, 로캘 변경 시 텍스트 인덱스 REINDEX 필요 가능. `lc_messages` 한국어라 서버 오류 메시지가 깨져 보일 수 있음(동작 무관) | Phase 3 템플릿 이름 기준 테스트(§P3), Phase 6 C.UTF-8 재검증(§P11). 개발 DB를 운영으로 옮기지 않는다 |
| R-17 | 로컬 PostgreSQL 18(5433)의 `listen_addresses = *` → 모든 네트워크 주소에서 연결 대기 | 사용자가 `pg_hba.conf`·Windows 방화벽 점검 또는 `listen_addresses`를 `127.0.0.1`로 제한 (사용자 설정, 미완료) |
| R-18 | `todo_test`의 public 스키마를 테스트가 다시 만들어 소유자가 `pg_database_owner` → `todo_test_user`로 바뀌고 PUBLIC USAGE 권한이 없어짐 | 테스트 계정 전용 DB라 테스트 영향 없음. 원상 복구는 DB 관리자 작업이며 별도 승인 |
| R-19 | PostgreSQL 테스트의 `DROP SCHEMA public CASCADE`는 스키마 소유자라면 다른 소유자의 객체까지 지운다 | **자동 게이트 구현 (2-A)**: 삭제 전 같은 트랜잭션에서 객체·소유자·리비전·연결 수(`numbackends` = 1)를 확인하고 어긋나면 멈춤(§P2 2-A). 게이트가 있어도 `todo_test` 실행은 계속 실행마다 승인받는다. **잔여 위험**: ① 확인과 삭제 사이의 몇 ms 틈에 새로 접속해 커밋까지 끝낸 객체는 막지 못함(PostgreSQL에 스키마 생성 차단 잠금이 없음) ② 게이트는 관계·함수·타입·확장·트리거·이벤트 트리거만 센다 — 통계 객체·연산자·콜레이션·텍스트 검색 객체 등은 미검사(2-A3 실측 0개) ③ 이름·종류·소유자 기준이라 CHECK 내용·기본값 같은 정의 수준 차이는 비교하지 않음 ④ 다른 역할의 세션이 `numbackends`에 잡히는지는 같은 역할 2번째 연결로만 실측했고 다른 역할로 직접 재현하지 않음 (2-E, 2026-11-06) PostgreSQL 테스트가 50개로 늘어 실행당 초기화 50회·게이트 호출 53회. 2-E HTTP 테스트 12개는 모두 실제로 commit 한다 — 단독(`-k`)·중단 시 합성 `users`·`sessions`·`auth_failures` 행이 남을 수 있다(복구는 승인 후 `rolled_back` 테스트 하나로 초기화, 수동 삭제 금지). 2-E 단독·전체 회귀 각 1회 모두 0행으로 끝났다. 파괴적 초기화이므로 실행마다 별도 승인 (열림) (2-F) PostgreSQL 테스트 55개, 실행당 초기화 55회·게이트 58회(코드 구조상 예상값). 다중 워커 테스트는 서버 프로세스가 실제로 `todo_test`에 commit 하므로 중단 시 합성 행이 남을 수 있다. `fresh_schema`는 파괴적 초기화이므로 실행마다 별도 승인과 사전 점검 (열림) |
| R-20 | 1-B 검증은 로컬 Python 3.14 기준, Docker 이미지는 Python 3.13 | Phase 6 이미지 안에서 전체 테스트 (§P11) |
| R-21 | FastAPI 기본 422 응답은 Pydantic 오류의 `input`을 돌려준다 → 가입·로그인 검증 실패 시 **비밀번호 원문이 응답 본문에 실릴 수 있음** | 2-E에서 인증 경로의 422 응답에서 입력값을 빼고, 비밀번호가 응답·로그에 없음을 테스트 **완료 (2-E, 2026-11-06, 로컬 커밋 `bbe5227`)**: 인증 경로 전용 422 처리기가 `input`·`ctx`를 빼고 `loc`·`msg`·`type`만 돌려준다(v6 경로는 기본 형식 유지). DB 없는 테스트로 비밀번호 비노출 확인 |
| R-22 | `APP_ENV` 미설정이면 `development`로 동작 (가입 허용, Secure 쿠키 아님) | 운영 compose에 `APP_ENV=production`·`COOKIE_SECURE=true` 필수(§P10). Phase 6 배포 검증에서 확인. 빈 값은 시작 오류 (2-E) 시작 가드: `APP_ENV` 미설정이면 `AUTH_FAILURE_KEY`가 필수라 키 없이는 시작하지 않는다. 키가 있으면 development로 동작하는 점은 그대로 — 운영 compose 필수값(§P10)은 Phase 6 (열림) |
| R-23 | 기대 Origin을 요청 `Host`·스킴에서 만들면 DNS 리바인딩·Host 위조·TLS 종단 프록시에서 잘못 판정할 수 있음 | 2-E에서 외부 Origin 결정 방식(허용 호스트 목록 또는 공개 Origin 설정, 프록시 헤더 신뢰 범위)을 정하고 테스트 (2-E) 현재 구현은 ASGI scope의 스킴(`request.url.scheme`)과 `Host` 헤더로 기대 Origin을 만든다(앱은 `X-Forwarded-*`를 읽지 않음). 허용 호스트 목록·공개 Origin 설정은 구현하지 않았다 — Host 위조·TLS 종단 프록시 판정 문제는 **열림**, HTTPS 게이트(§P7) 전 Phase 6에서 R-12와 함께 결정 |
| R-24 | 비밀번호 검증 경로에 길이 상한이 없으면 아주 긴 입력으로 Argon2 비용을 쓰게 할 수 있음 (5MB 입력 검증 약 0.2초) | **`auth.py` 방어선 완료 (2-B)**: 128자·512바이트 초과·인코딩 불가 입력은 Argon2에 넘기지 않음(테스트 포함). **남은 일 (2-E)**: HTTP 요청 본문 크기 제한, 인증 API 422 오류 응답 처리(R-21과 함께) (2-E) 인증 API 422 처리 완료(R-21), 로그인 본문 필드는 1024자 구조 상한(넘으면 422, 그 안의 규칙 위반은 401). 요청 본문 전체 크기 제한은 구현하지 않았다 — **열림** (Phase 6 리버스 프록시 또는 별도 결정) |
| R-25 | `urlsplit`이 탭·개행과 앞쪽 제어 문자를 지워, `http://test\tserver` 같은 비정상 Origin·Referer도 정상 값으로 판정됨 (브라우저는 보내지 않는 값이라 CSRF 우회 경로는 아님, 낮음) | **완료 (2-B)**: 파싱 전에 출력 가능한 ASCII(0x21~0x7E) 밖의 문자를 거부(지우지 않음), 제어 문자·비ASCII 거부와 정상 Origin·Referer 회귀 테스트 |
| R-26 | 사용자당 동시 세션 수 상한이 없음 → 정상 로그인을 반복하면 `sessions` 행이 쌓인다(만료 14일 뒤 로그인·가입 때 정리) | 2-C 결정: 이번에는 상한 없음(명세에 없음). 로그인 시도 제한(2-D)·정리(`purge_expired_sessions`)로 완화. 상한이 필요하면 별도 결정. (2-D 확인) 시도 제한은 성공하지 않은 시도만 세므로 **성공 로그인 반복은 막지 않는다** — 이 위험은 그대로 (2-E) HTTP 연결 후에도 사용자당 세션 수 상한은 없다 — 그대로 (열림) |
| R-27 | `hide_parameters=True`는 SQLAlchemy 가 만드는 오류 문자열·로그의 바인드 값만 숨긴다. 드라이버(PostgreSQL)의 오류 DETAIL(예: 중복 키 값)이나 앱이 직접 남기는 로그는 막지 못함 | 세 층으로 나눠 관리: ① SQLAlchemy 바인드 값 — `hide_parameters`로 숨김(실패 조건으로 테스트) ② PostgreSQL DETAIL — 중복 키 오류에 `token_hash`(토큰 원문이 아닌 SHA-256)가 실릴 수 있음 ③ 앱 로그·HTTP 응답 — 2-E에서 로그 설정과 500 응답 본문에 오류 내용이 나가지 않는지 확인. 세션 함수는 값을 로그·예외 메시지에 넣지 않음(테스트로 확인). **2-C3 관찰(2026-11-06, `todo_test`)**: ① 바인드 값 숨김 확인(`hide_parameters=True` 표시) · 세션 토큰 원문·CSRF 원문(기존 세션·두 번째 INSERT 모두)은 예외 문자열·DETAIL에 없음 ② `postgres_detail_contains_token_hash=True` — DETAIL에 `token_hash`가 실림을 확인(값은 출력하지 않음) ③ 미검증(2-E). users 고유 제약 위반 DETAIL에도 username 이 실릴 수 있음 → 2-E에서 중복을 먼저 조회해 409로 처리. **미해결(열림)** — ③ 확인 전까지 해결로 보지 않는다. (2-D 보강) `auth_failures`는 UNIQUE가 없어 중복 키 DETAIL은 없지만 CHECK 위반 DETAIL("Failing row contains …")에는 행 값(HMAC 키·IP)이 실린다. 코드는 `kind`를 고정값으로 넣어 정상 경로에서는 생기지 않는다. 잠금 대기 초과(55P03) 메시지에는 행 값이 없다. 2-D `todo_test` 실행(2026-11-06)에서도 `postgres_detail_contains_token_hash=True` 관찰이 같았다(값은 출력하지 않음) — 계속 미해결 (2-E 검증, 2026-11-06) ③ 인증 HTTP 경로: 422 입력값 제거, 500·503 고정 문구, 로그는 사건·예외 종류·SQLSTATE만(traceback 없음). DB 없는 테스트와 실제 PostgreSQL UNIQUE·NOT NULL 위반 HTTP 테스트(단독·전체 회귀 각 1회)로 **인증 HTTP 응답·앱 로그 비노출을 확인**했다. 드라이버 예외 자체에는 DETAIL이 계속 실릴 수 있고(①②, `token_hash` 관찰 동일), Phase 3 데이터 경로는 아직 확인하지 않았으며, Alembic을 앱과 같은 프로세스에서 실행하면 `fileConfig`가 앱 로거를 끄는 현상은 테스트에서만 보완했다 — **열림 유지** |
| R-28 | (2-D) `AUTH_FAILURE_KEY`가 워커·컨테이너마다 다르면 username 기준 제한이 워커별로 나뉜다(코드로 검출 불가). 키를 바꾸면 username 카운터가 초기화된다 | 운영 compose·secret 한 곳에서 모든 워커에 같은 값(§P10 변경안에 추가 예정). 교체는 드물게·점검 시간에, 교체 직후 1회 예산 초기화는 수용. 공개 개발용 키는 운영에서 거부, `APP_ENV` 미설정이면 키 필수 (2-F) 모든 프로세스·워커가 같은 `AUTH_FAILURE_KEY`를 쓰는 정상 구성만 검증했다. 서로 다른 키를 가진 워커 구성은 검증하지 않았다 (열림) |
| R-29 | (2-D) 선기록 방식의 오차단: 검증 중인 선기록도 한도에 세므로 같은 사용자의 동시 요청(더블클릭)이 한도 근처에서 429. 성공해도 이전 실패는 15분 창이 지나야 풀린다. 서버 중단·Tx2 실패 시 1회 과다 집계 | 승인된 의미 변경(`V7_SPEC.md` §29.5 변경 기록). 우회 방지를 우선. 성공 시 초기화가 꼭 필요해지면 상태 열(새 리비전)이 필요 — 별도 결정 |
| R-30 | (2-D) Argon2 슬롯(프로세스당 2개, 5초)이 차면 정상 사용자도 503. 가입은 해시 동안 DB 연결을 잡는다(가입 트랜잭션 1개 유지 — 가입 제한·운영 가입 비활성 D-13으로 완화) | Phase 6에서 운영 서버 코어·메모리·Argon2 시간을 측정해 슬롯 수 조정. 503은 인증 없음(우회 없음). 로그인은 보상 Tx로 검증하지 않은 시도를 세지 않는다 (2-E) 가입 경로를 정책대로 구현했다(트랜잭션 1개 안에서 해시, 연결 보유). 동시 같은 이름 가입은 고유 인덱스 대기가 `lock_timeout` 2초를 넘으면 503이 된다. PostgreSQL 테스트의 동시 가입 2건은 2초 안에 끝났다. 슬롯 수 조정은 Phase 6 (열림) |
| R-31 | (2-D) PostgreSQL 동시성 테스트(스레드 10·25개)는 NullPool 연결을 여러 개 열고 커밋한다 → R-19 게이트의 `numbackends = 1`·종료 후 0행 조건과 맞물림 | 테스트마다 `finally`에서 `dispose`, 커밋 테스트는 마지막에 두지 않음(마지막은 `rolled_back`). 실행마다 승인. (2026-11-06 보강·검증) `connect_timeout`·비-daemon 스레드·남은 스레드 실패 처리·연결 정리 대기(최대 10초, 읽기만)를 넣고 단독 1회·전체 회귀 1회 모두 경합·남은 스레드 없이 0행으로 끝났다. **남은 위험(열림)**: 커밋하는 테스트 3개(잠금 직렬화 1행, 동시성 5행·20~25행)를 단독(`-k`)으로 돌리거나 중간에 실패하면 합성 `auth_failures` 행이 남는다(복구는 승인 후 `rolled_back` 테스트 하나로 초기화, 수동 삭제 금지). `statement_timeout`이 없어 서버에서 문장이 멈추면 대기 상한이 없다. 2회 실행으로 불안정성이 없다고 보장하지 않는다 (2-E) 동시 가입 HTTP 테스트·Tx2 잠금 경합 테스트도 NullPool·`run_concurrently`·연결 정리 대기를 쓴다. 2-E 단독 1회·전체 회귀 1회 모두 경합·남은 스레드 없음. 실행 횟수가 적어 불안정성이 없다고 보장하지 않으며 `statement_timeout` 미설정도 그대로 (열림) (2-F) uvicorn 프로세스 정리: `CREATE_NEW_PROCESS_GROUP`, 정상 종료 `CTRL_BREAK_EVENT`, 감독은 Popen 핸들로 종료, 워커는 부모 PID·생성 시각·명령줄을 확인한 뒤 PID별 종료(`taskkill /T` 미사용, 식별이 불확실하면 강제 종료하지 않음), 종료 후 PID·포트·DB 연결 확인. 남은 위험: 워커별 요청 분배의 비결정성(B가 [검증 미완료]로 끝날 수 있음), 여러 워커가 쓰는 공유 로그 파일, 재확인과 종료 사이 PID 재사용의 짧은 틈, `statement_timeout` 없음 (열림) |
