#!/bin/sh
# 백업 폴더의 todo.json·task_templates.json 을 데이터 볼륨으로 옮긴다.
# fastapi-app 이미지 안에서 appuser 권한으로 실행한다 (README "배포와 데이터 관리" 참고):
#   docker compose run --rm --no-deps -v "$PWD/<백업폴더>:/backup:ro" -v "$PWD/scripts:/scripts:ro" \
#     fastapi-app sh /scripts/load-data.sh [--force]
# 대상 경로는 compose 의 TODO_FILE·TEMPLATE_FILE 을 그대로 쓴다 (main.py 와 같은 값).
set -eu

SRC=${BACKUP_DIR:-/backup}
TODO_DST=${TODO_FILE:?TODO_FILE 이 설정되지 않았습니다}
TEMPLATE_DST=${TEMPLATE_FILE:?TEMPLATE_FILE 이 설정되지 않았습니다}
FORCE=${1:-}

# JSON 목록인지 확인하고 항목 수를 출력한다 (깨진 파일이면 실패한다)
check_list() {
  python - "$1" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
if not isinstance(data, list):
    sys.exit(f"{sys.argv[1]}: JSON 목록이 아닙니다")
print(f"{sys.argv[1]}: {len(data)}건")
PY
}

# 임시 파일에 쓴 뒤 이름을 바꿔 반쯤 쓴 파일이 남지 않게 한다
copy_file() {
  cp "$1" "$2.tmp"
  mv "$2.tmp" "$2"
}

# 볼륨에 이미 데이터가 있으면 --force 없이는 덮어쓰지 않는다
if [ -s "$TODO_DST" ] && [ "$FORCE" != "--force" ]; then
  echo "중단: $TODO_DST 에 이미 데이터가 있습니다. 현재 데이터를 백업한 뒤 --force 로 실행하세요." >&2
  exit 1
fi

# 1) 먼저 모두 검사한다 (하나라도 깨졌으면 아무것도 바꾸지 않는다)
HAS_TODO=0
HAS_TEMPLATE=0
if [ -s "$SRC/todo.json" ]; then
  check_list "$SRC/todo.json"
  HAS_TODO=1
else
  echo "백업에 todo.json 이 없거나 비어 있습니다."
fi
if [ -s "$SRC/task_templates.json" ]; then
  check_list "$SRC/task_templates.json"
  HAS_TEMPLATE=1
else
  echo "백업에 task_templates.json 이 없거나 비어 있습니다 → 템플릿은 그대로 둡니다 (처음 저장할 때 만들어진다)."
fi

# 2) 복사
mkdir -p "$(dirname "$TODO_DST")" "$(dirname "$TEMPLATE_DST")"
if [ "$HAS_TODO" = 1 ]; then
  copy_file "$SRC/todo.json" "$TODO_DST"
elif [ ! -s "$TODO_DST" ]; then
  printf '[]' > "$TODO_DST"                 # 빈 목록으로 시작
  echo "$TODO_DST 를 빈 목록 [] 로 만들었습니다."
fi
if [ "$HAS_TEMPLATE" = 1 ]; then
  copy_file "$SRC/task_templates.json" "$TEMPLATE_DST"
fi

# 3) 결과 확인
check_list "$TODO_DST"
if [ -f "$TEMPLATE_DST" ]; then check_list "$TEMPLATE_DST"; fi
ls -ln "$(dirname "$TODO_DST")"
