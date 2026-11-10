#!/usr/bin/env bash
# 읽기 전용 동시성 실험 (u1 → u3 → u5) — 개인 서버(163.239.77.92)에서 docker 그룹 계정으로 실행한다. Jenkins 와 무관.
#
#   bash run_concurrency_experiment.sh --check-only   # 사전 점검만 (읽기 전용, 부하 없음)
#   bash run_concurrency_experiment.sh                # 실험 실행 (약 6분)
#
# 하는 일: 실험마다 일회성 JMeter 컨테이너(--rm, 이름 jm-exp-<STAMP>-u<N>)를 기존 네트워크 fastapi_jmeter_net 에 붙여
#   팀 서버에 GET / , GET /todos 만 보낸다(POST 없음 → 팀 서버 데이터 변경 없음). 결과는 InfluxDB(jmeter DB)에 '추가'만 된다.
# 바꾸지 않는 것: Jenkins 작업, 팀 서버 앱·DB, 기존 컨테이너·네트워크·볼륨, 기존 InfluxDB 측정값, Grafana 대시보드.
# 중단: Ctrl+C — 지금 돌고 있는 이 실험의 컨테이너(이름 지정)만 멈추고, 그때까지의 결과를 정리한다.
set -Eeuo pipefail

# ---------- 승인된 실험 조건 (바꾸려면 다시 승인) ----------
TEAM_HOST="163.239.77.78"
TEAM_PORT="3004"
LEVELS=(1 3 5)            # 동시 사용자
LOOPS=50                  # 사용자당 반복 (반복마다 GET / , GET /todos 2요청)
RAMP=5                    # 램프업(초)
THINK_MIN_MS=400          # 요청 전 대기 400 + [0,200) ms
THINK_RANGE_MS=200
GAP=60                    # 실험 사이 대기(초)
U1_MAX_AVG_MS=1000        # u1 평균 응답 시간 한도 — 넘으면 중단

# ---------- 기존 개인 서버 리소스 (읽기·접속만) ----------
JMETER_IMAGE="fastapi-jmeter-runner:5.6.3"
JMETER_NET="fastapi_jmeter_net"
INFLUX_CONTAINER="fastapi-jmeter-influxdb"
INFLUX_URL="http://influxdb:8086/write?db=jmeter"   # 기존 Jenkins·수동 실행과 같은 주소(네트워크 안의 이름)

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLAN="$HERE/concurrency_readonly.jmx"
ANALYZE="$HERE/analyze_jtl.py"
CHECK="$HERE/../check_results.py"                  # 기존 판정 스크립트 재사용 (샘플 수·실패율)
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${OUT_BASE:-$HOME/jmeter-exp}/$STAMP"
CHECK_ONLY=0
[ "${1:-}" = "--check-only" ] && CHECK_ONLY=1

# 콘솔 출력을 로그 파일에도 남긴다 — 바깥에서 `| tee` 로 파이프하지 않으므로 이 스크립트의 종료 코드가 그대로 보존된다.
# 로그용 tee 는 INT·TERM 을 무시한다: Ctrl+C 때 tee 가 먼저 죽으면 정리(docker stop·요약) 중 출력이 SIGPIPE 로 끊길 수 있다
LOG_DIR="${OUT_BASE:-$HOME/jmeter-exp}"
mkdir -p "$LOG_DIR"
if [ "$CHECK_ONLY" -eq 1 ]; then LOG="$LOG_DIR/console-${STAMP}-check.log"; else LOG="$LOG_DIR/console-${STAMP}.log"; fi
exec > >(trap '' INT TERM; exec tee -a "$LOG") 2>&1
echo "로그: $LOG"

CURRENT=""          # 지금 실행 중인 JMeter 컨테이너 이름 (Ctrl+C 때 이 이름만 멈춘다)
FIRST_START=""
TODOS_BEFORE=""

ts()  { date '+%Y-%m-%d %H:%M:%S %Z'; }
say() { echo "[$(date +%H:%M:%S)] $*"; }
JR() { docker run --rm --network "$JMETER_NET" -u "$(id -u):$(id -g)" -e HOME=/tmp "$@"; }
influx_q() { docker exec "$INFLUX_CONTAINER" influx -database jmeter -format csv -execute "$1" 2>/dev/null | tail -n +2 | cut -d, -f3; }
todo_count() { curl -s --max-time 10 "http://${TEAM_HOST}:${TEAM_PORT}/todos" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))' 2>/dev/null || echo "?"; }

finish() {
  local code="$1"
  set +e
  trap - INT TERM
  if [ "$CHECK_ONLY" -eq 0 ] && [ -d "$OUT" ]; then
    echo
    say "== 요약 =="
    [ -f "$OUT/summary.csv" ] && python3 "$ANALYZE" table --csv "$OUT/summary.csv" --md "$OUT/summary.md"
    local after; after="$(todo_count)"
    if [ "$after" = "$TODOS_BEFORE" ]; then
      say "TEAM_TODOS: 실험 전 ${TODOS_BEFORE}개 = 실험 후 ${after}개 (팀 서버 할 일 데이터 변화 없음)"
    else
      say "TEAM_TODOS_WARN: 실험 전 ${TODOS_BEFORE} / 실험 후 ${after} — 이 실험은 POST 를 보내지 않는다. 다른 요청(Jenkins 빌드·사용자)이 있었는지 확인"
    fi
    {
      echo "grafana_from_local=${FIRST_START}"
      echo "grafana_to_local=$(ts)"
      echo "exit_code=${code}"
    } >> "$OUT/run-info.txt"
    say "Grafana 시간 범위: ${FIRST_START} ~ $(ts)  (application =~ /^team3004-exp-${STAMP}-u[135]\$/)"
    say "결과 폴더: $OUT"
  fi
  exit "$code"
}
stop() { say "STOP: $*"; finish 1; }

on_interrupt() {
  trap - INT TERM
  echo
  say "INTERRUPTED: 사용자 중단(Ctrl+C)"
  if [ -n "$CURRENT" ]; then
    say "  실행 중인 컨테이너 ${CURRENT} 만 멈춘다"
    docker stop -t 10 "$CURRENT" >/dev/null 2>&1 || true
  fi
  finish 130
}
trap on_interrupt INT TERM

# ---------- 1. 사전 점검 (읽기 전용) ----------
say "== 사전 점검 =="
for t in docker python3 curl sha256sum; do command -v "$t" >/dev/null || stop "필요한 명령 없음: $t"; done
for f in "$PLAN" "$ANALYZE" "$CHECK"; do [ -f "$f" ] || stop "파일 없음: $f"; done
grep -q 'HTTPSampler.method">POST' "$PLAN" && stop "계획 파일에 POST 샘플러가 있음 — 읽기 전용 계획이 아님"
[ "$(grep -c '<HTTPSamplerProxy ' "$PLAN")" -eq 2 ] || stop "계획 파일의 샘플러가 2개(GET /, GET /todos)가 아님"
docker image inspect "$JMETER_IMAGE" >/dev/null 2>&1 || stop "JMeter 이미지 없음: $JMETER_IMAGE"
[ "$(docker inspect -f '{{.State.Running}}' "$INFLUX_CONTAINER" 2>/dev/null)" = true ] || stop "$INFLUX_CONTAINER 가 실행 중이 아님"
nets="$(docker network inspect "$JMETER_NET" --format '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null)" || stop "네트워크 없음: $JMETER_NET"
[[ " $nets " == *" $INFLUX_CONTAINER "* ]] || stop "$JMETER_NET 에 $INFLUX_CONTAINER 가 없음"
t_code="$(JR "$JMETER_IMAGE" curl -s -o /dev/null --max-time 10 -w '%{http_code}' "http://${TEAM_HOST}:${TEAM_PORT}/" || true)"
i_code="$(JR "$JMETER_IMAGE" curl -s -o /dev/null --max-time 10 -w '%{http_code}' "http://influxdb:8086/ping" || true)"
say "PRECHECK: JMeter 컨테이너 → 팀 서버 ${TEAM_HOST}:${TEAM_PORT} ${t_code}, InfluxDB ${i_code}"
[ "$t_code" = 200 ] || stop "JMeter 컨테이너에서 팀 서버에 접속할 수 없음"
[ "$i_code" = 204 ] || stop "JMeter 컨테이너에서 InfluxDB(${INFLUX_URL})에 접속할 수 없음"
TODOS_BEFORE="$(todo_count)"
say "PRECHECK: 팀 서버 할 일 개수(실험 전) ${TODOS_BEFORE}"
say "PRECHECK: 계획 $(sha256sum "$PLAN" | cut -c1-16)…  분석 $(sha256sum "$ANALYZE" | cut -c1-16)…  판정 $(sha256sum "$CHECK" | cut -c1-16)…"
say "PRECHECK_OK — 예정: u1 100 + u3 300 + u5 500 = 900 요청 (읽기 전용), 실험 사이 ${GAP}초"
if [ "$CHECK_ONLY" -eq 1 ]; then
  say "--check-only: 실험은 실행하지 않는다"
  exit 0
fi

# ---------- 2. 실험 u1 → u3 → u5 ----------
[ -e "$OUT" ] && stop "결과 폴더가 이미 있음: $OUT"
mkdir -p "$OUT"
{
  echo "stamp=${STAMP}"; echo "target=${TEAM_HOST}:${TEAM_PORT}"; echo "image=${JMETER_IMAGE}"
  echo "levels=${LEVELS[*]} loops=${LOOPS} ramp=${RAMP} think=${THINK_MIN_MS}+${THINK_RANGE_MS}ms gap=${GAP}s"
  echo "plan_sha256=$(sha256sum "$PLAN" | cut -d' ' -f1)"; echo "todos_before=${TODOS_BEFORE}"
} > "$OUT/run-info.txt"
FIRST_START="$(ts)"

n=0
for U in "${LEVELS[@]}"; do
  n=$((n + 1))
  L="u${U}"; EXP=$((U * LOOPS * 2)); APP="team3004-exp-${STAMP}-${L}"; RUN="exp-${STAMP}-${L}"
  NAME="jm-exp-${STAMP}-${L}"; D="$OUT/$L"
  mkdir -p "$D"
  say "== 실험 ${L}: 동시 사용자 ${U}, 요청 ${EXP}개, application=${APP} =="
  echo "${L}_start=$(ts)" >> "$OUT/run-info.txt"
  CURRENT="$NAME"
  set +e
  docker run --rm --name "$NAME" --network "$JMETER_NET" -u "$(id -u):$(id -g)" -e HOME=/tmp \
    -v "$HERE:/plan:ro" -v "$D:/out" -w /out "$JMETER_IMAGE" \
    jmeter -n -t /plan/concurrency_readonly.jmx -l /out/results.jtl -j /out/jmeter.log -e -o /out/report \
      -JTARGET_HOST="$TEAM_HOST" -JTARGET_PORT="$TEAM_PORT" \
      -JTHREADS="$U" -JLOOPS="$LOOPS" -JRAMP="$RAMP" -JTHINK_MIN_MS="$THINK_MIN_MS" -JTHINK_RANGE_MS="$THINK_RANGE_MS" \
      -JAPP_NAME="$APP" -JRUN_ID="$RUN" "-JINFLUX_URL=${INFLUX_URL}" \
      -Jjmeter.save.saveservice.output_format=csv
  rc=$?
  set -e
  CURRENT=""
  echo "${L}_end=$(ts)" >> "$OUT/run-info.txt"
  [ "$rc" -eq 0 ] || stop "${L}: JMeter 종료 코드 ${rc}"
  if grep -E " (ERROR|FATAL) " "$D/jmeter.log" > "$D/jmeter-errors.txt" 2>/dev/null; then
    stop "${L}: jmeter.log 에 ERROR/FATAL 기록 ($D/jmeter-errors.txt)"
  fi
  # 판정 ① 기존 check_results.py (샘플 수·실패율 0%)
  python3 "$CHECK" "$D/results.jtl" --expected "$EXP" --max-error-rate 0 || stop "${L}: check_results.py 판정 실패"
  # 판정 ② 분석 (라벨·응답 코드·평균 한도) + 요약 CSV 기록
  avg_arg=()
  [ "$U" -eq 1 ] && avg_arg=(--max-avg-ms "$U1_MAX_AVG_MS")
  python3 "$ANALYZE" check --label "$L" --users "$U" --jtl "$D/results.jtl" --expected "$EXP" \
    "${avg_arg[@]}" --csv "$OUT/summary.csv" || stop "${L}: 분석 판정 실패 (u1 은 오류 또는 평균 ${U1_MAX_AVG_MS}ms 초과 시 중단)"
  [ -f "$D/report/index.html" ] || stop "${L}: HTML 리포트 없음"
  # 판정 ③ InfluxDB 기록 (Backend Listener URL 호환성) — 이번 실행의 시작·종료 이벤트가 있어야 한다
  ev="$(influx_q "SELECT count(\"text\") FROM \"events\" WHERE \"application\"='${APP}' AND \"tags\" = 'build=${RUN}'")"
  [ "${ev:-0}" -gt 0 ] 2>/dev/null || stop "${L}: InfluxDB 에 이번 실행(build=${RUN}) 이벤트 없음 — Backend Listener 전송 확인"
  # 참고: 중복 계산 점검 — transaction='all', statut='all' 행의 count 합이 JTL 샘플 수와 같은지 (판정에는 쓰지 않음)
  sum="$(influx_q "SELECT sum(\"count\") FROM \"jmeter\" WHERE \"application\"='${APP}' AND \"transaction\"='all' AND \"statut\"='all'")"
  if [ "${sum:-}" = "$EXP" ]; then m=yes; else m="no(확인 필요)"; fi
  say "INFLUX: events ${ev}건, sum(count)[transaction=all,statut=all]=${sum:-없음} / JTL ${EXP} → 일치 ${m}"
  say "${L} OK"
  if [ "$n" -lt "${#LEVELS[@]}" ]; then
    say "다음 실험까지 ${GAP}초 대기 (Ctrl+C 로 중단 가능)"
    sleep "$GAP"
  fi
done

say "ALL_OK: u1·u3·u5 완료"
finish 0
