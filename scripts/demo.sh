#!/usr/bin/env bash
# Business World Model 데모 환경 한 번에 띄우기 / 내리기 / 상태 보기
#
#   ./scripts/demo.sh up       그래프 DB → API → 화면 순서로 띄우고 건강검진까지 한다
#   ./scripts/demo.sh down     내가 띄운 것만 내린다 (그래프 DB 는 데이터 보존을 위해 그대로 둔다)
#   ./scripts/demo.sh status    지금 무엇이 떠 있고 데이터가 얼마나 있는지 본다
#   ./scripts/demo.sh ask "질문"  화면 없이 명령줄에서 질문 하나만 던져 본다
#   ./scripts/demo.sh logs      API 로그를 따라간다
#
# 이 스크립트는 이미 떠 있는 것을 다시 띄우지 않는다(포트가 밀려 중복 서버가 쌓이는 것을 막는다).

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

CONTAINER="bwm-neo4j"
API_PORT="${BWM_API_PORT:-8099}"
WEB_PORT="${BWM_WEB_PORT:-5173}"
PY="$REPO/.venv/bin/python"
RUN_DIR="$REPO/data/run"
API_LOG="$RUN_DIR/api.log"
WEB_LOG="$RUN_DIR/web.log"

mkdir -p "$RUN_DIR"

# ── 화면 출력 도우미 ───────────────────────────────────────────────
if [ -t 1 ]; then
  B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; D=$'\033[2m'; N=$'\033[0m'
else
  B=''; G=''; Y=''; R=''; D=''; N=''
fi
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$1"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$1"; }
bad()  { printf '  %s✗%s %s\n' "$R" "$N" "$1"; }
step() { printf '\n%s%s%s\n' "$B" "$1" "$N"; }
dim()  { printf '    %s%s%s\n' "$D" "$1" "$N"; }

port_pid() { lsof -ti "tcp:$1" -sTCP:LISTEN 2>/dev/null | head -1; }

# ── 준비물 확인 ────────────────────────────────────────────────────
check_prereqs() {
  local missing=0
  command -v docker >/dev/null 2>&1 || { bad "docker 가 없다. Docker Desktop 을 켜라"; missing=1; }
  [ -x "$PY" ] || { bad "$PY 가 없다. python3.12 -m venv .venv 로 만들어라"; missing=1; }
  command -v npm >/dev/null 2>&1 || { warn "npm 이 없다. 화면 없이 API 만 띄운다"; }
  [ "$missing" -eq 0 ]
}

# ── 1. 그래프 DB ───────────────────────────────────────────────────
up_graph() {
  step "1/3  그래프 DB (Neo4j)"
  local state
  state="$(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null)"
  case "$state" in
    running) ok "이미 떠 있다 ($CONTAINER)" ;;
    exited|created|paused)
      dim "멈춰 있어서 다시 켠다. 데이터는 컨테이너 안에 그대로 있다"
      docker start "$CONTAINER" >/dev/null && ok "켰다 ($CONTAINER)" || { bad "docker start 실패"; return 1; } ;;
    "")
      bad "컨테이너 $CONTAINER 가 없다"
      dim "처음 만들 때는 이렇게 한다 (데이터가 이 컨테이너 안에 쌓인다):"
      dim "docker run -d --name $CONTAINER -p 7474:7474 -p 7687:7687 \\"
      dim "  -e NEO4J_AUTH=neo4j/bwmdemo123 -e NEO4J_PLUGINS='[\"apoc\"]' neo4j:5.26-community"
      dim "만든 뒤 스키마를 깔고 자료를 넣는다:  $PY -m graph.ddl apply  &&  $PY scripts/ingest.py run"
      return 1 ;;
    *) warn "상태를 모르겠다: $state" ;;
  esac

  printf '    연결을 기다린다'
  for _ in $(seq 1 30); do
    if "$PY" - <<'PY' >/dev/null 2>&1
from graph.connection import ReadOnlyGraph
with ReadOnlyGraph() as g: g.verify_connectivity()
PY
    then printf '\n'; ok "연결됨 (bolt://localhost:7687)"; return 0; fi
    printf '.'; sleep 1
  done
  printf '\n'; bad "30초 안에 연결되지 않았다. docker logs $CONTAINER 를 보라"; return 1
}

# ── 2. 답변 API ────────────────────────────────────────────────────
up_api() {
  step "2/3  답변 API"
  local pid; pid="$(port_pid "$API_PORT")"
  if [ -n "$pid" ]; then
    if curl -fsS -m 5 "http://localhost:$API_PORT/health" >/dev/null 2>&1; then
      ok "이미 떠 있다 (포트 $API_PORT · pid $pid)"; return 0
    fi
    warn "포트 $API_PORT 를 pid $pid 가 쓰는데 건강검진에 답하지 않는다. 그대로 두고 넘어간다"
    return 1
  fi
  dim "로그: $API_LOG"
  # 이 스크립트가 죽어도 서버는 살아 있어야 한다. 괄호 안에서 nohup 으로 한 번 더 갈라
  # 프로세스 그룹을 벗어난다(스크립트를 백그라운드로 돌리다 취소했을 때 서버까지 같이
  # 끌려가 내려간 적이 있다).
  ( cd "$REPO" && nohup "$PY" -m uvicorn api.main:app --host 127.0.0.1 --port "$API_PORT" \
      >"$API_LOG" 2>&1 & echo $! >"$RUN_DIR/api.pid" ) &
  wait $! 2>/dev/null || true
  printf '    기동을 기다린다'
  for _ in $(seq 1 40); do
    if curl -fsS -m 3 "http://localhost:$API_PORT/health" >/dev/null 2>&1; then
      printf '\n'; ok "떴다 (http://localhost:$API_PORT)"; return 0
    fi
    printf '.'; sleep 1
  done
  printf '\n'; bad "40초 안에 안 떴다. 로그를 보라: tail -40 $API_LOG"; return 1
}

# ── 3. 화면 ────────────────────────────────────────────────────────
up_web() {
  step "3/3  화면 (Vite dev server)"
  command -v npm >/dev/null 2>&1 || { warn "npm 이 없어 건너뛴다"; return 0; }
  local pid; pid="$(port_pid "$WEB_PORT")"
  if [ -n "$pid" ]; then ok "이미 떠 있다 (포트 $WEB_PORT · pid $pid)"; return 0; fi
  [ -d web/node_modules ] || { dim "의존성을 처음 받는다 (몇 분 걸린다)"; ( cd web && npm install >"$RUN_DIR/npm-install.log" 2>&1 ); }
  dim "로그: $WEB_LOG"
  ( cd "$REPO/web" && VITE_API_TARGET="http://localhost:$API_PORT" \
      nohup npm run dev -- --port "$WEB_PORT" --strictPort >"$WEB_LOG" 2>&1 & echo $! >"$RUN_DIR/web.pid" )
  printf '    기동을 기다린다'
  for _ in $(seq 1 40); do
    if curl -fsS -m 3 "http://localhost:$WEB_PORT/" >/dev/null 2>&1; then
      printf '\n'; ok "떴다 (http://localhost:$WEB_PORT)"; return 0
    fi
    printf '.'; sleep 1
  done
  printf '\n'; bad "40초 안에 안 떴다. 로그를 보라: tail -40 $WEB_LOG"; return 1
}

# ── 상태 ───────────────────────────────────────────────────────────
show_status() {
  step "상태"
  local state; state="$(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo 없음)"
  printf '  그래프 DB   %s\n' "$state"
  if curl -fsS -m 5 -o "$RUN_DIR/health.json" "http://localhost:$API_PORT/health" 2>/dev/null; then
    "$PY" scripts/_show.py health "$RUN_DIR/health.json"
  else
    printf '  답변 API    떠 있지 않다 (포트 %s)\n' "$API_PORT"
  fi
  local wp; wp="$(port_pid "$WEB_PORT")"
  [ -n "$wp" ] && printf '  화면        http://localhost:%s (pid %s)\n' "$WEB_PORT" "$wp" \
               || printf '  화면        떠 있지 않다 (포트 %s)\n' "$WEB_PORT"

  if [ "$state" = running ]; then
    "$PY" - <<'PY' 2>/dev/null
from graph.connection import ReadOnlyGraph
Q = [("Source","(:Source)"),("Evidence","(:Evidence)"),("Observation","(:Observation)"),
     ("Claim","(:Claim)"),("BusinessDomain","(:BusinessDomain)"),("Account","(:Account)"),
     ("Feature","(:Feature)"),("Need","(:Need)"),("Capability","(:Capability)")]
with ReadOnlyGraph() as g, g.session() as s:
    print("\n  \033[1m자료\033[0m")
    for name, pat in Q:
        c = s.run(f"MATCH {pat} RETURN count(*) AS c").single()["c"]
        print(f"    {name:<16}{c:>8,}")
PY
  fi
}

# ── 명령줄에서 질문 하나 ───────────────────────────────────────────
ask_once() {
  local q="${1:-}"
  [ -n "$q" ] || { bad '질문을 적어라:  ./scripts/demo.sh ask "우리 BD 는 무엇이 있나?"'; return 1; }
  local out="$RUN_DIR/last-answer.json"
  dim "묻는 중… 전략 질문은 1~2분 걸린다"
  if curl -fsS -m 900 -o "$out" -X POST "http://localhost:$API_PORT/ask" \
      -H 'Content-Type: application/json' \
      --data "$("$PY" -c 'import json,sys; print(json.dumps({"question": sys.argv[1], "account": "demo"}))' "$q")"
  then
    "$PY" scripts/_show.py answer "$out"
    dim "응답 원본: $out"
  else
    bad "질문이 실패했다"
    dim "서버가 떠 있는지: ./scripts/demo.sh status"
    dim "모델 호출 상한에 걸렸다면 BWM_LLM_BUDGET_USD 를 올려서 API 를 다시 띄운다"
    return 1
  fi
}

# ── 내리기 ─────────────────────────────────────────────────────────
do_down() {
  step "내린다 (그래프 DB 는 데이터 보존을 위해 그대로 둔다)"
  for name in api web; do
    local f="$RUN_DIR/$name.pid"
    if [ -f "$f" ]; then
      local pid; pid="$(cat "$f")"
      if kill "$pid" 2>/dev/null; then ok "$name 종료 (pid $pid)"; else dim "$name pid $pid 는 이미 없다"; fi
      rm -f "$f"
    fi
  done
  local p; p="$(port_pid "$API_PORT")"; [ -n "$p" ] && { kill "$p" 2>/dev/null && ok "포트 $API_PORT 정리 (pid $p)"; }
  p="$(port_pid "$WEB_PORT")";  [ -n "$p" ] && { kill "$p" 2>/dev/null && ok "포트 $WEB_PORT 정리 (pid $p)"; }
  dim "그래프 DB 도 멈추려면:  docker stop $CONTAINER"
  dim "⚠ docker rm 은 하지 마라. 자료 30,000여 노드가 그 컨테이너 안에 있다"
}

# ── 진입점 ─────────────────────────────────────────────────────────
case "${1:-up}" in
  up)
    check_prereqs || exit 1
    up_graph || exit 1
    up_api   || exit 1
    up_web
    show_status
    cat <<EOF

${B}다 됐다${N}

  화면      http://localhost:$WEB_PORT
  API 문서  http://localhost:$API_PORT/docs
  그래프 DB http://localhost:7474   (neo4j / bwmdemo123)

  이렇게 물어 보라
    "우리가 개척하려는 Business Domain 들은 무엇이고 각각 어느 단계인가?"
    "여러 Business Domain 에서 공통적으로 등장하는 Need 는 무엇인가?"
    "가나손해보험 딜은 지금 어느 단계인가?"

  화면 없이 명령줄로:  ./scripts/demo.sh ask "질문"
  내릴 때:            ./scripts/demo.sh down
EOF
    ;;
  down)   do_down ;;
  status) show_status ;;
  ask)    shift; ask_once "${1:-}" ;;
  logs)   tail -f "$API_LOG" ;;
  *) sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
esac
