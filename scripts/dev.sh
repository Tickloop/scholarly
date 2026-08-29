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

cleanup() {
  [[ -n ${api_pid:-} ]] && kill "$api_pid" 2>/dev/null || true
  [[ -n ${trueforge_pid:-} ]] && kill "$trueforge_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

pnpm dlx @truefoundry/trueforge@0.1.4 --port "$trueforge_port" &
trueforge_pid=$!

uv run --directory "$project_dir/backend" research-map-migrate

uv run --directory "$project_dir/backend" \
  uvicorn research_map_backend.app:app --host "$api_host" --port "$api_port" --reload &
api_pid=$!

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

pnpm --dir "$project_dir/ui" dev --host "$ui_host" --port "$ui_port" --strictPort
