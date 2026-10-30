#!/usr/bin/env bash
# Cross-platform deployment lifecycle script for WholeTake + Roomstamp (POSIX/Bash)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="${WT_DEPLOY_ROOT:-$DEFAULT_ROOT}"

DRY_RUN=0
WITH_DAEMONS=0
ENV_FILE=""
SUBCOMMAND=""
LOCAL_ACTION="start"

usage() {
    cat <<'EOF'
Usage: ./deploy/deploy.sh <subcommand> [options]

Subcommands:
  build                  Build Docker Compose images
  up | start             Ensure runtime dirs/keys and start Docker Compose stack
  down | stop            Stop Docker Compose stack
  status                 Show Docker Compose and local-demo status
  logs                   Tail service logs
  check-env              Initialize runtime dirs/ephemeral venue key and check config
  local-demo [action]    Manage native local Python processes (start|stop|status)

Options:
  --dry-run              Print commands and initialize dirs/keys without running Docker
  --with-daemons         Enable optional background daemons (--profile daemons)
  --env-file <path>      Specify custom .env file path
  --root <path>          Override working root directory (default: repo root)
  -h, --help             Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        build|up|start|down|stop|status|logs|check-env)
            if [[ -z "$SUBCOMMAND" ]]; then
                SUBCOMMAND="$1"
            fi
            shift
            ;;
        local-demo)
            SUBCOMMAND="local-demo"
            shift
            if [[ $# -gt 0 && ! "$1" =~ ^- ]]; then
                LOCAL_ACTION="$1"
                shift
            fi
            ;;
        --dry-run|-DryRun)
            DRY_RUN=1
            shift
            ;;
        --with-daemons|-WithDaemons)
            WITH_DAEMONS=1
            shift
            ;;
        --env-file|-EnvFile)
            if [[ $# -lt 2 || -z "${2:-}" || "${2:-}" =~ ^- ]]; then
                echo "ERROR: Missing value for $1" >&2
                usage >&2
                exit 1
            fi
            ENV_FILE="$2"
            shift 2
            ;;
        --root|-Root)
            if [[ $# -lt 2 || -z "${2:-}" || "${2:-}" =~ ^- ]]; then
                echo "ERROR: Missing value for $1" >&2
                usage >&2
                exit 1
            fi
            ROOT_DIR="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: Unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if [[ -z "$SUBCOMMAND" ]]; then
    usage >&2
    exit 1
fi

 find_python() {
    if [[ -x "$DEFAULT_ROOT/.venv/bin/python" ]]; then
        echo "$DEFAULT_ROOT/.venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        echo "python3"
    elif command -v python >/dev/null 2>&1; then
        echo "python"
    else
        echo "python3"
    fi
}

PY_BIN="$(find_python)"

generate_hex32() {
    if command -v "$PY_BIN" >/dev/null 2>&1; then
        "$PY_BIN" -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null && return 0
    fi
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 32 2>/dev/null && return 0
    fi
    od -An -N32 -tx1 /dev/urandom | tr -d ' \n'
    echo ""
}

ensure_runtime_env() {
    mkdir -p \
        "$ROOT_DIR/data/witness" \
        "$ROOT_DIR/data/verdicts" \
        "$ROOT_DIR/data/simx" \
        "$ROOT_DIR/data/uploads" \
        "$ROOT_DIR/logs" \
        "$ROOT_DIR/keys"

    local key_file="$ROOT_DIR/keys/venue.key"
    local existing_key=""
    if [[ -f "$key_file" ]]; then
        existing_key="$(tr -d '[:space:]' < "$key_file" 2>/dev/null || true)"
    fi
    if [[ ! "$existing_key" =~ ^[0-9a-fA-F]{64}$ ]]; then
        local hex_key
        hex_key="$(generate_hex32 | tr -d '\r\n ')"
        printf '%s\n' "$hex_key" > "$key_file"
        chmod 600 "$key_file" 2>/dev/null || true
        echo "[deploy] Generated ephemeral venue key at $key_file (mode 0600)"
    else
        chmod 600 "$key_file" 2>/dev/null || true
    fi
}

COMPOSE_ARGS=()
if [[ -f "$ROOT_DIR/docker-compose.yml" ]]; then
    COMPOSE_ARGS+=(-f "$ROOT_DIR/docker-compose.yml")
elif [[ -f "$DEFAULT_ROOT/docker-compose.yml" ]]; then
    COMPOSE_ARGS+=(-f "$DEFAULT_ROOT/docker-compose.yml")
fi

if [[ -n "$ENV_FILE" ]]; then
    COMPOSE_ARGS+=(--env-file "$ENV_FILE")
elif [[ -f "$ROOT_DIR/.env" ]]; then
    COMPOSE_ARGS+=(--env-file "$ROOT_DIR/.env")
fi

if [[ "$WITH_DAEMONS" -eq 1 ]]; then
    COMPOSE_ARGS+=(--profile daemons)
fi

run_compose() {
    local cmd_str="docker compose ${COMPOSE_ARGS[*]} $*"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "[dry-run] $cmd_str"
        return 0
    fi
    echo "[deploy] Running: $cmd_str"
    docker compose "${COMPOSE_ARGS[@]}" "$@"
}

run_local_demo() {
    local action="$1"
    ensure_runtime_env
    local simx_pid_file="$ROOT_DIR/logs/simx.pid"
    local verdict_pid_file="$ROOT_DIR/logs/verdict.pid"
    local bot_pid_file="$ROOT_DIR/logs/bot.pid"

    case "$action" in
        start|up)
            local simx_cmd="$PY_BIN -m simx serve --host 127.0.0.1 --port 8791 --data \"$ROOT_DIR/data/simx\""
            local verdict_cmd="WT_FETCH_ALLOW_HOSTS=127.0.0.1:8791 WT_WITNESS_ROOT=\"$ROOT_DIR/data/witness\" WT_VERDICTS_DIR=\"$ROOT_DIR/data/verdicts\" WT_UPLOADS_DIR=\"$ROOT_DIR/data/uploads\" ROOMSTAMP_LOG_DIR=\"$ROOT_DIR/logs\" ROOMSTAMP_KEY_FILE=\"$ROOT_DIR/keys/venue.key\" WHOLETAKE_ASR=none $PY_BIN -m verdict serve --host 127.0.0.1 --port 8790"
            if [[ "$DRY_RUN" -eq 1 ]]; then
                echo "[dry-run] $simx_cmd"
                echo "[dry-run] $verdict_cmd"
                if [[ "$WITH_DAEMONS" -eq 1 ]]; then
                    echo "[dry-run] X_API_BASE=http://127.0.0.1:8791 X_BOT_TOKEN=sim-wholetake WT_VERDICT_URL=http://127.0.0.1:8790 WT_PAGE_BASE=http://127.0.0.1:8790 $PY_BIN -m bot run"
                fi
                return 0
            fi
            (
                cd "$DEFAULT_ROOT"
                nohup "$PY_BIN" -m simx serve --host 127.0.0.1 --port 8791 --data "$ROOT_DIR/data/simx" > "$ROOT_DIR/logs/simx.log" 2>&1 &
                echo $! > "$simx_pid_file"
                WT_FETCH_ALLOW_HOSTS="127.0.0.1:8791" \
                WT_WITNESS_ROOT="$ROOT_DIR/data/witness" \
                WT_VERDICTS_DIR="$ROOT_DIR/data/verdicts" \
                WT_UPLOADS_DIR="$ROOT_DIR/data/uploads" \
                ROOMSTAMP_LOG_DIR="$ROOT_DIR/logs" \
                ROOMSTAMP_KEY_FILE="$ROOT_DIR/keys/venue.key" \
                WHOLETAKE_ASR="${WHOLETAKE_ASR:-none}" \
                nohup "$PY_BIN" -m verdict serve --host 127.0.0.1 --port 8790 > "$ROOT_DIR/logs/verdict.log" 2>&1 &
                echo $! > "$verdict_pid_file"
                if [[ "$WITH_DAEMONS" -eq 1 ]]; then
                    X_API_BASE="http://127.0.0.1:8791" \
                    X_BOT_TOKEN="sim-wholetake" \
                    WT_VERDICT_URL="http://127.0.0.1:8790" \
                    WT_PAGE_BASE="http://127.0.0.1:8790" \
                    BOT_POLL_S="5" \
                    BOT_INCLUDE_LINK="1" \
                    nohup "$PY_BIN" -m bot run > "$ROOT_DIR/logs/bot.log" 2>&1 &
                    echo $! > "$bot_pid_file"
                fi
            )
            echo "[deploy] Started local-demo processes (verdict :8790, simx :8791)"
            ;;
        stop|down)
            if [[ "$DRY_RUN" -eq 1 ]]; then
                echo "[dry-run] Stop PIDs in $simx_pid_file $verdict_pid_file $bot_pid_file"
                return 0
            fi
            for pf in "$bot_pid_file" "$verdict_pid_file" "$simx_pid_file"; do
                if [[ -f "$pf" ]]; then
                    local pid
                    pid="$(tr -d '[:space:]' < "$pf" 2>/dev/null || true)"
                    if [[ "$pid" =~ ^[0-9]+$ ]] && [[ "$pid" -gt 0 ]]; then
                        kill "$pid" 2>/dev/null || true
                    fi
                    rm -f "$pf"
                fi
            done
            echo "[deploy] Stopped local-demo processes"
            ;;
        status)
            for name in simx verdict bot; do
                local pf="$ROOT_DIR/logs/${name}.pid"
                if [[ -f "$pf" ]]; then
                    local pid
                    pid="$(tr -d '[:space:]' < "$pf" 2>/dev/null || true)"
                    if [[ "$pid" =~ ^[0-9]+$ ]] && [[ "$pid" -gt 0 ]] && kill -0 "$pid" 2>/dev/null; then
                        echo "local-demo $name pid=$pid"
                    else
                        rm -f "$pf"
                        echo "local-demo $name stopped"
                    fi
                else
                    echo "local-demo $name stopped"
                fi
            done
            ;;
        *)
            echo "ERROR: Invalid local-demo action: $action (expected start|stop|status)" >&2
            exit 1
            ;;
    esac
}

case "$SUBCOMMAND" in
    check-env)
        ensure_runtime_env
        echo "[check-env] ROOT_DIR=$ROOT_DIR"
        echo "[check-env] VENUE_KEY=$ROOT_DIR/keys/venue.key"
        echo "[check-env] DIRECTORIES=data/witness data/verdicts data/simx logs keys"
        echo "[check-env] OK"
        ;;
    build)
        ensure_runtime_env
        run_compose build
        ;;
    up|start)
        ensure_runtime_env
        run_compose up -d
        ;;
    down|stop)
        run_compose down
        ;;
    status)
        run_compose ps
        ;;
    logs)
        run_compose logs -f
        ;;
    local-demo)
        run_local_demo "$LOCAL_ACTION"
        ;;
    *)
        echo "ERROR: Unknown subcommand: $SUBCOMMAND" >&2
        usage >&2
        exit 1
        ;;
esac
