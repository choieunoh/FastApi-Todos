# V8_SPEC.md — Version 8.0.0 "Account Management & UI/UX Improvement"

- 작성: 2026-11-15 (1단계 Spec, 구현 전). 상태: **명세 확정(2026-11-15 사용자 승인)** → 단계별 구현·검증 후 DoD에 체크한다.
- 기준 코드: `main` = `origin/main` `eef2dfe` (v7.0.0은 PR #3, 머지 커밋 `7cf68bb`, 2026-11-10에 main에 병합됨).
- 우선순위: 이 문서 > `CLAUDE.md`의 `(8.0.0)` 표시 > `V7_SPEC.md`·`V7_PLAN.md` > `CLAUDE.md`의 v4~v6 규칙. 이 문서에 없는 v4~v7 규칙은 그대로 계약이다.
- 절 번호는 `V7_SPEC.md`(§25~§34)에 이어 §35부터 쓴다.
- 이 문서에 없는 기능은 구현하지 않는다(`CLAUDE.md` Purpose).

## 범위 재정의 기록 (2026-11-15, 사용자 승인)
- v8.0.0의 목표를 "Multi-User Data Migration"에서 **"Account Management & UI/UX Improvement"**로 바꾼다.
- 기존 "Multi-User Data Migration" 계획(사용자별 데이터 격리, 할 일·템플릿의 PostgreSQL 전환, JSON 이전, soft delete·position, 사용자별 알림 키, 운영 전환)은 삭제하지 않고 **후속 개발 항목(버전 미정, 이하 "후속")**으로 옮긴다. 그 명세는 `V7_SPEC.md`·`V7_PLAN.md`에 그대로 보존한다.
- 다만 그 계획 가운데 **데이터 API 15개의 로그인 인증·CSRF(D-11, 호환성 변경 C-1)와 화면 로그인 게이트**는 v8.0.0에 포함한다(§36 E-2). 사용자별 격리는 포함하지 않는다.
- `V7_SPEC.md`·`V7_PLAN.md`·`README.md`·`RELEASE_NOTES.md`의 "v8.0.0 이관/예정"은 당시 기록이므로 고치지 않는다. 그 문서들에서 "v8.0.0"은 이제 "후속"을 뜻한다. 단, 이 문서가 v8.0.0에 포함한다고 적은 항목은 예외다. (`README.md`·`RELEASE_NOTES.md`는 3단계에서 새 판을 쓰면서 정리한다.)

### 기존 문서와의 충돌 정리
| # | 기존 서술 | 위치 | v8.0.0 처리 |
|---|---|---|---|
| K-1 | v8.0.0 = "Multi-User Data Migration" | `CLAUDE.md` Purpose·Target, `V7_SPEC.md` 머리말·§34.1, `V7_PLAN.md` §P13, README, RELEASE_NOTES | 목표 변경. 기존 계획은 "후속"으로 옮기고 `CLAUDE.md` 표시를 `(후속 예정)`으로 바꾼다 |
| K-2 | "7.0.0은 main에 병합하지 않았다, push·main 병합 금지(R-35)" | `CLAUDE.md` Current/Target Version, `V7_PLAN.md` R-35 | 실제 상태: PR #3로 병합(2026-11-10), 이후 과제 커밋까지 `origin/main`에 push됨. `CLAUDE.md`를 사실대로 고친다. R-35는 v7.0.0 당시 규칙으로 기록만 남는다 |
| K-3 | 비밀번호 변경 범위 밖(D-9), 계정 삭제 UI 범위 밖 | `V7_SPEC.md` §26·§34, `CLAUDE.md` Out of Scope | v8.0.0 범위 안이다(§37~§39). D-9는 7.0.0에 한한 결정으로 본다 |
| K-4 | 데이터 경로 v8.0.0 계약 = 인증 + 사용자별 격리(타 사용자 404) + position + soft delete | `V7_SPEC.md` §27.3~§27.5, §28.2 "v8.0.0 계약" | 인증(401)·CSRF(403)만 v8.0.0에 적용한다. 격리·position·soft delete·시퀀스 id는 후속이다. 데이터는 공유 JSON 그대로다(§40) |
| K-5 | v8.0.0 계약의 화면 로그인 게이트, 사용자별 알림 키(D-8) | `V7_SPEC.md` §30 | 게이트는 적용(§41.1). D-8은 후속이다 — 데이터가 공유라서 v6 키를 그대로 쓴다 |
| K-6 | "데이터 경로는 DB를 쓰지 않는다", "DB를 쓰는 앱 경로는 `/auth/*` 4개뿐" | `CLAUDE.md` Coding Conventions | v8.0.0에서는 데이터 경로도 세션 조회 때문에 DB를 쓴다. 데이터 자체는 JSON 그대로다(§40.3) |
| K-7 | "v7.0.0은 운영 공개 금지(R-34)" vs 실제 공개 배포(5001 `docker-compose.v7-public.yml`, 5002, 3004) | `CLAUDE.md` 과제 절, `V7_PLAN.md` R-34 | v8.0.0 문서 범위에서는 배포를 바꾸지 않는다. v8.0.0의 배포·main 병합·push·Jenkins 작업 변경은 각각 별도 승인을 받는다(§44.4) |
| K-8 | JMeter 계획이 인증 없이 `POST /todos`를 보냄 | `jmeter/*.jmx`, `ci/Jenkinsfile.team3004` | v8.0.0 서버에서는 401이 난다. 우회 설정 없이 테스트 계정으로 로그인하는 절차로 바꾼다(§42) |
| K-9 | `test_integration.py`가 인증 없이 외부 서버에 요청 | `tests/test_integration.py` | v8.0.0 서버에서는 401이 난다. 3단계에서 로그인 절차를 넣는다(실행은 계속 승인 필요) |

## 35. 목표와 범위

### 35.1 v8.0.0에 포함
| # | 항목 | 절 |
|---|---|---|
| 1 | 현재 로그인한 사용자 정보 확인(`GET /auth/me`에 가입일 추가, 계정 메뉴) | §37.1, §41.2 |
| 2 | 비밀번호 변경(현재 비밀번호 확인, 모든 세션 무효화 후 새 세션 발급) | §37.2, §38 |
| 3 | 회원 탈퇴(비밀번호 재확인, 계정·세션 삭제, 공유 JSON 보존) | §37.3, §39 |
| 4 | 데이터 API 15개 로그인 필수 + 쓰기 11개 CSRF(A안) — 공유 JSON 데이터 유지 | §40 |
| 5 | 화면 로그인 게이트, 계정 메뉴, 레이아웃·CSS 정리(테마 3종 유지) | §41 |
| 6 | JMeter·Jenkins의 테스트 계정 로그인 절차(인증 우회 없음, 로그인과 부하 측정 분리) | §42 |
| 7 | 계정 API·보안·회귀·UI 테스트, README·릴리스 노트·화면 버전 `Version 8.0.0` | §43, §44 |

### 35.2 v8.0.0에 포함하지 않음 (후속)
- 사용자별 데이터 격리(다른 사용자 리소스 404), 할 일·템플릿의 PostgreSQL 전환, `migrate_json.py`·JSON 이전, position·soft delete·시퀀스 id, 사용자별 템플릿 이름 기준(R-16), 사용자별 알림 키(D-8), 운영 전환·백업·롤백, R-12 실환경 클라이언트 IP 검증, HTTPS 게이트 통과 — `V7_SPEC.md` §27·§28.2 "v8.0.0 계약"·§31, `V7_PLAN.md` §P3 3-6·§P4~§P7·§P10·§P11.

### 35.3 영구 범위 밖 (v7 §34 유지)
- OAuth·소셜 로그인, 이메일 인증·비밀번호 재설정 메일, 2단계 인증, 관리자 역할, 데이터 공유·협업 기능, 비동기 ORM, 프론트엔드 프레임워크·빌드 도구·외부 CDN, PostgreSQL 외 DB, 백그라운드 스케줄러.
- v8.0.0에서 새로 범위 밖으로 정하는 것: 아이디(username) 변경, 탈퇴 계정 복구·유예 기간, "다른 기기 로그아웃" 단독 버튼, 세션 목록 조회, 비밀번호 강도 측정기, 새 런타임 의존성.

## 36. 결정 (2026-11-15 승인 또는 이 문서의 설계 결정)
| ID | 결정 | 근거 |
|---|---|---|
| E-1 | v8.0.0 = "Account Management & UI/UX Improvement". 기존 v8 계획은 후속으로 옮긴다 | 사용자 승인 |
| E-2 | 데이터 API 15개는 로그인 필수(익명 401), GET이 아닌 11개는 CSRF 필수(403). JSON 저장 유지, 사용자별 격리 없음(로그인한 모든 사용자가 같은 데이터) | 사용자 승인(A안) — D-11·C-1의 인증 부분만 적용 |
| E-3 | 현재 비밀번호 불일치 400, 미인증 401, CSRF·Origin 403, 시도 제한 429, 입력 검증 422 | 사용자 승인 |
| E-4 | JMeter·Jenkins에 인증 우회 설정(환경변수·헤더·IP 예외)을 두지 않는다. 테스트 계정 로그인 절차를 쓰고, 로그인 샘플은 부하 측정 지표와 분리한다 | 사용자 승인 |
| E-5 | 회원 탈퇴는 `todo.json`·`task_templates.json`·삭제 복구 버퍼를 바꾸지 않는다 | 사용자 승인 |
| E-6 | 비밀번호 변경이 성공하면 그 사용자의 **모든** 세션(요청한 세션 포함)을 지우고 새 세션 1개를 발급한다. 같은 트랜잭션이라 중간 상태가 없다 | 사용자 승인(세션 무효화 후 재발급) |
| E-7 | 새 Alembic 리비전 없음. 스키마(`0002`)를 그대로 쓴다. 현재 비밀번호 오류 횟수는 기존 `auth_failures`의 `kind='login'` 기록으로 센다 | 마이그레이션 위험 회피 |
| E-8 | Argon2 검증·해시 동안 DB 연결·트랜잭션을 잡지 않는다(로그인의 Tx1 → Argon2 → Tx2 방식을 그대로 쓴다) | `V7_SPEC.md` §29.5와 같은 원칙 |
| E-9 | 동시 요청으로 비밀번호 해시가 검증 이후 바뀐 경우(로그인 rehash 등)는 409로 응답하고 아무것도 바꾸지 않는다 | §38.3. 2026-11-15 사용자 승인 |
| E-10 | 탈퇴는 DB의 `ON DELETE CASCADE`에 맡긴다(ORM 관계 cascade를 쓰지 않는다). `data_migrations`가 사용자를 참조하면 409로 거부하고 아무것도 지우지 않는다 | §39.2. 2026-11-15 사용자 승인 |
| E-13 | 409 응답 문구는 일반 문구만 쓴다. 표·제약·SQLSTATE 등 내부 DB 구조를 응답·화면에 드러내지 않는다(로그는 기존 규칙대로 사건·예외 종류·SQLSTATE만) | 2026-11-15 사용자 승인 |
| E-14 | 현재 비밀번호 검증 실패와 로그인 실패는 같은 시도 제한 예산을 공유한다. 기존 시도 제한 수치·창은 바꾸지 않는다 | §38.2. 2026-11-15 사용자 승인 |
| E-15 | 기존 API 테스트의 인증 대체 픽스처와 실제 인증 보안 테스트를 분리한다 | §43.2. 2026-11-15 사용자 승인 |
| E-11 | 알림 읽음 키는 v6 `todo.v6.notifications.read` 그대로(D-8은 후속) | 데이터가 공유이므로 사용자별 키가 의미 없음 |
| E-12 | 새 런타임·개발 의존성 없음. `main.py`·`db.py`·`auth.py`·`index.html` 구성 유지 | `CLAUDE.md` Constraints |

## 37. 계정 API 계약

공통:
- 오류 본문은 `{"detail": "<고정 문구>"}`이고 입력값·비밀번호·해시·토큰·예외 문자열을 넣지 않는다. 422는 `/auth/` 경로 규칙대로 `loc`·`msg`·`type`만이다(`V7_SPEC.md` §28.1).
- 500·503은 기존 고정 문구(`SERVER_ERROR`·`SERVICE_BUSY`)이고, 로그는 사건·예외 종류·SQLSTATE만 남긴다(R-27).
- 고정 경로는 기존 규칙(§9)대로 선언 순서를 지킨다.

### 37.1 GET `/auth/me` (변경)
| 항목 | 내용 |
|---|---|
| 성공 | 200 `{id, username, csrf_token, created_at}` — `created_at`은 가입 시각(ISO 8601, UTC, 타임존 포함). **필드 추가만** 하므로 기존 클라이언트와 호환된다 |
| 실패 | 세션 없음·만료 401, DB 오류 500, 잠금 대기 초과 503 (v7 그대로) |
- `/auth/login`·`/auth/register`의 응답은 v7 그대로 `{id, username, csrf_token}`이다(가입일은 `/auth/me`로만 준다).

### 37.2 POST `/auth/password` (신규)
- 요청: `{"current_password": str, "new_password": str}` — 각 필드는 문자열이고 1024자 이하(구조 검사).
- 성공: **200** `{id, username, csrf_token}` + 새 세션 쿠키(`Set-Cookie`, 속성은 §29.2 그대로). `csrf_token`은 새 세션의 값이다.

| 순서 | 검사 | 실패 응답 |
|---|---|---|
| 1 | `Origin`(없으면 `Referer`)이 있으면 요청 Origin과 같아야 한다(`require_same_origin`) | 403 `요청 출처가 올바르지 않습니다` |
| 2 | 유효한 세션 | 401 `로그인이 필요합니다` |
| 3 | `X-CSRF-Token` = 세션 값 | 403 `CSRF 토큰이 올바르지 않습니다` |
| 4 | 본문 구조, `new_password` 규칙(8~128자, UTF-8 인코딩 가능 — `auth.check_password`), `new_password` ≠ `current_password`(문자열 비교) | 422 |
| 5 | 시도 제한(로그인과 같은 규칙·창, 그 사용자의 username 기준 — §38.2) | 429 + `Retry-After`(정수 초) |
| 6 | 현재 비밀번호 검증 (`current_password`의 길이·형식 오류도 불일치로 본다) | **400** `현재 비밀번호가 올바르지 않습니다` |
| 7 | Tx2에서 세션 재확인 | 401 (그 사이 로그아웃·탈퇴·다른 비밀번호 변경으로 세션이 사라짐) |
| 8 | Tx2에서 해시가 검증 때와 같은지 | 409 `다른 요청과 충돌했습니다. 다시 시도해 주세요` |
- 해시 슬롯 혼잡·잠금 대기 초과 503, 그 밖의 DB 오류 500. 어느 실패에서도 비밀번호·세션·쿠키는 바뀌지 않는다.
- 본문 JSON이 깨진 요청은 프레임워크가 의존성보다 먼저 422를 낼 수 있다(`V7_SPEC.md` §28.2 예외와 같다).

### 37.3 POST `/auth/account/delete` (신규)
- 요청: `{"password": str}` (문자열, 1024자 이하). 경로는 본문을 쓰기 위해 `DELETE` 대신 `POST`로 한다.
- 성공: **204** + 세션 쿠키 삭제(`cookie_delete_kwargs`).

| 순서 | 검사 | 실패 응답 |
|---|---|---|
| 1 | Origin | 403 |
| 2 | 유효한 세션 | 401 |
| 3 | CSRF | 403 |
| 4 | 본문 구조 | 422 |
| 5 | 시도 제한(§38.2와 같음) | 429 + `Retry-After` |
| 6 | 비밀번호 검증 | **400** `비밀번호가 올바르지 않습니다` |
| 7 | Tx2에서 세션 재확인 | 401 |
| 8 | Tx2에서 해시가 검증 때와 같은지 | 409 `다른 요청과 충돌했습니다. 다시 시도해 주세요` |
| 9 | 탈퇴를 막는 참조가 있음(현재 스키마에서는 `data_migrations` FK 위반) | 409 `지금은 탈퇴할 수 없는 계정입니다. 관리자에게 문의하세요` (E-13 — 내부 구조를 드러내지 않는다) |
- 503·500은 §37.2와 같다. 실패하면 계정·세션·쿠키가 그대로다.

### 37.4 상태 코드 요약 (계정 경로 2개)
| 코드 | 의미 |
|---|---|
| 200 / 204 | 비밀번호 변경 성공 / 탈퇴 성공 |
| 400 | 현재 비밀번호(탈퇴는 비밀번호) 불일치 |
| 401 | 미인증, 또는 처리 중 세션이 사라짐 |
| 403 | Origin 불일치, CSRF 헤더 없음·불일치 |
| 409 | 동시 요청 충돌(E-9), 탈퇴 차단 참조(E-10) |
| 422 | 입력값 검증 실패 |
| 429 | 시도 제한 |
| 500 / 503 | 서버 오류 / 해시 슬롯 혼잡·잠금 대기 초과 |

## 38. 비밀번호 변경 처리

### 38.1 트랜잭션 구성 (E-8)
1. **인증 의존성**: Origin → 세션 조회 → CSRF. 세션 조회는 짧은 별도 트랜잭션으로 하고 바로 닫는다. 요청 Session(`RequestDbDep`)을 Argon2 동안 열어 두지 않는다.
2. **본문 검증(422)**.
3. **Tx1**: `begin_login_attempt`(username = 세션 사용자의 username) — 시도 제한 판정과 선기록, 저장된 해시 조회 → commit. 제한이면 429(기록 없음).
4. **트랜잭션 밖**: `verify_login_password`로 현재 비밀번호 검증 → 불일치면 400(선기록은 실패로 남는다). 일치하면 `hash_password_limited(new_password)`로 새 해시. 두 단계 어디서든 해시 슬롯 혼잡이면 보상 Tx(자기 선기록 삭제) 후 503.
5. **Tx2** (`lock_timeout` 2s, 하나라도 실패하면 전체 rollback):
   1. `lock_user(user_id)` — `SELECT … FOR UPDATE`로 같은 사용자의 비밀번호 변경·탈퇴를 직렬화한다.
   2. 요청 쿠키의 세션이 여전히 유효하고 그 사용자 것인지 확인 → 아니면 401.
   3. `finish_login_success`(자기 선기록 삭제) → 선기록이 없으면 500(로그인과 같은 내부 불일치 처리).
   4. `password_hash`를 **Tx1에서 읽은 값과 같을 때만** 새 해시로 바꾼다(`update_password_hash_if_unchanged`) → 0행이면 409.
   5. 그 사용자의 세션을 **모두** 삭제한다(요청한 세션과 다른 기기 세션 포함).
   6. 만료 세션 정리 후 새 세션 1개 생성.
   7. commit.
6. commit이 끝난 뒤에만 200 본문과 `Set-Cookie`를 만든다. 실패 응답에는 `Set-Cookie`가 없다.

### 38.2 시도 제한
- 현재 비밀번호 불일치는 **같은 username의 로그인 실패**로 센다(E-7·E-14). 로그인·비밀번호 변경·탈퇴 세 경로가 같은 함수(`begin_login_attempt`·`finish_login_success`·`cancel_login_attempt`)로 판정·기록하므로 어느 경로에서 시작해도 같은 횟수로 막힌다. 로그인과 같은 예산(`(username, IP)` 15분 5회, username 50회, IP 20회)을 함께 쓴다. 그래서 비밀번호 변경 화면으로 로그인 제한을 우회해 비밀번호를 대입할 수 없다.
- 결과: 같은 IP에서 현재 비밀번호를 5번 틀리면 그 계정의 로그인도 15분 동안 429다. 이것은 의도한 동작이다.
- 탈퇴의 비밀번호 확인도 같은 규칙이다.

### 38.3 동시성과 실패 (테스트 대상)
| 상황 | 기대 결과 |
|---|---|
| 같은 세션으로 비밀번호 변경 2건 동시 | 1건 200, 1건 401(Tx2에서 세션이 사라짐). 최종 비밀번호 = 200을 받은 요청의 값. 유효 세션은 그 요청의 새 세션 1개 |
| 같은 사용자의 다른 세션 2개로 동시 변경 | 위와 같다 |
| 비밀번호 변경과 탈퇴 동시 | 둘 중 먼저 `lock_user`를 잡은 쪽이 성공하고, 다른 쪽은 401(세션 없음). 고아 세션이 없다 |
| 검증 후 로그인 rehash로 해시가 바뀜 | 409, 아무것도 바뀌지 않음 |
| Tx2의 세션 생성·삭제·해시 UPDATE 중 DB 오류(세션 교체 실패) | 500(55P03이면 503). 해시는 그대로, 기존 세션은 모두 유효, `Set-Cookie` 없음 |
| 해시 슬롯 혼잡 | 503, 보상 Tx로 선기록 삭제, 아무것도 바뀌지 않음 |
| 이전 세션 토큰으로 요청 | 성공 이후 `/auth/me`·데이터 API 모두 401 |
| 이전 비밀번호로 로그인 | 401, 새 비밀번호로는 200 |

## 39. 회원 탈퇴 처리

### 39.1 처리 순서
- 비밀번호 변경의 1~4단계와 같다(Tx1 선기록 → 트랜잭션 밖 검증). 새 해시는 만들지 않는다.
- Tx2: `lock_user` → 세션 재확인(401) → `finish_login_success` → `DELETE FROM users WHERE id = :uid AND password_hash = :verified_hash`(Core 문장, 0행이면 409 E-9) → commit → 204 + 쿠키 삭제.
- 탈퇴 뒤에는 같은 username으로 다시 가입할 수 있다(`uq_users_username`만 있으므로).

### 39.2 외래 키 참조 관계 (스키마 `0002` 기준, 1단계에서 카탈로그로 검증)
| 참조하는 표 | 제약 이름(규칙상) | ON DELETE | 탈퇴 시 |
|---|---|---|---|
| `sessions.user_id` | `fk_sessions_user_id_users` | CASCADE | 그 사용자 세션 모두 삭제 |
| `todos.user_id` | `fk_todos_user_id_users` | CASCADE | v8.0.0에서는 행 없음(앱 미사용). 있으면 함께 삭제 |
| `subtasks.todo_id` → `todos` | `fk_subtasks_todo_id_todos` | CASCADE | todos 삭제를 따라 삭제 |
| `task_templates.user_id` | `fk_task_templates_user_id_users` | CASCADE | v8.0.0에서는 행 없음. 있으면 함께 삭제 |
| `data_migrations.owner_user_id` | `fk_data_migrations_owner_user_id_users` | 없음(NO ACTION) | 행이 있으면 FK 위반(SQLSTATE 23503) → rollback → 409(E-10). v8.0.0 앱은 이 표에 쓰지 않는다 |
| `auth_failures` | FK 없음 | — | 남는다. 평문 username이 없고(HMAC) 24시간 뒤 정리된다(R8-6) |
- 1단계 테스트는 `pg_constraint`에서 `users`를 참조하는 FK 목록과 `confdeltype`이 위 표와 정확히 같은지 확인한다. 표가 바뀌면(새 리비전) 이 표와 테스트를 함께 고친다.
- 409 판정은 SQLSTATE 23503과 제약 이름이 `fk_data_migrations_owner_user_id_users`일 때만이다. 다른 무결성 오류는 500이다(가입 409 판정과 같은 방식).

### 39.3 공유 JSON 보존 (E-5)
- 탈퇴 경로는 `TODO_FILE`·`TEMPLATE_FILE`을 읽지도 쓰지도 않고, 메모리 삭제 복구 버퍼도 건드리지 않는다.
- 테스트: 탈퇴 전후 두 파일의 SHA-256이 같고, 다른 사용자의 `GET /todos`·`GET /task-templates` 응답이 같다.
- 화면과 README에 "할 일·템플릿은 모든 로그인 사용자가 함께 쓰는 데이터이며, 탈퇴해도 삭제되지 않는다"를 명시한다.

## 40. 데이터 API 인증·CSRF (E-2)

### 40.1 적용 대상
| 구분 | 경로 | 의존성 |
|---|---|---|
| 읽기 4개 | GET `/todos`, GET `/todos/export`, GET `/stats`, GET `/task-templates` | `require_user` |
| 쓰기 11개 | POST `/todos`, POST `/todos/restore`, POST `/todos/bulk`, PUT·DELETE `/todos/{id}`, POST `/todos/{id}/subtasks`, PUT·DELETE `/todos/{id}/subtasks/{sid}`, POST `/task-templates`, PUT·DELETE `/task-templates/{id}` | `require_csrf`(`require_user` 포함) |
- 구현(1단계, 2026-11-15): 15개 경로를 `main.data_router`(APIRouter)에 기존 순서 그대로 선언하고, 파일 끝에서 `app.include_router(data_router, dependencies=[Depends(require_csrf)])`로 붙인다. `require_csrf`는 `require_user`를 거치고 GET·HEAD는 CSRF를 보지 않으므로 위 표와 같은 동작이다. 핸들러 본문은 바꾸지 않았다.
- 인증 없이 열린 경로는 `GET /`, `/auth/register`, `/auth/login`, `/auth/logout`, `/auth/me`(401 반환용), `/metrics`, `/docs`·`/openapi.json`뿐이다(C-6 그대로).
- 검사 순서: 401 → 403 → 422 → 404·409(기존). 깨진 JSON의 422 예외는 `V7_SPEC.md` §28.2와 같다.
- 응답 본문: 401 `{"detail": "로그인이 필요합니다"}`, 403 `{"detail": "CSRF 토큰이 올바르지 않습니다"}`.

### 40.2 바뀌지 않는 것
- 경로·메서드·성공 상태 코드·응답 형식·검증 규칙·JSON 저장·id 규칙(최대 id + 1)·메모리 복구 버퍼(30초, 100건)는 v6/v7 그대로다.
- 데이터는 **로그인한 모든 사용자가 공유**한다. A가 만든 할 일을 B가 보고 수정·삭제·복구할 수 있다. 이것은 결함이 아니라 v8.0.0의 명시된 한계이며, 화면 버전 문단과 README에 적는다.
- 내보내기 `<a download>`는 같은 출처의 GET 이동이라 세션 쿠키(SameSite=Lax)가 붙는다. CSRF 헤더는 필요 없다.

### 40.3 DB 의존
- 데이터 요청마다 세션 조회 SELECT 1회가 생긴다(`RequestDbDep`, 응답 전에 commit). 쿠키가 없으면 DB를 열지 않고 401이다.
- PostgreSQL이 멈추면 데이터 API도 500·503이 된다(v7에서는 데이터 API가 DB 없이 동작했다). R8-1.

### 40.4 호환성 변경 (V7 §28.4에 이어서)
| # | 변경 | 상태 |
|---|---|---|
| C-1 | 데이터 API 익명 요청 401 | **적용 v8.0.0** (2026-11-15) |
| C-10 | 데이터 API 쓰기 요청에 `X-CSRF-Token` 필수, 없으면 403 | **적용 v8.0.0** |
| C-11 | `GET /auth/me` 응답에 `created_at` 추가 | 필드 추가 — 기존 클라이언트 영향 없음 |
| C-12 | 새 경로 `/auth/password`, `/auth/account/delete`의 400·409 | 새 경로 — 영향 없음 |

## 41. UI (`index.html` 1개 유지)

### 41.1 화면 로그인 게이트 (V7 §30 "v8.0.0 계약" 중 게이트 부분 적용)
- 시작 시 `GET /auth/me`를 보낸다.
  - **401**: 로그인·가입 화면만 보이고 앱 영역(알림 바, 폼, 템플릿, 보기, 목록, 내보내기)을 숨긴다. 데이터 요청(`/todos`, `/stats`, `/task-templates`)을 보내지 않는다. 401은 `#error`에 쓰지 않는다.
  - **200**: 앱 영역을 보이고 `load()`를 시작한다. `csrf_token`은 메모리 변수에만 둔다.
  - 그 밖의 오류: 로그인 화면 + `auth-error`에 "로그인 상태를 확인하지 못했습니다…"를 보인다.
- 로그인·가입에 성공하면 로그인 화면을 숨기고 앱을 보인 뒤 `load()`를 부른다.
- `/auth/`가 아닌 요청이 401을 받으면 인증 상태를 지우고 앱 영역을 숨기며, 로그인 화면에 "다시 로그인하세요."를 보인다(v7 `invalidateAuth`를 그대로 쓴다).
- 로그아웃·탈퇴에 성공하면 `location.reload()`로 화면 상태를 모두 비운다(v7 그대로).
- v7의 접힌 진입 버튼(`#auth-open`)과 그 높이 상쇄 CSS(R-32)는 없앤다. 로그아웃 상태에서는 로그인 카드가 항상 펼쳐져 있다.

### 41.2 계정 메뉴
- 헤더: 제목 / 테마 select / 계정 버튼 / [로그아웃].
  - 계정 버튼 `data-testid="account-menu"`는 `aria-expanded`·`aria-controls`가 있다. 안에 사용자명 `current-user`(기존 testid, `textContent`)가 있다.
  - [로그아웃] `logout`은 기존처럼 헤더에 둔다(위치·testid 유지).
- 계정 패널 `account-panel`(알림 패널과 같은 펼침 방식, Esc·[닫기]로 닫으면 계정 버튼으로 포커스가 돌아간다):
  - **내 정보**: 아이디, 가입일(`account-created`, Asia/Seoul 날짜 `YYYY-MM-DD`, §20).
  - **비밀번호 변경** `password-form`: 현재 비밀번호 `password-current`, 새 비밀번호 `password-new`, 새 비밀번호 확인 `password-confirm`, [변경] `password-submit`, 오류 `password-error`(`role="alert"`).
    - 확인 칸이 다르면 요청하지 않고 오류를 보인다.
    - 성공하면 새 `csrf_token`으로 바꾸고 폼을 비우며, `#status-message`에 "비밀번호를 변경했습니다. 다른 기기의 로그인은 해제되었습니다."를 보인다.
    - 오류 문구: 400 현재 비밀번호 불일치, 401 다시 로그인(게이트로 이동), 403, 409 다시 시도, 422 규칙 안내, 429 "약 N분", 500·503.
  - **회원 탈퇴** `delete-account-form`: 안내문("할 일·템플릿은 공유 데이터라 삭제되지 않습니다"), 비밀번호 `delete-password`, [탈퇴] `delete-submit`, 오류 `delete-error`.
    - 제출하면 `confirm()`을 거친다. 취소하면 요청하지 않는다(v5 삭제 확인과 같은 방식, `prompt()` 금지).
    - 성공하면 `location.reload()`로 로그인 화면이 된다.
- 비밀번호 입력란은 `type="password"`이고 `autocomplete`는 각각 `current-password`·`new-password`다. 값을 localStorage나 로그에 넣지 않으며, 요청 뒤에는 입력란을 비운다.

### 41.3 레이아웃·CSS 정리 (대규모 개편 금지)
- 색은 기존 `:root` 토큰 체계를 유지한다. 라이트/다크/시스템 테마, `todo.v6.theme` 키, 그리기 전 테마 적용 스크립트, 두 다크 블록 규칙(§24)은 그대로다. 새 색이 필요하면 토큰으로만 추가하고 두 테마 모두 4.5:1(글자)·3:1(막대·포커스)을 지킨다.
- 간격·모서리 토큰(`--space-1`~`--space-4`, `--radius`)을 추가하고, 새로 쓰거나 고치는 규칙부터 적용한다. 기존 규칙을 모두 다시 쓰지 않는다.
- 버튼은 3단계로 구분한다(기본·주요·위험). 주요는 추가·저장·로그인·변경, 위험은 Delete·일괄 삭제·탈퇴다. 클래스만 추가하고 기존 버튼 요소·testid·텍스트는 유지한다.
- 로그인·가입 카드: 가운데 정렬, 라벨이 있는 세로 입력란, 모드 전환 링크형 버튼, 오류 영역을 정리한다.
- 할 일 추가 폼: 입력란 묶음과 간격을 정리한다. 필드·순서·testid·동작은 유지한다.
- 목록 항목: 제목 줄과 메타 줄(우선순위·마감일·D-day·태그·생성/완료일)의 간격·크기·색 대비를 정리한다. 요소 구조와 testid는 유지한다.
- 본문 최대 폭은 640px을 유지한다. 넓히려면 칸반 드래그·375px 테스트를 다시 확인하고 그 결과를 기록한다.
- 반응형: 375px에서 모든 보기와 로그인·계정 화면에 가로 스크롤이 없어야 한다. 560px 이하 규칙(§24)을 유지하고, 계정 패널 폼은 세로로 쌓는다.
- 접근성: `:focus-visible` 3px 윤곽선, `aria-pressed`·`aria-expanded`, 색이 아닌 상태 표시(§24)를 유지한다.

### 41.4 data-testid
- 기존 v4~v7 testid는 **모두 유지**한다. 빠지는 것은 testid가 아닌 `id="auth-open"` 하나다.
- 새 testid(v8): `account-menu`, `account-panel`, `account-created`, `password-form`, `password-current`, `password-new`, `password-confirm`, `password-submit`, `password-error`, `delete-account-form`, `delete-password`, `delete-submit`, `delete-error`, `app-area`(게이트로 숨기는 앱 영역 컨테이너).

### 41.5 버전 문단
- `Version 8.0.0 · 할 일·템플릿은 로그인한 모든 사용자가 함께 씁니다` (기존 `.version` 문단 그대로, 3단계에서 바꾸고 B22와 `/` 응답 테스트를 함께 갱신).

## 42. JMeter·Jenkins 로그인 절차 (E-4)

### 42.1 원칙
- 앱에 인증 우회 설정을 넣지 않는다. 부하 테스트도 실제 사용자와 같은 경로(로그인 → 쿠키 → CSRF 헤더)를 거친다.
- 테스트 계정은 대상 환경마다 **합성 계정 1개**를 둔다(예: 5002 과제 앱, 3004 팀 서버). 가입이 막혀 있으므로(`REGISTRATION_ENABLED=false`) 기존 절차(가입 잠시 허용 → 계정 생성 → 다시 `false`)로 만든다. 계정명·비밀번호는 저장소·로그·보고에 넣지 않는다.
- 자격 증명은 Jenkins Credentials(아이디·비밀번호 형식, 새 ID는 3단계에서 정한다)에만 둔다. JMeter에는 빌드 워크스페이스 밖 임시 디렉터리의 속성 파일(권한 600, `-q`로 전달, `post { always }`에서 삭제)로 넘긴다. 명령줄 `-J`는 프로세스 목록과 콘솔 로그에 노출되므로 쓰지 않는다.

### 42.2 테스트 계획 구조 (`jmeter/fastapi_test_plan.jmx`, 실험용 `concurrency_readonly.jmx`)
1. 스레드마다 **Once Only Controller** 안에서 로그인 샘플러 `AUTH POST /auth/login`(JSON 본문, `Origin` 헤더 없음 — 허용됨)을 1회 보낸다. 200 확인, JSON Extractor로 `csrf_token` → `${csrf}`를 얻고, 스레드 범위 Cookie Manager가 세션 쿠키를 보관한다.
2. 로그인에 실패한 스레드는 측정 샘플러를 보내지 않고 멈춘다(Result Status Action Handler). 그 결과 기대 샘플 수가 맞지 않아 빌드가 실패한다 — 401 잡음이 측정 지표에 섞이지 않는다.
3. 측정 샘플러는 기존 라벨 `GET /`, `POST /todos`, `GET /todos` 그대로다. 쓰기 샘플러에는 `X-CSRF-Token: ${csrf}` 헤더를 붙인다.
4. **분리**:
   - InfluxDB Backend Listener의 `samplersRegex`를 측정 라벨로 한정해 `AUTH ` 라벨을 지표(`transaction='all'` 포함)에서 뺀다. JMeter 버전에서 이 동작이 맞는지는 3단계에서 실측해 확인하고, 맞지 않으면 로그인을 측정 리스너가 보지 않는 위치로 옮긴다.
   - `check_results.py`·Jenkins 응답 코드 판정은 `AUTH` 샘플(스레드 수만큼, 200)과 측정 샘플(THREADS × LOOPS × 3)을 따로 센다. JTL에는 로그인 샘플도 남지만 HTML 리포트·판정에서는 구분한다.
- 로그인 부하: 스레드 10개가 램프업 10초 동안 각자 1회 로그인한다(Argon2 슬롯 대기 5초 안). 성공한 로그인은 시도 제한에 세지 않는다(선기록이 삭제된다).
- 같은 계획은 v7 서버에서도 동작한다(v7도 로그인 경로가 있고 CSRF 헤더를 무시한다). 그래서 서버 버전과 무관하게 먼저 바꿀 수 있다.
- 기존 동시 사용자 실험 결과(u1·u3·u5)는 인증 없는 v7 기준이다. v8 결과에는 요청마다 세션 조회 비용이 더해지므로 직접 비교하지 않는다(R8-2).

### 42.3 Jenkins
- `ci/Jenkinsfile.team3004`(저장소 사본)와 개인 서버 5002 작업: Credentials 바인딩 → 속성 파일 생성 → JMeter → 판정 → 파일 삭제. 팀 서버에 보내는 요청은 GET 확인, 로그인(스레드 수만큼), 기존 30 샘플이다.
- 작업(Job)의 실제 스크립트 수정·빌드 실행·Credentials 등록은 별도 승인을 받는다. 저장소 사본은 3단계에서 고친다.
- Test 단계(pytest)는 빌드마다 새로 만드는 임시 PostgreSQL 그대로이고, 테스트가 계정을 직접 만든다. 바꾸지 않는다.

## 43. 테스트

### 43.1 실행 조건 (V7 §33 유지)
- DB 없는 회귀: `test_ui.py`·`test_ui_auth.py`·`test_db_postgres.py`를 빼고 실행한다.
- `test_ui_auth.py`: 브라우저, `/auth/*` 모킹, DB 없음.
- PostgreSQL 하네스(`test_db_postgres.py`)·PostgreSQL UI(`test_ui.py`): 고정 대상 `todo_test`, **실행마다 승인**, R-19 사전·사후 읽기 전용 점검.
- `test_integration.py`·JMeter·Jenkins 실행: 명시적 승인 없이 실행하지 않는다.

### 43.2 기존 테스트 호환
- `test_api.py`의 기존 테스트는 의미를 바꾸지 않는다. 모듈 픽스처로 `require_user`·`require_csrf`를 고정 사용자로 바꾸는 `dependency_overrides`만 추가한다.
- **분리 원칙(E-15)**: 인증 대체 픽스처는 `test_api.py` 안에서만 쓰고, 인증·CSRF·계정 보안 테스트(`test_auth_routes.py`, `test_db_postgres.py`)는 대체 없이 실제 `require_user`·`require_csrf`를 거친다. 대체가 남아 있지 않은지(`dependency_overrides`가 비어 있는지) 보안 테스트 시작 시 확인한다.
- `test_ui.py`는 3-2부터 실제 로그인 세션으로 돈다. 게이트 도입 뒤에도 그대로 통과해야 한다. 쓰기 요청은 화면의 `api()`가 CSRF 헤더를 붙인다.
- 의미가 바뀌는 기존 테스트(예: `test_ui_auth.py`의 "비로그인에도 v6 앱 보임"·`#auth-open` 펼침, B22 버전 문구)는 변경 목록을 단계 보고에 적는다.

### 43.3 반드시 포함할 확인 (2026-11-15 사용자 지정)
- 비밀번호 변경 뒤 **이전 세션**으로 보낸 요청(`/auth/me`, 데이터 API)은 401.
- 비밀번호 변경 뒤 **이전 비밀번호** 로그인은 401, 새 비밀번호는 200.
- 비밀번호 변경 요청의 **현재 비밀번호 불일치는 400**(401·403이 아님, 세션·해시 그대로).
- 기존 API 테스트의 인증 대체 픽스처와 실제 인증 보안 테스트의 분리(§43.2).

### 43.4 새 테스트
| 구분 | 파일 | 내용 |
|---|---|---|
| 라우트 목록 | `test_auth_routes.py` (DB 없음) | 데이터 경로 15개 모두 `require_user`, 쓰기 11개 `require_csrf`, 그 밖의 경로 목록이 §40.1과 같음 |
| 데이터 인증 | `test_auth_routes.py` (가짜 Session) | 15개 익명 401(DB 미접속), 쓰기 11개 CSRF 없음·불일치 403 + JSON 파일 해시 불변, 정상 세션이면 기존 동작 |
| 계정 API | `test_auth_routes.py` (가짜 Session) | §37 상태 코드 표 전체·검사 순서, 400·429·503·500, 응답·로그에 비밀번호·해시·토큰 없음, 실패 응답에 `Set-Cookie` 없음, Tx2 실패 주입(세션 생성·삭제·해시 UPDATE 각각) → 해시·세션 그대로 |
| 계정 HTTP | `test_db_postgres.py` (승인) | 변경 후 이전 토큰 401·다른 기기 401·새 토큰 200, 이전 비밀번호 401·새 비밀번호 200, `$argon2id$`, 세션 1개만 남음 |
| 동시성 | `test_db_postgres.py` (승인) | §38.3 표의 동시 요청(스레드 2개 + 실제 PostgreSQL 행 잠금), rehash 경쟁 409 |
| 탈퇴·FK | `test_db_postgres.py` (승인) | `pg_constraint` FK 표 = §39.2, 탈퇴 후 users·sessions 0행(다른 기기 포함), 합성 todos·task_templates 행 CASCADE, `data_migrations` 참조 시 409 + 아무것도 안 지움, 같은 username 재가입 201 |
| JSON 보존 | `test_api.py`·`test_db_postgres.py` | 탈퇴 전후 `TODO_FILE`·`TEMPLATE_FILE` SHA-256 동일, 다른 사용자의 목록 응답 동일, 복구 버퍼 유지 |
| 공유 데이터 | `test_db_postgres.py` (승인) | 사용자 A가 만든 할 일을 B가 조회·수정 가능(의도된 공유) |
| UI 모킹 | `test_ui_auth.py` | 게이트(401이면 데이터 요청 0건), 로그인 후 `load()`, 데이터 401 → 로그인 화면, 계정 메뉴 키보드·포커스, 비밀번호 확인 불일치 시 요청 없음, 400·409·422·429 문구, 탈퇴 `confirm` 취소 시 요청 없음, 375px |
| UI 실제 | `test_ui.py` (승인) | 비밀번호 변경 → 새 CSRF로 이후 쓰기 성공, 탈퇴 → 로그인 화면·JSON 보존, 기존 125개 회귀, 칸반 드래그, 두 테마 명도 대비, 375px |

## 44. 단계와 완료 기준 (DoD)

각 단계는 앞 단계 테스트를 모두 통과한 상태에서 시작하고, 단계마다 사용자 승인을 받은 뒤 다음 단계로 간다. PostgreSQL 실행은 실행마다 승인을 받는다.

### 44.0 Spec (이 문서)
- [x] 0-1. 기존 문서와의 충돌 정리(K-1~K-9), `V8_SPEC.md` 작성, `CLAUDE.md`에 v8.0.0 표시·브랜치 상태 정정 (2026-11-15).
- [x] 0-2. 사용자 검토·승인(2026-11-15). 409 2건(E-9·E-10)·시도 제한 공유(E-14)·테스트 분리(E-15) 확정. `feature/v8.0.0` 브랜치 생성 승인.

### 44.1 1단계 — 계정 API와 데이터 API 인증
**1단계 진행 상태 (2026-11-15, 브랜치 `feature/v8.0.0`, 미커밋)** — 구현 완료, DB 없는 검증 완료, PostgreSQL 1차 실행 92/97 통과 → 실패 5건 원인 수정 완료(아래 "PostgreSQL 1차 실행과 보정") → 2차 실행 77 통과·0 실패·20 미실행(R-19 게이트 중단) → R-19 연결 진단 추가(아래 "PostgreSQL 2차 실행과 R-19 진단"). → 3차 실행 **97 passed**(아래 "PostgreSQL 3차 실행"). **1단계 완료(2026-11-15 승인)**.
- 변경: `main.py`(데이터 라우터 + `require_csrf`, `/auth/me` `created_at`, `/auth/password`, `/auth/account/delete`, `require_account_session`), `db.py`(`delete_user_sessions`, `delete_user_if_unchanged`, `AccountDeleteBlocked`). 새 의존성·Alembic 리비전 없음.
- DB 없는 회귀(`test_ui.py`·`test_ui_auth.py`·`test_db_postgres.py` 제외): **1094 passed, 3 deselected** (v7 마감 기준 994 + 새 테스트 100).
- `test_db_postgres.py`에 v8 PostgreSQL 테스트 함수 13개(매개변수 포함 14건)를 추가했지만 **실행하지 않았다**(실행마다 승인, §43.1). `test_ui.py`(PostgreSQL UI)도 실행하지 않았다.
- `test_ui_auth.py`(DB 없는 브라우저): **5 passed, 26 failed** — v7 과도기 화면(비로그인에도 앱 표시)이 이제 데이터 API 401을 받아 `invalidateAuth()`로 로그인 폼을 펼치기 때문이다. 2단계 로그인 게이트(§41.1)에서 이 테스트들을 다시 쓴다(R8-8). 1단계에서는 화면 코드를 바꾸지 않았다.
- 바꾼 기존 테스트(의미 변경 목록): `test_api.py` 인증 대체 픽스처 추가·경로 목록 테스트(계정 경로 2개, `data_router`), `test_auth_routes.py`·`test_request_db.py`의 `/auth/me` 기대값에 `created_at`, 가짜 사용자에 `created_at`, `test_db_schema.py` import 검사 스크립트에 인증 대체(저장 경로만 검사), `conftest.py` UI 서버 준비 확인 경로 `/todos` → `/`.

**PostgreSQL 1차 실행과 보정 (2026-11-15)**
- 1차 실행(승인, `todo_test` 고정 대상, `tests/test_db_postgres.py` 전체 1회): **92 passed, 5 failed**(138초). v8 PostgreSQL 테스트 14건은 모두 통과했다(§43.3 필수 검증 포함). 실행 전후 읽기 전용 R-19 점검은 같았다 — PostgreSQL 18.6·`127.0.0.1:5433`·`todo_test`·비슈퍼유저, 게이트 PASS(0002), 표·시퀀스·인덱스·타입 8/5/18/16, 사용자 데이터 7개 표 0행, 다른 세션·잠금·준비된 트랜잭션 0, 연결 1. 실제 `todo.json` 해시 불변, `task_templates.json` 없음 그대로.
- 실패 5건과 보정(테스트·하네스만, 앱 동작과 API 계약은 그대로):

  | 테스트 | 원인 | 보정 |
  |---|---|---|
  | `test_http_register_me_logout_flow`, `test_mp_session_and_csrf_shared_across_processes` | `/auth/me` 응답 전체를 가입 응답과 같다고 비교 — v8에서 `created_at` 추가(§37, C-11) | 헬퍼 `assert_me_matches_auth`: 가입 응답 필드(`id`·`username`·`csrf_token`) 값 그대로 + 키 집합이 정확히 그 셋 + `created_at`, `created_at`은 시간대 있는 ISO 8601이고 지금 이전 |
  | `test_mp_user_limit_shared_across_processes`, `test_mp_concurrent_failures_exact_across_processes`, `test_uvicorn_two_workers_distribute_and_share_limit` | **v8 이전부터 있던 문제**: `main` 커밋 `77bb6fa`(Loki·Alloy)가 `uvicorn.access`를 끄고 `custom.access`로 PID 없는 줄을 남겨, 하네스가 읽는 `ACCESS pid=` 줄이 0개. 응답 상태(401·429) 검증은 통과했고 워커 관찰만 실패 | `tests/pg_harness.py`의 `SERVER_LOG_CONFIG`에 `custom.access` 로거용 처리기(`ACCESS pid=%(process)d %(message)s`)를 더하고, `_ACCESS` 정규식을 앱 줄 형식 전체(`IP - "METHOD PATH HTTP/1.1" 상태 0.000s`)에 맞춰 고정했다. 테스트의 PID 단언(워커 PID 집합 일치, 두 워커 분산)은 그대로다. 앱의 로그 설정·stdout 원문 줄(Loki·Alloy 파싱 대상)은 바꾸지 않았다 |

  - 접근 로그 보정은 DB 없이 `uvicorn --workers 2`(APP_ENV=test, 임시 JSON)로 확인했다: PID 줄 41개, 접근 로그의 PID 집합 = 기동 로그의 워커 PID 2개, 앱 원문 줄 41개 그대로. 끝난 뒤 남은 프로세스 0.
- 라우터 등록 검증 보강: FastAPI 0.142는 `include_router`를 지연 등록해 `app.routes`에 데이터 경로 15개가 `APIRoute`로 나오지 않는다. 그래서 `app.routes`만 도는 검사가 데이터 경로를 조용히 건너뛰었다. 보강 내용(비공식 FastAPI 내부 함수는 쓰지 않는다):
  - `test_auth_routes.py`: 공개 API `app.openapi()`로 앱에 등록된 경로가 데이터 15 + 인증 6과 정확히 같은지, HTTP 매트릭스(익명 401·CSRF 403)가 15개를 빠짐없이 한 번씩 덮는지 확인. 새 `test_data_routes_document_401_and_write_403_in_openapi`, 쓰기 11개가 세션·CSRF가 맞으면 인증을 통과하고 요청 Session을 1개만 여는지 보는 `test_data_writes_with_csrf_pass_auth_with_one_request_session`(읽기 4개는 기존 테스트).
  - `test_api.py`: 경로 목록 테스트에 OpenAPI 기준 등록 확인 추가.
  - `test_request_db.py`: Session scope 검사가 선언 라우터도 돌고, 돌아본 경로가 OpenAPI 21개를 모두 덮는지 확인(건너뛰면 실패). `include_router`에 준 의존성의 scope는 HTTP 요청(Session 1개)으로 확인한다.
  - 변이 확인: 임시 사본에서 `include_router` 줄을 지우면 위 검사 15개가 실패했다.
- OpenAPI 문서 보완(동작 변화 없음): `include_router(..., responses={401})`로 데이터 15개에 401, 쓰기 11개 선언에 `CSRF_RESPONSE`(403). GET 4개에는 403을 적지 않는다(CSRF를 보지 않음). 데이터 경로의 세션 조회 DB 오류 500·503(§40.3)은 문서에 적지 않았다.
- 보정 후 DB 없는 회귀(`test_ui.py`·`test_ui_auth.py`·`test_db_postgres.py` 제외): **1106 passed, 3 deselected**(1094 + 새 테스트 12). PostgreSQL 재실행·`test_ui.py`는 하지 않았다(승인 대기). 아래 1-2~1-6은 재실행 결과로 체크한다.

**PostgreSQL 2차 실행과 R-19 진단 (2026-11-15)**
- 2차 실행(승인, `todo_test` 고정 대상, `tests/test_db_postgres.py` 전체 1회): **77 passed, 0 failed, 20 not run**(39.8초). 78번째 테스트(`test_position_after_delete_restore_and_purge`)의 `fresh_schema` 준비에서 R-19 게이트가 **DB 연결 수 2**를 감지해 스키마를 지우기 전에 세션을 멈췄다(`pytest.exit`, "DB 전체 연결 수 2 (점검 연결 1개만 허용, 다른 역할 세션 포함)"). 게이트 앞 대기(`wait_until_single_backend`)는 연결 수 1을 보고 통과했고, 같은 역할의 다른 세션은 0이었다 — 대기와 게이트 사이의 짧은 접속으로 보이며, 정체는 확인하지 못했다.
  - v8 PostgreSQL 테스트 14건: **모두 통과**(비밀번호 변경·세션 무효화·현재 비밀번호 400·시도 제한 공유·세션 교체 실패 rollback·rehash 경합 409·동시 요청 3종·FK 카탈로그·탈퇴 CASCADE와 공유 JSON 보존·참조 409·탈퇴 비밀번호 400·`/auth/me` `created_at`·데이터 API 인증 매트릭스).
  - 1차 실패 5건: `test_http_register_me_logout_flow` 통과(해결 확인). 나머지 4건(`test_mp_*` 3건, `test_uvicorn_two_workers_distribute_and_share_limit`)은 중단 지점 뒤라 **미실행·미확인**.
  - 실행 전후 읽기 전용 R-19 점검 같음: 게이트 PASS(0002), 8/5/18/16, 사용자 데이터 7개 표 0행, 다른 세션·잠금·준비된 트랜잭션 0, 연결 1. 실제 `todo.json` 해시 불변, `task_templates.json` 없음 그대로. 남은 프로세스 0. 중단으로 남은 합성 행 없음.
  - 읽기 전용으로 본 서버 설정: `autovacuum = on`, `autovacuum_naptime = 1min`. autovacuum 작업자가 이 DB에 잠깐 접속해 `numbackends`에 잡혔을 수 있다는 것은 **가설일 뿐 확인된 원인이 아니다**. 다른 PostgreSQL 백그라운드 작업, 테스트 프로세스, 외부 연결 가능성도 열려 있다. **원인 미확정**(`V7_PLAN.md` R-19 "연결 수 2 사건" 4번째).
- R-19 연결 진단 추가(테스트 하네스만, 2026-11-15 승인) — 목적: 다음에 게이트가 연결 수 이상으로 멈출 때 끼어든 백엔드가 무엇인지 기록해 원인을 실제 자료로 판단한다.
  - `tests/pg_harness.py`: `describe_backends(conn)`·`diagnose_backends_on_failure(conn)`·`BACKEND_DIAG_SQL`. 게이트(`db.verify_test_schema`)가 `DatabaseConfigError`로 멈추면 **멈추게 한 그 연결·트랜잭션에서** `pg_stat_activity`를 읽어 같은 예외 종류로 메시지에 덧붙인다(기존 처리기가 그대로 `pytest.exit`). 사전 대기(`wait_until_single_backend`)의 중단 3경로와 `wait_for_backends_released`의 실패에도 같은 진단을 붙였다. 게이트 호출 6곳(`reset_test_schema` 2곳, `open_verified_engine`, `verify_gate_readonly`, `test_db_postgres.py`의 리비전 게이트 테스트 2곳)을 감쌌다.
  - 기록 항목: 이 DB의 백엔드마다 PID(자기 연결 표시 — `pg_backend_pid()` 비교), `backend_type`, `usename`, `application_name`(인쇄 가능 문자만·공백 치환·40자), `state`, `backend_start`(UTC), 클라이언트 종류(`none`/`loopback`/`other`, 주소 자체는 기록하지 않음), 권한 부족으로 세부가 가려졌는지. 같은 트랜잭션의 `numbackends`와 행 수를 함께 적고, 서버 전체 `backend_type`별 개수(이름 없음)도 적는다. 비슈퍼유저라 다른 역할의 일부 열은 `-`로 나올 수 있다.
  - 기록하지 않는 것: SQL 본문(`query`는 권한 부족 표시와 같은지 비교만), 비밀번호·토큰, 클라이언트 IP, 행 데이터.
  - 안전 조건: 게이트 판정 코드(`db.py`)는 바꾸지 않았다(연결 수 1 조건 그대로). 진단은 멈출 때만, 새 연결 없이 읽기만 한다(commit·rollback·SET·연결 종료·설정 변경 없음, autovacuum 설정 그대로). 진단이 실패해도 원래 예외로 멈추고 계속 진행하지 않는다. 성공 경로와 다른 종류의 오류에는 진단 SQL을 실행하지 않는다. 스키마 삭제 전 중단 순서는 그대로다.
  - DB 없는 검증: `tests/test_db_schema.py`에 가짜 연결 테스트 15건(진단 SQL 읽기 전용·본문·주소 미기록, 자기 연결 표시·정리, 진단 실패 시에도 같은 예외, 성공·다른 오류 불간섭, 연결 수 2 게이트에서 DROP·CREATE·LOCK·SET·upgrade 없이 `pytest.exit` + 진단, 통과 시 진단 없음, 대기 3경로·정리 대기 실패의 진단)과 AST 구조 테스트 갱신(게이트 호출은 래퍼 안에 그 호출 하나만). 변이 확인: 래퍼가 예외를 삼키게 바꾸면 2건 실패. DB 없는 회귀 **1121 passed, 3 deselected**(1106 + 15). `tests/test_db_postgres.py`는 DB 주소 없이 수집만(1 skipped). PostgreSQL 실행은 하지 않았다.
**PostgreSQL 3차 실행 (2026-11-15, 승인) — 1단계 최종 검증**
- `tests/test_db_postgres.py` 전체 1회(`todo_test` 고정 대상): **97 passed, 0 failed, 0 skipped, 0 not run**(94.8초). R-19 게이트 중단 없음(진단 메시지 0건 — 진단은 작동할 일이 없었다).
- 1차 실패 5건 **모두 해결**: `test_http_register_me_logout_flow`(2차에서 확인), `test_mp_session_and_csrf_shared_across_processes`, `test_mp_user_limit_shared_across_processes`, `test_mp_concurrent_failures_exact_across_processes`, `test_uvicorn_two_workers_distribute_and_share_limit`(접근 로그 PID로 두 워커 분산 확인, "검증 미완료" 없음).
- 2차 미실행 20건 모두 통과, 2차 통과 77건 중 회귀 없음. v8 PostgreSQL 테스트 14건 모두 통과.
- 실행 전후 읽기 전용 R-19 점검 완전히 같음: PostgreSQL 18.6·`127.0.0.1:5433`·`todo_test`·`todo_test_user`·비슈퍼유저, 게이트 PASS(0002), 8/5/18/16, 사용자 데이터 7개 표 0행, 다른 세션·잠금·준비된 트랜잭션 0, 연결 1. 실제 `todo.json` 해시 불변, `task_templates.json` 없음 그대로, 남은 프로세스 0.
- R-19 간헐적 연결 수 증가(2차 실행 중단)의 원인은 **미확정**으로 남는다(autovacuum은 가설). 진단 코드는 다음 발생 때 기록하도록 유지한다.
- 1단계 최종 수치: PostgreSQL **97/97**, DB 없는 회귀 **1121 passed·3 deselected**. `test_ui_auth.py` 26건 실패(v7 과도기 화면)는 2단계에서 고친다(R8-8). `test_ui.py`는 2단계 검증 범위.

- [x] 1-0. 시작 기준선: v7 마감 기록의 DB 없는 회귀 994 passed·3 deselected를 기준으로 했다(브랜치 생성 직후 재실행은 하지 않았다).
- [x] 1-1. `GET /auth/me`의 `created_at`, `POST /auth/password`, `POST /auth/account/delete`가 §37 표대로 동작한다(DB 없는 테스트, 2026-11-15).
- [x] 1-2. 비밀번호 변경: 모든 이전 세션 401, 새 세션·새 CSRF, 이전 비밀번호 401, Tx2 실패 시 해시·세션 불변·`Set-Cookie` 없음, §38.3 동시성 표 통과(PostgreSQL).
- [x] 1-3. 회원 탈퇴: 계정·전체 세션 삭제, FK 카탈로그 = §39.2, `data_migrations` 참조 409, 재가입 가능, JSON 두 파일 해시 불변(PostgreSQL + DB 없음).
- [x] 1-4. 데이터 API 15개 익명 401, 쓰기 11개 CSRF 403(파일 불변), 라우트 목록 테스트, 기존 `test_api.py` 의미 변경 없이 통과. — DB 없는 부분·PostgreSQL 매트릭스 모두 통과(2026-11-15).
- [x] 1-5. 비밀번호·해시·세션 토큰·CSRF 토큰이 응답(정해진 필드 외)·로그에 없음, 시도 제한이 로그인과 예산 공유(§38.2).
- [x] 1-6. 새 Alembic 리비전·새 의존성 없음, 시작 가드·v7 인증 경로 회귀 통과, PostgreSQL 실행 전후 R-19 점검 통과·종료 후 사용자 데이터 0행.

### 44.2 2단계 — UI

**2단계 진행 상태 (2026-11-15, 착수 승인, 미커밋)** — 구현 완료, DB 없는 검증 완료, PostgreSQL UI 125/125 통과(아래). **2단계 완료(2026-11-15 승인)**.
- 화면(`templates/index.html`): 로그인 게이트(`app-area`, 시작은 로그아웃 상태로 그리고 `/auth/me` 200 이면 앱을 열고 불러오기 시작, 401·그 밖의 오류면 로그인 카드만, 데이터 401 → 로그인 카드 "다시 로그인하세요."·`#error` 에는 쓰지 않음·포커스는 아이디 입력란), 계정 버튼 `account-menu`(안에 `current-user`)·계정 패널(내 정보·가입일 KST·비밀번호 변경·회원 탈퇴), 공유 데이터 안내(로그인 카드·알림 줄 왼쪽·탈퇴 안내), `#auth-open`과 높이 상쇄 CSS 제거, 간격·모서리 토큰, 버튼 3단계(`btn-primary`·`btn-danger`·`btn-link`), 로그인 카드 세로 폼, 목록 제목 줄 강조. 1단계 API·보안 동작, DB, 서버 코드는 바꾸지 않았다.
- 레이아웃 제약 실측(R-32·R8-5, 1280×720, 같은 가짜 데이터): v7 [칸반] 버튼 top 718.4px. 할 일 추가 폼을 테두리 상자(안쪽 여백)로 묶으면 폼 184.2→220.6px·버튼 754.7px 로 내려가 Playwright 칸반 드래그가 다른 카드를 집었다(PUT 대상이 바뀜). 그래서 폼 상자와 주요 버튼의 안쪽 여백은 넣지 않았다(주요·위험 버튼은 색·굵기만, 큰 여백은 로그인·계정 폼만). 최종 716.4px, 드래그 양방향 정상.
- `tests/test_ui_auth.py` 재작성(DB 없음): 데이터 API 도 브라우저 안 가짜 응답(`FakeData`)으로 흉내 낸다(로그인 필수라 DB 없는 서버로는 데이터를 받을 수 없음, 앱에 인증 우회 없음). 기존 31건(26 실패 포함)을 모두 v8 의미로 옮기고(삭제·skip 없음) 계정 메뉴·비밀번호 변경·탈퇴·게이트·칸반 드래그·testid 유지·375px·대비 테스트를 더해 **54 passed**.
  - 의미가 바뀐 기존 테스트: 401 시작 화면(앱 표시 → 로그인 카드만·데이터 요청 0건), Tab 순서(접힌 폼 건너뛰기 → 테마·로그인 폼), 500 시작(목록 유지 → 로그인 카드+안내), 로그인 전 POST(헤더 없음 → 로그인 전에는 요청 자체가 없음), 데이터 401(`#error`도 표시 → 로그인 카드에만 안내), 로그인 뒤 헤더 순서(테마 → 로그아웃 → 테마 → 계정 → 로그아웃), `#auth-open` 펼침·접힘(제거된 요소 — 카드가 늘 펼쳐져 있음을 확인), 다크 로그인 대비(→ 라이트·다크 + 계정 패널·오류 문구).
- `tests/test_ui.py`(PostgreSQL, 미실행): `test_keyboard_theme_and_focus_visible`의 헤더 Tab 순서만 테마 → 계정 → 로그아웃 → 종으로 고쳤다.
- DB 없는 회귀(`test_ui.py`·`test_ui_auth.py`·`test_db_postgres.py` 제외): **1121 passed, 3 deselected**(1단계와 같음). 실제 `todo.json` 해시 불변.
- **PostgreSQL UI 실행 (2026-11-15, 승인)**: `tests/test_ui.py` 전체 1회 **125 passed, 0 failed·skipped·not run**(142초), 실제 `todo_test` 로그인 세션. R-19 게이트 중단·진단 메시지 0건. 칸반 `drag_to` 테스트 4건(양방향·같은 열·실패 롤백·반복 다음 카드)과 칸반 테스트 10건 모두 통과, `test_mobile_375_every_view`·`test_contrast_in_every_view` 라이트·다크 통과, 헤더 Tab 순서(테마 → 계정 → 로그아웃 → 종) 통과. 실행 전후 읽기 전용 R-19 점검 완전히 같음(게이트 PASS 0002, 8/5/18/16, 사용자 데이터 7개 표 0행, 다른 세션·잠금·준비된 트랜잭션 0, 연결 1). 실제 `todo.json` 해시 불변·`task_templates.json` 없음, 테스트 서버·브라우저 프로세스 0. R-19 간헐 연결 수 증가의 원인은 계속 **미확정**.
- **알려진 제한(2단계 보류 사항)**: ① 할 일 추가 폼의 테두리 상자 정리는 넣지 않았다 — 앱 영역 세로 배치가 Playwright 칸반 드래그에 민감하다(R-32·R8-5, 위 실측). ② 로그인한 사용자도 `/auth/me` 응답 전 잠깐 로그인 카드를 본다(늦은 응답 중에도 로그인할 수 있게 한 선택). ③ `#status-message`가 앱 영역 안이라 로그아웃 상태에서 테마를 바꾸면 안내 문구가 보이지 않는다.
- [x] 2-1. 로그인 게이트: 401이면 앱 영역 숨김·데이터 요청 0건, 로그인·가입 후 앱 표시, 데이터 401 → 로그인 화면.
- [x] 2-2. 계정 메뉴: 내 정보(가입일 KST), 비밀번호 변경(확인 불일치 무요청, 상태별 문구, 성공 시 새 CSRF로 이후 요청 성공), 탈퇴(`confirm` 취소 무요청, 성공 시 로그인 화면), 키보드·Esc·포커스 복귀.
- [x] 2-3. (할 일 폼 상자는 R-32 실측으로 보류 — 아래 알려진 제한) CSS 정리(§41.3): 간격 토큰·버튼 3단계·로그인 카드·폼·목록 가독성, 기존 testid 전부 유지, `#auth-open`·높이 상쇄 제거.
- [x] 2-4. 라이트/다크/시스템 모든 보기·로그인·계정 화면 명도 대비(4.5:1·3:1), 375px 가로 스크롤 없음, 칸반 드래그 3개 통과.
- [x] 2-5. `test_ui_auth.py` 통과, `test_ui.py` 전체 통과(PostgreSQL, 승인), 의미가 바뀐 기존 테스트 목록 보고.

### 44.3 3단계 — 안정화와 마감

**3단계 진행 상태 (2026-11-15, 착수 승인, 미커밋)** — 구현·DB 없는 검증 완료. **실제 PostgreSQL(하네스·UI)·통합 테스트·JMeter·Jenkins 실행은 승인 대기.**
- 버전: 화면 `Version 8.0.0 · 할 일·템플릿은 로그인한 모든 사용자가 함께 씁니다`(§41.5). `test_api.py`의 `/` 응답(새 문구 포함·`Version 7.0.0` 없음)은 통과, `test_ui.py` B22(`.version` 전체 문구)는 고쳤고 실행 전. 1280×720 실측: 버전 문단 높이 그대로(19.0px), [칸반] 버튼 716.4px, 칸반 드래그 양방향 정상(DB 없음, 가짜 데이터).
- 보안 회귀(3-1): DB 없는 `test_export_links_without_session_are_401`(CSV·JSON), PostgreSQL `test_http_v8_security_regressions`(같은 사용자의 다른 세션 CSRF 403, 비밀번호 변경 뒤 옛 쿠키·옛/새 CSRF 로 데이터 읽기·쓰기 401, 탈퇴 뒤 쿠키로 데이터 읽기·내보내기·쓰기 401, 비로그인 내보내기 401, 거부 요청은 공유 JSON 불변 — 실행 전). 다른 사용자 토큰 재사용 403 은 1단계 매트릭스에 이미 있다.
- 통합 테스트(3-3): `test_integration.py`를 `INTEGRATION_USERNAME`·`INTEGRATION_PASSWORD` 환경변수 로그인(없으면 실패) → `requests.Session` 쿠키 + `X-CSRF-Token` → 끝나면 로그아웃으로 바꿨다. 익명 401(목록·통계·템플릿·CSV 내보내기)·CSRF 없음 403·`/auth/me` 필드 확인을 더했다(9개, 수집만 확인).
- JMeter·Jenkins(3-2): 계획 2개에 스레드별 Cookie Manager, Once Only 로그인(`AUTH POST /auth/login`, `props.get()` 값을 `JsonOutput.toJson`으로 JSON 이스케이프, 200 확인, `csrf_token` 추출, 실패 시 스레드 중지), "Measured" 컨트롤러(측정 샘플러 + InfluxDB 리스너, `samplersRegex` 를 측정 라벨로 고정), `POST /todos`의 `X-CSRF-Token`, 마지막 반복의 `AUTH POST /auth/logout`. **R8-9 판단**: JMeter InfluxDB 리스너는 `transaction='all'` 합계에 `samplersRegex`와 관계없이 본 샘플을 모두 더하므로 정규식만으로는 분리되지 않는다 — §42.2 의 대안("측정 리스너가 보지 않는 위치")을 처음부터 적용했다(실측은 실행 승인 뒤, Jenkins 사본이 이번 실행 구간의 `AUTH` 지표 0건을 판정한다). `check_results.py --expected-auth`·`analyze_jtl.py --expected-auth`가 인증 샘플을 따로 센다. 자격 증명은 `jmeter/auth_properties.py`(새 파일·600·Java properties 이스케이프·값 미출력, 비대화형에서 환경변수가 없으면 실패) → `-q`·읽기 전용 마운트, 명령줄 `-J` 없음. `ci/Jenkinsfile.team3004` 사본: Credentials `jm-team3004-test-account`(Username with password)를 `withCredentials`로 환경변수에만 받고(작은따옴표 `sh` — Groovy 보간 없음), `${WORKSPACE_TMP}` 아래 700 디렉터리·600 파일, EXIT 트랩과 `post { always }`에서 삭제. 팀 서버 확인은 `TEAM_APP_VERSION`(8.0.0: 비로그인 `/todos` 401). 실험 스크립트는 `JMETER_AUTH_FILE`(600·소유자 확인), 할 일 개수는 로그인해 센다.
- DB 없는 정적 검증 `tests/test_jmeter_plan.py` 19건(계획 구조·리스너 범위·CSRF 헤더·`-J` 없음·속성 파일 이스케이프 왕복·600·덮어쓰기 거부·값 미출력·판정 스크립트 분리·Jenkins 사본 규칙).
- 문서(3-5): README(8.0.0 절, 계정 기능, 데이터 API 정책, 공유 데이터·탈퇴 한계, JMeter 로그인 절차, 테스트 실행 조건·결과), `RELEASE_NOTES.md` 8.0.0 절, `jmeter/experiments/README.md` 계정 파일 절차.
- DB 없는 회귀: **1142 passed, 9 deselected**(2단계 1121 + 새 21; deselected 9 = integration). `test_ui_auth.py` **54 passed**. 실제 `todo.json` 해시 불변.
- 범위 밖으로 남긴 것: 5002 Jenkins 작업의 실제 스크립트(저장소에 없음)에 같은 로그인 절차 반영, Credentials 등록, 테스트 계정 생성, 배포.
- **3단계 PostgreSQL 실행 (2026-11-15, 승인, 순서대로 따로 1회씩 — 동시 실행 없음)**:
  - 하네스 `tests/test_db_postgres.py`: **98 passed, 0 failed·skipped·not run**(96초). 새 `test_http_v8_security_regressions` 통과, v8 테스트 15건·멀티프로세스 3건·워커 분산(검증 미완료 없음) 통과. R-19 게이트 중단·진단 메시지 0건.
  - UI `tests/test_ui.py`: **125 passed, 0 failed·skipped·not run**(142초). B22(`Version 8.0.0 · 할 일·템플릿은 로그인한 모든 사용자가 함께 씁니다`) 통과, 칸반 10건(드래그 4건·모바일 터치 포함)·375px·라이트/다크 대비·키보드 순서 통과. R-19 게이트 중단·진단 메시지 0건.
  - 두 실행 모두 실행 전후 읽기 전용 R-19 점검이 **완전히 같았다**(PostgreSQL 18.6·`127.0.0.1:5433`·`todo_test`·`todo_test_user`·비슈퍼유저, 게이트 PASS 0002, 8/5/18/16, 사용자 데이터 7개 표 0행, 다른 세션·잠금·준비된 트랜잭션 0, 연결 1). 실제 `todo.json` 해시 불변·`task_templates.json` 없음, Git 작업 트리 불변, 테스트 서버·브라우저 프로세스 0.
  - R-19 간헐 연결 수 증가(1단계 2차 실행 중단)의 원인은 계속 **미확정**이다(autovacuum 은 가설). 진단 코드는 유지한다.
- **통합 테스트 (2026-11-15, 승인, 로컬 격리 1회)**:
  - `INTEGRATION_BASE_URL` 필수화: 이전 외부 기본 주소(`163.239.77.78:5014`)를 없앴다. 없거나 빈 값·공백·`http(s)://호스트[:포트]` 형식이 아니면 각 테스트의 준비 단계(fixture)에서 요청 전에 실패한다(모듈 import 때 실패시키면 기본 DB 없는 회귀의 수집이 깨지므로). 기존 9건의 검증 내용은 그대로다. DB 없는 `tests/test_integration_guard.py` 11건: 주소 검증 6종·명시 주소 사용·외부 주소 문자열 없음, 그리고 주소 없이 `-m integration` 을 자식 프로세스로 돌려 9건 모두 준비 단계 실패 + 소켓 연결·DNS 조회 시도 0건 + 비밀번호 미출력.
  - 실행 방식: 저장소 밖 실행기(scratchpad)가 `pg_harness.py` 절차를 그대로 쓴다 — 고정 대상·`DATABASE_URL`·`PG*`(PGPASSFILE 제외) 거부 → `open_verified_engine` → 시작 전 0행 → `reset_test_schema`(R-19 게이트) → `start_server`(127.0.0.1 임의 포트, `todo_test`, 임시 JSON — 주소 형식·경로 겹침 확인) → 합성 계정(가입·로그아웃·로그인) → 자식 `pytest -m integration`(대상·계정은 자식 환경변수로만, DB 접속 변수 미전달, 출력의 계정 값 가림) → 서버 종료(실패해도) → `_finish_ui_session`(연결 정리·읽기 전용 게이트·출처 확인·잠금 아래 재확인·RESTRICT 정리).
  - 결과: **9 passed, 0 failed·not run**(로컬 v8 서버 `127.0.0.1` 임의 포트). 출처 확인 통과 후 정리, 사용자 데이터 0행. 실행 전후 읽기 전용 R-19 점검 **완전히 같음**. 실제 `todo.json` 해시 불변·`task_templates.json` 없음, 임시 JSON 은 테스트가 만든 항목을 지워 0개, 계정 값 로그 노출 0회, 서버·브라우저 프로세스 0, 외부 HTTP 요청 없음.
  - DB 없는 회귀: **1153 passed, 9 deselected**(1142 + 가드 11).
- **JMeter 5.6.3 로컬 격리 검증 1회 (2026-11-15~16, 승인) — 미통과**:
  - 설치: `archive.apache.org` 공식 배포 파일, SHA-512 공식 값 일치(`downloads.apache.org` 체크섬과도 같음), scratchpad 에만 압축 해제(시스템 설정·PATH 변경 없음, Docker 미사용).
  - 환경: 저장소 밖 실행기가 `pg_harness.py` 절차 재사용(고정 대상 `todo_test`, R-19 게이트, `127.0.0.1` 임의 포트 v8 서버, 임시 JSON, 무작위 합성 계정), 가짜 InfluxDB 수신기 `127.0.0.1` 전용. JMeter 는 `java -jar`(cmd 해석 없음), 죽은 프록시 + `-N 127.0.0.1`(127.0.0.1 밖 요청 불가), 자격 증명은 `-q` 속성 파일(Windows ACL 현재 사용자만, 실행 뒤 삭제), `-J` 에 자격 증명 없음.
  - 결과(THREADS=10, LOOPS=1): 로그인 **10/10**, 측정 **30/30**(`GET /` 200·`POST /todos` 201·`GET /todos` 200 각 10), 로그아웃 **0/10(실행되지 않음)**. `check_results.py --expected 30 --expected-auth 20` **실패**("인증 샘플 수 10 != 기대 20" — JMeter 종료 코드는 0 이었다). 가짜 InfluxDB: **AUTH 행 0건**, `transaction=all·statut=all` count 합 **30**(측정 샘플만 — R8-9 분리 실측 확인). JTL·로그·리포트·수신 기록에 비밀번호·아이디·세션 쿠키·`csrf_token` 노출 0, JTL 에 본문·헤더 열 없음. 실제 `todo.json` 해시 불변·`task_templates.json` 없음, JMeter·서버·수신기 프로세스 0.
  - 원인: 로그아웃 If Controller 조건식 `vars.get('__jm__FastAPI Load Test__idx').toInteger()` 가 10개 스레드 모두 **NullPointerException**(`jmeter.log` 의 WARN `Error running groovy script` — ERROR/FATAL 검사에 걸리지 않음). JMeter 5.6.3 은 반복 번호 변수를 `__jm__<반복 컨트롤러 이름>__idx` 로 만들고, 스레드 그룹 시작 때 그 이름을 스레드 그룹 이름으로 바꾸는데 JMX 의 `ThreadGroup.main_controller` 에 이름 속성이 **없으면 바꾸지 못한다**(바이트코드 확인 — 손으로 쓴 JMX 라 이름 속성이 없다). 실험 계획도 같은 형태였다.
  - 정리: 실행 뒤 R-19 출처 확인이 **잔여 세션을 발견해 정리를 중단**했다(기대 세션 1 ≠ 실제 11, 지우지 않음). 남은 합성 행(`todo_test`): users 1, sessions 11(하네스 1 + JMeter 10), auth_failures 1. 2026-11-16 정리 전 읽기 전용 점검에서 같은 값·다른 세션·잠금·준비된 트랜잭션 0·연결 1 을 다시 확인했다. **잔여 데이터 정리는 하지 않았다** — 제안했던 `test_gate_accepts_state_left_by_migrations` 는 `fresh_schema` 만 쓰고 V7_PLAN 이 기록한 "`rolled_back` 테스트 하나로 초기화"와 문구가 다르며, `fresh_schema` 경로는 R-19 게이트만 거치고 하네스의 출처 확인·잠금(`before_drop`, UI 종료 경로 전용)을 쓰지 않아 승인 조건("출처 확인과 잠금 절차를 그대로 사용")을 만족하지 못한다. 정리 방법은 다시 승인받는다. 이 잔여 행이 있는 동안 `test_ui.py` 는 시작 전 검사에서 멈춘다.
  - 수정(2026-11-16, 승인): 두 계획의 로그아웃 조건식을 `${__groovy(vars.getIteration() == (props.get('LOOPS') ?: '1').toInteger())}`(실험 계획은 기본 `'50'`)로 바꿨다 — `vars.getIteration()` 은 JMeter 가 스레드 반복마다 1부터 올리는 값이라 이름과 무관하다. 다른 샘플러·경로·라벨·인증 흐름은 그대로. `ci/Jenkinsfile.team3004`·`run_concurrency_experiment.sh` 의 `jmeter.log` 판정에 `Error running groovy script` 를 더했다(ERROR·FATAL·샘플 수·인증 실패 판정 유지). `tests/test_jmeter_plan.py` 는 조건식 전체·기본 반복 수 일치·`__jm__` 미사용·로그 판정 패턴을 확인한다(이전 테스트는 버그 형태인 `__jm__` 포함을 확인하고 있었다). 정적 검증: JMX XML 파싱, Python·Bash 문법, Jenkinsfile 괄호·따옴표 짝, `test_jmeter_plan.py` 20 passed, DB 없는 회귀 **1154 passed, 9 deselected**.
  - **잔여 합성 행 정리 (2026-11-16, 승인 — 방법 (나), 1회)**: 처음 JMeter 검증이 미통과였다는 기록은 그대로다.
    - 정리 전 READ ONLY 점검: 고정 대상(18.6·`127.0.0.1:5433`·`todo_test`·`todo_test_user`·비슈퍼유저), 게이트 PASS 0002, 8/5/18/16, users 1·sessions 11·auth_failures 1·나머지 0, 다른 세션·잠금·준비된 트랜잭션 0, 연결 1 — 예상과 일치.
    - 출처 확인(행 개수만이 아니라 값으로, 비밀번호·토큰·CSRF 열은 읽지 않음): ① 7개 표 행 수 정확히 일치 ② 유일한 사용자의 아이디가 하네스 합성 계정 형식(`ui32_` + 16진 8자리)이고 가입 시각이 JMeter 첫 로그인 전 5분 안 ③ 세션 11개 모두 그 사용자 소유·모두 만료 = 생성 + 14일, 10개의 생성 시각이 JTL 의 JMeter 로그인 샘플 10건과 1:1(샘플 시작 ≤ 생성 ≤ 끝 + 2초), 남은 1개는 가입 뒤·JMeter 전(하네스 로그인) ④ auth_failures 1개가 `kind='register'`·`username_key` 없음·`127.0.0.1`·가입 시각과 같은 created_at(하네스 `UI_ORIGIN_SQL["register_record"]` 기준) ⑤ 로컬 서버 접근 로그의 인증 요청이 register 201 ×1·하네스 logout 204 ×1·login 200 ×11(JMeter logout 0)로 DB 와 같은 내용.
    - 절차: 저장소 밖 정리 스크립트가 먼저 읽기 전용으로 ①~④ 를 확인(통과)한 뒤 기존 `pg_harness.reset_test_schema(engine, cfg, before_drop=...)` 경로로 1회 실행 — 한 트랜잭션 안에서 서버 확인·R-19 게이트·`lock_timeout` 5초·8개 표 ACCESS EXCLUSIVE 잠금·게이트 재검사(연결 수 1) 뒤 같은 연결·잠금 아래에서 ①~④ 재확인 → 통과해서 알려진 8개 표·빈 public 을 RESTRICT 로 삭제·재생성 → commit → Alembic upgrade head(별도 연결, R-36 의 기존 공백). 출처 확인과 삭제 사이에 트랜잭션 경계가 없다. 수동 DELETE·TRUNCATE·DROP SQL 은 쓰지 않았고 게이트 조건은 그대로다.
    - 정리 후 READ ONLY 점검: 게이트 PASS 0002, 8/5/18/16, Alembic 0002, 사용자 데이터 7개 표 0행, 다른 세션·잠금·준비된 트랜잭션 0, 연결 1 — 통합 테스트 실행 전 기준과 **완전히 같음**. 실제 `todo.json` 해시 불변·`task_templates.json` 없음.
- **JMeter 로컬 격리 재실행 1회 (2026-11-16, 승인) — 통과** (순서: 첫 실행 미통과 → 로그아웃 조건식 수정 → 잔여 데이터 안전 정리 → 재실행 통과):
  - 조건: JMeter 5.6.3·Java 21(Temurin), `jmeter/fastapi_test_plan.jmx` 만, THREADS=10·LOOPS=1·RAMP=10. 첫 실행과 같은 저장소 밖 실행기·안전 장치(`pg_harness.py` 절차, `127.0.0.1` 임의 포트 v8 서버·가짜 InfluxDB 수신기, 죽은 프록시 + `-N 127.0.0.1`, `-q` 속성 파일 — Windows ACL 현재 사용자만·실행 뒤 삭제, 명령줄에 자격 증명 없음). 실행 전 R-19 READ ONLY 점검 기준 일치, 두 계획의 조건식 정적 확인(`vars.getIteration()` 각 1건, `__jm__` 0건).
  - 결과: 로그인 **10/10**(200), 로그아웃 **10/10**(204), 측정 **30/30**(`GET /` 200·`POST /todos` 201·`GET /todos` 200 각 10), 인증 **20/20**. `check_results.py --expected 30 --expected-auth 20` → **JMETER_CHECK_OK**. `jmeter.log` 의 `Error running groovy script` 0건·ERROR·FATAL 0건(WARN 도 0줄). 가짜 InfluxDB 수신기: **AUTH 행 0건**, `transaction=all·statut=all` count 합 **30**(= 측정 샘플 — R8-9 지표 분리 실측 확인).
  - 정리: JMeter 세션 10개는 로그아웃으로 모두 지워져 하네스 출처 확인 통과 → 기존 잠금·RESTRICT 정리 → 사용자 데이터 **0행**. 실행 전후 R-19 READ ONLY 점검 **완전히 같음**(PASS 0002·8/5/18/16·0행·다른 세션·잠금·준비된 트랜잭션 0·연결 1). 실제 `todo.json` 해시 불변·`task_templates.json` 없음. JTL·로그·콘솔·수신 기록·HTML 리포트에 비밀번호·아이디·세션 쿠키·`csrf_token` 노출 0건, JTL 에 본문·헤더 열 없음. JMeter·서버·수신기 프로세스 0.
  - 아직 실행으로 확인하지 않은 것: 동시 사용자 실험 계획(같은 조건식, 반복 50회), Jenkins 사본(실제 InfluxDB 쿼리·Linux 호스트), 팀 서버. **실제 Jenkins 검증 대기.**
- **Jenkins 검증 경로 결정 (2026-11-16)**: 사전 조사 결과 A안(feature 브랜치 + v7 팀 서버 대상 JMeter)은 **제외** — JMeter 계획이 빌드마다 팀 서버의 공유 `todo.json` 에 할 일 10건을 만들고 지우지 않으며, v7 데이터 경로에 소유자가 없어 이 빌드가 만든 항목을 입증·복구할 수 없다. B안(main 병합 후 검증)은 **보류** — 5002 작업의 트리거가 저장소에 없어 main 변경 시 v8 자동 배포 가능성을 배제할 수 없다. **C안(Jenkins 빌드 전용 일회용 v8 앱·PostgreSQL 대상) 채택.**
- **C안 설계·사본 수정·정적 검증 (2026-11-16, 승인 — 실행 아님)**: `ci/Jenkinsfile.team3004` 사본만 수정(실제 작업 스크립트 교체·빌드 실행·커밋·push 없음).
  - 체크아웃: `CHECKOUT_BRANCH='feature/v8.0.0'`(허용 목록 `^(main|feature/v8\.0\.0)$`), HEAD = `origin/<CHECKOUT_BRANCH>` 비교, `REQUIRED_COMMIT` 조상 확인·깨끗한 작업 트리 확인 유지, 호스트 `python3` 3.8+ 확인 추가.
  - 단계: Checkout → Verify Checkout → 의존성·pip-audit → Test & Coverage(**변경 없음**, R-19 게이트 그대로) → v8 이미지 로컬 빌드 → 팀 서버 GET 확인(읽기만, `TEAM_APP_VERSION='7.0.0'`) → JMeter 이미지 → **Start Ephemeral v8 Target**(같은 이름 자원이 있으면 멈춤, `--internal` 네트워크, PostgreSQL·앱 tmpfs, 호스트 포트 없음, 비밀번호는 URL 이 아닌 `PGPASSWORD`, `alembic upgrade head` 뒤에만 앱 기동, 내부 프로브로 `/` 200·비로그인 `/todos`·`/auth/me` 401·`Version 8.0.0` 확인) → **Prepare Test Account**(기본 `JM_AUTH_MODE='generated'` 빌드마다 무작위 합성 계정, `credentials` 모드는 기존 `withCredentials` 경로 유지 — 어느 쪽도 일회용 앱에만 가입) → **Isolation Gate**(대상 이름이 이 빌드의 일회용 앱·팀 서버 주소 아님, 라벨=`BUILD_TAG`, 실행 중, 공개 포트·bind 마운트 없음, 네트워크가 일회용 내부망 하나, 내부망 구성원 = 앱·PG 뿐, 앱 `DATABASE_URL` = 일회용 PG, `TODO_FILE`·`TEMPLATE_FILE` = 컨테이너 tmpfs·마운트 0, 자격 증명 700/600·가입 본문 삭제 확인 — 실패하면 JMeter 단계가 돌지 않음) → **Run JMeter (ephemeral v8)**(JMeter 는 일회용 내부망 + InfluxDB 망에만, 죽은 프록시 + `-N "<앱>|influxdb"`, `-q` 속성 파일, 판정은 `check_results` 30/20·라벨별 코드·JTL URL 이 모두 일회용 앱·Groovy/ERROR/FATAL·HTML 리포트) → 파이프라인 `post { always }` 정리.
  - InfluxDB: application = `fastapi-todos-eph-team3004-<빌드번호>`(빌드 고유), 구간 `[RUN_START_NS, RUN_END_NS(+5초)]` 만 조회, 쿼리 명령 실패·`ERR` 응답은 `INFLUX_QUERY_FAIL` 로 실패(0건과 구분), 이벤트 > 0, AUTH 행 0, `all/all` 합계 = 측정 30(필수 판정).
  - 정리: 이 빌드의 정확한 이름이고 라벨이 이 빌드와 같을 때만 `docker rm -f -v`·`docker network rm`, 다르면 `EPH_CLEANUP_SKIP`(지우지 않음), prune·volume rm 없음, 자격 증명·비밀 폴더 삭제 — 성공·실패·중단 모두.
  - 정적 검증: Groovy 3.0.20(JMeter 5.6.3 번들)으로 사본 컴파일 성공, 사본 안 셸 블록 전부 `bash -n` 통과, JMX XML 파싱·Python 문법·실험 스크립트 `bash -n` 통과. `tests/test_jmeter_plan.py` **27 passed**(C안 테스트 8개 추가 — 브랜치·팀 서버 대상 차단·격리 게이트·자원 이름/라벨/포트·정리 범위·계정 모드/비밀값·판정/InfluxDB 범위·셸 문법; 이전 Jenkinsfile 테스트 1개는 같은 확인을 새 구조로 옮김). 변이 확인: JMeter 대상을 팀 서버로·정리의 라벨 검사 제거·앱 포트 공개를 각각 넣으면 테스트가 실패. DB 없는 회귀 **1161 passed, 9 deselected**.
  - **실제 Jenkins 실행은 하지 않았다.** 남은 것: 커밋·feature 브랜치 push(그 전에 5002 작업 트리거 읽기 확인), 실제 작업 스크립트 교체(기존 스크립트 백업), 빌드 1회 실행 — 각각 별도 승인.
- [x] 3-1. (DB 없는 2건 + PostgreSQL `test_http_v8_security_regressions` 통과, 2026-11-15) 보안 보강 테스트: 다른 세션의 CSRF 토큰 재사용 403, 탈퇴·비밀번호 변경 후 쿠키 재사용 401, 내보내기 링크 비로그인 401.
- [x] 3-2. (2026-11-16 — 계획 2개·판정 스크립트·Jenkins 사본 수정, 로컬 격리 JMeter 재실행 통과로 로그인 샘플 분리 실측 확인. 이 항목은 "실제 실행은 별도 승인"이라 Jenkins 실행은 범위 밖 — 3-6 에서 다룬다. 실험 계획은 정적 검증만) JMeter 계획 2개(§42.2)와 `check_results.py`·`ci/Jenkinsfile.team3004` 사본을 로그인 절차로 수정하고, 로그인 샘플 분리를 확인한다(실제 실행은 별도 승인).
- [x] 3-3. (로컬 격리 9/9 통과, 주소 필수화, 2026-11-15) `test_integration.py`에 테스트 계정 로그인을 넣는다(환경변수, 실행은 승인).
- [x] 3-4. (`/` 응답 테스트·B22 통과, 2026-11-15) 화면 버전 `Version 8.0.0`(§41.5), B22·`/` 응답 테스트 갱신.
- [x] 3-5. README(공유 데이터 한계, 계정 기능, 테스트 실행 조건, JMeter 계정 절차), `RELEASE_NOTES.md` 8.0.0 절, `CLAUDE.md` 상태 갱신.
- [ ] 3-6. (진행 중 — DB 없는 1154 passed·9 deselected, PostgreSQL 하네스 98/98, PostgreSQL UI 125/125, 로컬 통합 9/9, 로컬 JMeter 재실행 통과 각각. 실제 Jenkins 미실행이라 완료 체크 보류) 전체 회귀: DB 없는 실행 1회 + PostgreSQL 하네스 1회 + PostgreSQL UI 1회(각각 승인, 결과는 합산하지 않고 따로 적는다).
- [ ] 3-7. (선택) 로컬 Docker 이미지 빌드·실행 확인 — Docker 데몬을 쓸 수 있을 때만.

### 44.4 범위 밖 작업 (각각 별도 승인)
- 커밋(Conventional Commits, Co-Authored-By 트레일러 없음), push, PR·main 병합.
- 5001·5002·3004 등 서버 배포, Jenkins 작업 수정·실행, Credentials 등록, 테스트 계정 생성.
- HTTP 공개 배포에서는 비밀번호와 세션 쿠키가 평문으로 전송된다. HTTPS 게이트(`V7_SPEC.md` §29.6)는 후속 운영 공개 조건으로 남는다(R8-4).

## 45. 위험
| ID | 위험 | 대응 |
|---|---|---|
| R8-1 | 데이터 API가 PostgreSQL 가용성에 의존(§40.3) | 500·503 고정 문구, 문서화. 배포 환경의 DB 헬스체크는 기존 compose 그대로 |
| R8-2 | JMeter 지표가 v7 기준선과 비교 불가(세션 조회 추가) | 보고서에 버전·조건을 함께 적고 직접 비교하지 않는다 |
| R8-3 | 비밀번호 변경·탈퇴 오입력이 로그인 제한 예산을 함께 씀 | 의도된 동작(§38.2). 화면 429 안내 |
| R8-4 | HTTP 배포에서 비밀번호 평문 전송 | v8.0.0 문서·README에 명시. 배포는 별도 승인 |
| R8-5 | CSS 정리로 칸반 드래그·375px 배치 회귀 | 2단계 DoD 2-4에서 다시 확인, 폭 640px 유지 |
| R8-6 | 탈퇴 후 `auth_failures`(HMAC)가 24시간 남아 같은 username 재가입자의 로그인 제한에 영향 | 최대 15분 창이라 영향이 작다. 수용 |
| R8-7 | 공유 데이터: 로그인한 누구나 다른 사용자의 할 일을 수정·삭제·복구 가능 | E-2로 수용, 화면·README 명시. 격리는 후속 |
| R8-8 | `test_ui_auth.py`의 과도기 화면 테스트 의미 변경 | 변경 목록을 2단계 보고에 적는다 |
| R8-9 | `samplersRegex`가 로그인 샘플을 지표에서 빼는지 미확인 | 3단계 실측, 안 되면 계획 구조 변경(§42.2) |
| R8-10 | 1단계와 2단계 사이에는 화면이 데이터 API 인증을 모른다 — 로그아웃 상태로 열면 목록이 401로 비고 로그인 폼이 펼쳐진다(로그인하면 정상) | 중간 상태를 배포하지 않는다. 2단계 게이트로 해소 |
| R8-11 | FastAPI 0.142의 `include_router`는 경로를 지연 포함(`_IncludedRouter`)한다 — `app.routes`에 데이터 경로가 `APIRoute`로 나오지 않는다 | 경로 목록 테스트는 공개 API인 `main.data_router.routes`와 실제 요청(401·403)으로 확인한다 |
