#!/bin/zsh

set -eu

project_dir=${0:A:h:h}
mkdir -p "$project_dir/.data"

api_host=${API_HOST:-127.0.0.1}
api_port=${API_PORT:-8000}
trueforge_url=${TRUEFORGE_URL:-http://localhost:${TRUEFORGE_PORT:-8790}}
trueforge_port=${TRUEFORGE_PORT:-${trueforge_url##*:}}
trueforge_port=${trueforge_port%/}
ui_port=${UI_PORT:-5173}
ui_host=${UI_HOST:-127.0.0.1}
export API_HOST="$api_host"
export API_PORT="$api_port"
export UI_HOST="$ui_host"
export UI_PORT="$ui_port"
export TRUEFORGE_URL="$trueforge_url"
export RESEARCH_MAP_MCP_URL=${RESEARCH_MAP_MCP_URL:-http://$api_host:$api_port/mcp}
export VITE_API_PROXY_TARGET=${VITE_API_PROXY_TARGET:-http://$api_host:$api_port}
browser_api_host=$api_host
if [[ "$browser_api_host" == "0.0.0.0" ]]; then
  browser_api_host=127.0.0.1
fi
if (( ! ${+VITE_API_BASE_URL} )); then
  export VITE_API_BASE_URL="http://$browser_api_host:$api_port"
else
  export VITE_API_BASE_URL
fi

typeset -a service_root_pids=()
cleanup_started=0

collect_process_tree() {
  local root_pid=$1
  local current_pid child_pid children
  local -a pending_pids=($root_pid)

  while (( ${#pending_pids[@]} > 0 )); do
    current_pid=$pending_pids[1]
    shift pending_pids
    if [[ $current_pid != <-> ]] || ! kill -0 "$current_pid" 2>/dev/null; then
      continue
    fi
    print -r -- "$current_pid"
    children=$(pgrep -P "$current_pid" 2>/dev/null || true)
    for child_pid in ${(f)children}; do
      [[ -n $child_pid ]] && pending_pids+=("$child_pid")
    done
  done
}

cleanup() {
  local root_pid process_pid
  local -a scoped_pids=()
  local -A seen_pids=()

  if (( cleanup_started )); then
    return 0
  fi
  cleanup_started=1
  trap - EXIT INT TERM HUP

  # Snapshot each service tree before signalling its supervisor. This keeps
  # uvicorn reload workers and pnpm/node children in scope even if their parent
  # exits and the OS reparents them during cleanup.
  for root_pid in $service_root_pids; do
    scoped_pids+=("${(@f)$(collect_process_tree "$root_pid")}")
  done
  for process_pid in $scoped_pids; do
    if [[ -n $process_pid && -z ${seen_pids[$process_pid]-} ]]; then
      seen_pids[$process_pid]=1
      kill -TERM "$process_pid" 2>/dev/null || true
    fi
  done

  # Give every scoped process a short graceful-exit window, then force only
  # the exact PIDs captured above. No name-based or system-wide kill is used.
  for _attempt in {1..50}; do
    local any_alive=0
    for process_pid in ${(k)seen_pids}; do
      if kill -0 "$process_pid" 2>/dev/null; then
        any_alive=1
        break
      fi
    done
    (( any_alive == 0 )) && break
    sleep 0.1
  done
  for process_pid in ${(k)seen_pids}; do
    kill -KILL "$process_pid" 2>/dev/null || true
  done
  for root_pid in $service_root_pids; do
    wait "$root_pid" 2>/dev/null || true
  done

  return 0
}

handle_signal() {
  local signal_status=$1
  cleanup
  exit "$signal_status"
}

handle_exit() {
  local exit_status=$?
  cleanup
  exit "$exit_status"
}

trap 'handle_signal 130' INT
trap 'handle_signal 143' TERM
trap 'handle_signal 129' HUP
trap handle_exit EXIT

pnpm dlx @truefoundry/trueforge@0.1.4 --port "$trueforge_port" &
trueforge_pid=$!
service_root_pids+=("$trueforge_pid")

uv run --directory "$project_dir/backend" research-map-migrate

uv run --directory "$project_dir/backend" \
  uvicorn research_map_backend.app:app --host "$api_host" --port "$api_port" --reload &
api_pid=$!
service_root_pids+=("$api_pid")

for attempt in {1..60}; do
  if ! kill -0 "$trueforge_pid" 2>/dev/null; then
    print -u2 "TrueForge stopped before becoming ready. Port $trueforge_port may be in use."
    exit 1
  fi
  if ! kill -0 "$api_pid" 2>/dev/null; then
    print -u2 "FastAPI stopped before becoming ready. Port $api_port may be in use."
    exit 1
  fi
  if curl -fsS "$trueforge_url/healthz" >/dev/null && \
    curl -fsS "http://$api_host:$api_port/api/v1/health" >/dev/null; then
    break
  fi
  if (( attempt == 60 )); then
    print -u2 "Local services did not become ready."
    exit 1
  fi
  sleep 1
done

uv run --directory "$project_dir/backend" research-map-bootstrap

pnpm --dir "$project_dir/ui" dev --host "$ui_host" --port "$ui_port" --strictPort &
vite_pid=$!
service_root_pids+=("$vite_pid")

set +e
wait "$vite_pid"
vite_status=$?
set -e
exit "$vite_status"
