# 읽기 전용 동시성 실험 (팀 서버 3004) — 실행 절차서

동시 사용자 수(1·3·5명)에 따라 **응답 시간·처리량·오류율**이 어떻게 변하는지 JMeter로 측정하고, InfluxDB/Grafana로 비교한다.
Jenkins 작업과 무관하게 개인 서버에서 수동으로 실행한다.

## 파일
| 파일 | 역할 |
|---|---|
| `concurrency_readonly.jmx` | JMeter 계획 — `GET /`, `GET /todos`만 보낸다(POST 없음). 요청 전 400~600ms 대기(Uniform Random Timer). Backend Listener 설정은 `../fastapi_test_plan.jmx`와 같다 |
| `analyze_jtl.py` | 결과(JTL) 분석·판정 — 요청 수, 성공/실패, 평균, p95, 실제 처리량, 라벨별 평균. 요약 CSV·Markdown 표 |
| `run_concurrency_experiment.sh` | 사전 점검 → u1 → u3 → u5 실행. 단계마다 판정이 통과해야 다음 단계로 간다 |
| `../check_results.py` | 기존 판정 스크립트(샘플 수·실패율)를 그대로 재사용한다 |

## 실험 조건 (승인안)
| 실험 | 동시 사용자 | 사용자당 반복 | 요청 수 | 램프업 | 대기(think time) |
|---|---|---|---|---|---|
| u1 | 1 | 50 | 100 | 5초 | 요청마다 400~600ms |
| u3 | 3 | 50 | 300 | 5초 | 요청마다 400~600ms |
| u5 | 5 | 50 | 500 | 5초 | 요청마다 400~600ms |

- 반복 1회 = `GET /` + `GET /todos` 2요청. 실험 사이 60초 대기. 실험당 약 1분, 전체 약 6분, **총 900요청**.
- 최대 부하는 약 10요청/초다(5명 × 요청당 약 0.5초 대기).
- **읽기 전용**: 팀 서버 할 일 데이터는 바뀌지 않는다. 스크립트가 실험 전후 `GET /todos` 개수를 비교해 보고한다.
- **중단 조건**: 어느 단계든 판정이 실패하면 다음 단계로 가지 않는다. u1은 오류가 1건이라도 있거나 평균 응답 시간이 1,000ms를 넘으면 중단한다.

## 실험별 태그
| 항목 | 값 | 쓰는 곳 |
|---|---|---|
| `APP_NAME` → `application` 태그 | `team3004-exp-<STAMP>-u1` / `-u3` / `-u5` | InfluxDB `jmeter` 측정값을 실험별로 나눈다 |
| `RUN_ID` | `exp-<STAMP>-u1` … | InfluxDB `events`(시작·종료) — `tags = 'build=<RUN_ID>'` |
| 컨테이너 이름 | `jm-exp-<STAMP>-u1` … | 일회성(`--rm`). Ctrl+C 때 이 이름만 멈춘다 |

`<STAMP>`는 실행 시각(`YYYYMMDD-HHMMSS`)이다. 결과 폴더는 `~/jmeter-exp/<STAMP>/`(저장소 밖)이다.

## 실행 순서 (개인 서버 163.239.77.92, docker 그룹 계정)
1. **파일 복사** (이 PC에서): 저장소의 `jmeter` 폴더를 개인 서버로 옮긴다.
   ```bash
   scp -r jmeter <계정>@163.239.77.92:~/fastapi-exp/
   ```
2. **실행 금지 시간 확인**: 실험하는 동안 Jenkins 빌드를 돌리지 않는다(빌드마다 `POST /todos` 10건이 생겨 `GET /todos` 조건이 바뀐다).
3. **사전 점검만** (읽기 전용, 부하 없음):
   ```bash
   cd ~/fastapi-exp/jmeter/experiments
   bash run_concurrency_experiment.sh --check-only
   ```
   `PRECHECK_OK`가 나와야 한다. 점검 항목:
   - 계획 파일에 POST가 없고 샘플러가 2개인지
   - JMeter 이미지, InfluxDB 컨테이너, `fastapi_jmeter_net`이 있는지
   - JMeter 컨테이너에서 팀 서버(200)와 InfluxDB(204)에 닿는지
   - 실험 전 할 일 개수
4. **실험 실행** (승인 후):
   ```bash
   bash run_concurrency_experiment.sh; echo "exit=$?"
   ```
   - 스크립트가 콘솔 출력을 `~/jmeter-exp/console-<STAMP>.log`에도 남기므로 `| tee`를 붙이지 않는다.
   - 파이프하면 종료 코드가 `tee`의 것으로 바뀐다. 꼭 파이프해야 한다면 `set -o pipefail`을 먼저 실행하고 `${PIPESTATUS[0]}`로 확인한다.
   - 종료 코드: 0 = 3단계 모두 통과, 1 = 판정 실패로 중단, 130 = Ctrl+C로 중단
5. **중단이 필요하면** Ctrl+C를 누른다.
   - 지금 실행 중인 `jm-exp-<STAMP>-u<N>` 컨테이너만 `docker stop`으로 멈춘다.
   - 그때까지의 요약과 Grafana 시간 범위를 출력한다.
   - 다른 컨테이너·네트워크·볼륨은 건드리지 않는다.

## 단계마다 하는 판정 (하나라도 실패하면 중단)
1. JMeter 종료 코드가 0
2. `jmeter.log`에 ERROR/FATAL이 없음
3. `check_results.py` — 샘플 수가 기대값(100/300/500)과 같고 실패율이 0%
4. `analyze_jtl.py check`
   - 라벨이 `GET /`, `GET /todos` 두 개뿐이고 각각 절반씩
   - 응답 코드가 모두 200, 실패 0건
   - u1은 평균 1,000ms 이하
5. HTML 리포트(`report/index.html`)가 생성됨
6. InfluxDB `events`에 이번 실행(`build=<RUN_ID>`)의 기록이 있음 — Backend Listener 주소 `http://influxdb:8086/write?db=jmeter`가 기존 방식과 똑같이 동작하는지 확인한다

**참고 출력 (판정에는 쓰지 않음)**: `sum(count)[transaction=all,statut=all]`과 JTL 샘플 수의 일치 여부. 같으면 Grafana 집계에 중복이 없다는 뜻이다.

## 결과 확인
- **콘솔**: 단계별 `RESULT[uN] …`, `ANALYZE_OK[uN]`, `INFLUX: …`, 마지막에 요약 표와 `Grafana 시간 범위`
- **`~/jmeter-exp/<STAMP>/`**
  - `summary.csv`, `summary.md`: 과제에 붙일 표
  - `run-info.txt`: 실험 조건, 시각, 계획 파일 해시
  - `uN/results.jtl`, `uN/jmeter.log`, `uN/report/index.html`
- **HTML 리포트를 PC로 가져오기**:
  ```bash
  scp -r <계정>@163.239.77.92:~/jmeter-exp/<STAMP> .
  ```
  `uN/report/index.html`을 브라우저에서 연다.

## Grafana (기존 대시보드는 수정하지 않는다 — 새 대시보드를 만든다)
데이터 소스는 기존 InfluxDB(`jmeter` DB)를 쓴다. 집계는 **`transaction='all' AND statut='all'` 행만** 쓴다. `ok`/`ko`/`all`이나 오류 행을 함께 더하면 같은 요청이 여러 번 세어진다.

```sql
-- 평균 응답 시간(ms)
SELECT mean("avg") FROM "jmeter" WHERE "application" =~ /^team3004-exp-<STAMP>-u[135]$/
  AND "transaction" = 'all' AND "statut" = 'all' AND $timeFilter GROUP BY time(5s), "application" fill(null)
-- 처리량(요청/초) — 리스너가 5초마다 그 구간의 건수를 보낸다
SELECT sum("count") / 5 FROM "jmeter" WHERE "application" =~ /^team3004-exp-<STAMP>-u[135]$/
  AND "transaction" = 'all' AND "statut" = 'all' AND $timeFilter GROUP BY time(5s), "application" fill(0)
-- 오류율(%) — 실험별 합계
SELECT sum("countError") / sum("count") * 100 FROM "jmeter" WHERE "application" =~ /^team3004-exp-<STAMP>-u[135]$/
  AND "transaction" = 'all' AND "statut" = 'all' AND $timeFilter GROUP BY "application"
-- 실제 동시 사용자(활성 스레드)
SELECT max("maxAT") FROM "jmeter" WHERE "application" =~ /^team3004-exp-<STAMP>-u[135]$/
  AND "transaction" = 'internal' AND $timeFilter GROUP BY time(5s), "application"
-- 주석(시작·종료)
SELECT "text" FROM "events" WHERE "application" =~ /^team3004-exp-<STAMP>-/ AND $timeFilter
```
- 시간 범위는 스크립트가 마지막에 출력한 `Grafana 시간 범위`를 쓴다.
- 처음 쓰기 전에 필드·태그 이름을 확인한다(읽기 전용):
  ```bash
  docker exec fastapi-jmeter-influxdb influx -database jmeter -execute 'SHOW FIELD KEYS FROM "jmeter"'
  ```
- `mean("avg")`는 5초 구간 평균들의 평균이다(요청 수로 가중되지 않음). **보고서의 수치는 `summary.md`(JTL 기준)를 쓰고**, Grafana는 시간에 따른 흐름을 보여 주는 데 쓴다.

## 과제 3번 기록 틀
1. **목적**: 동시 사용자 수(1/3/5)에 따라 평균 응답 시간, 처리량, 오류율이 어떻게 변하는지 확인
2. **환경**
   - 대상: 팀 서버 `163.239.77.78:3004`(FastAPI v7)
   - 부하 발생: 개인 서버의 JMeter 5.6.3 Docker 컨테이너
   - 수집: InfluxDB 1.8(Backend Listener, 5초 간격) → Grafana
3. **방법**
   - 읽기 요청만 사용(`GET /`, `GET /todos`)
   - 사용자당 50회 반복, 요청 전 400~600ms 대기, 램프업 5초, 실험 사이 60초
   - 실험별 `application` 태그로 구분
4. **지표**
   - 평균 응답 시간(ms) = elapsed 평균
   - p95
   - 처리량 = 요청 수 ÷ 실제 경과 시간
   - 오류율 = 실패 ÷ 전체
5. **결과**: `summary.md` 표, Grafana 그래프 캡처(응답 시간·처리량·오류율·활성 스레드), JMeter HTML 리포트
6. **해석**
   - 처리량이 사용자 수에 거의 비례해 늘면 아직 여유가 있다는 뜻이다.
   - 평균·p95가 늘기 시작하는 지점이 병목의 신호다.
   - 이번 규모(최대 약 10요청/초)에서는 차이가 작을 수 있다. 그것도 결과다.
7. **한계**
   - 쓰기(POST)는 앱의 동시 쓰기 한계(파일 저장에 잠금 없음) 때문에 제외했다.
   - 팀 서버는 공유 서버라 외부 요인이 섞일 수 있다.
   - 실험별 1회만 실행했다.
