#!/usr/bin/env bash
# Toggle the demo between local-only and LAN/ngrok modes.
#
# Modes:
#   lan     Start ngrok tunnels (backend + session), rewrite the candidate-
#           facing env values to the public ngrok URLs, recreate the backend
#           docker containers so they pick up the new env.
#   local   Kill ngrok, restore the original env files, recreate the backend
#           containers.
#   status  Show which mode is active and the current ngrok URLs.
#
# Scope (decided 2026-05-18):
#   - This script DOES touch:  ngrok lifecycle, backend/nexus/.env,
#     frontend/session/.env.local, backend docker compose.
#   - This script does NOT:    start `npm run dev` for any frontend, touch the
#     recruiter app (frontend/app), or change anything Supabase-related.
#
# After running `lan`, restart the session app yourself:
#     (cd frontend/session && npm run dev)
# The recruiter dashboard stays on http://localhost:3000.

set -euo pipefail

# ---------------------------------------------------------------------------
# Paths + constants

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend/nexus"
BACKEND_ENV="$BACKEND_DIR/.env"
SESSION_ENV="$REPO_ROOT/frontend/session/.env.local"
NGROK_CONFIG="$REPO_ROOT/scripts/ngrok.yml"
NGROK_GLOBAL_CONFIG="$HOME/.config/ngrok/ngrok.yml"
STATE_DIR="$REPO_ROOT/scripts/.state"
NGROK_PID_FILE="$STATE_DIR/ngrok.pid"
NGROK_LOG_FILE="$STATE_DIR/ngrok.log"
BACKEND_ENV_BACKUP="$STATE_DIR/backend.env.backup"
SESSION_ENV_BACKUP="$STATE_DIR/session.env.local.backup"

# Origins to keep in CORS_ORIGINS regardless of mode (local dev shouldn't
# break while the tunnel is up).
LOCAL_ORIGINS=(
  "http://localhost:3000"
  "http://localhost:3001"
  "http://localhost:3002"
  "http://127.0.0.1:3000"
  "http://127.0.0.1:3001"
  "http://127.0.0.1:3002"
)

# ---------------------------------------------------------------------------
# Helpers

usage() {
  cat <<EOF
Usage: $(basename "$0") <command>

Commands:
  lan      Switch to LAN mode (start ngrok, rewrite envs, recreate backend).
  local    Switch back to local mode (kill ngrok, restore envs, recreate backend).
  status   Show current mode + ngrok URLs (if any).
EOF
}

die() { echo "✗ $*" >&2; exit 1; }
info() { echo "→ $*"; }
ok()   { echo "✓ $*"; }

require_tools() {
  local missing=()
  for cmd in ngrok docker jq curl python3; do
    command -v "$cmd" >/dev/null || missing+=("$cmd")
  done
  if (( ${#missing[@]} > 0 )); then
    die "Missing required tools: ${missing[*]}"
  fi
}

require_files() {
  [[ -f "$BACKEND_ENV" ]]        || die "Backend .env not found at $BACKEND_ENV"
  [[ -f "$SESSION_ENV" ]]        || die "Session .env.local not found at $SESSION_ENV"
  [[ -f "$NGROK_CONFIG" ]]       || die "ngrok config not found at $NGROK_CONFIG"
  [[ -f "$NGROK_GLOBAL_CONFIG" ]]|| die "Global ngrok config not found at $NGROK_GLOBAL_CONFIG (run \`ngrok config add-authtoken <token>\`)"
}

ngrok_pid_alive() {
  [[ -f "$NGROK_PID_FILE" ]] || return 1
  local pid; pid="$(cat "$NGROK_PID_FILE" 2>/dev/null || true)"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

kill_ngrok_if_running() {
  if ngrok_pid_alive; then
    local pid; pid="$(cat "$NGROK_PID_FILE")"
    kill "$pid" 2>/dev/null || true
    # Give it a beat to release port 4040 before any restart attempt.
    sleep 1
  fi
  rm -f "$NGROK_PID_FILE"
}

start_ngrok() {
  kill_ngrok_if_running
  mkdir -p "$STATE_DIR"

  # `--config` flag can be passed twice — ngrok merges them. Auth token
  # comes from the global config; endpoint definitions come from the
  # project file. Neither file gets mutated by this script.
  nohup ngrok start --all \
      --config "$NGROK_GLOBAL_CONFIG" \
      --config "$NGROK_CONFIG" \
      --log "$NGROK_LOG_FILE" \
      --log-format json \
      >/dev/null 2>&1 &
  echo $! > "$NGROK_PID_FILE"
}

# Poll the ngrok local API until both endpoints are visible. Returns
# "<backend>|<session>" on stdout.
fetch_tunnel_urls() {
  local deadline=$(( $(date +%s) + 25 ))
  while [[ "$(date +%s)" -lt $deadline ]]; do
    if ! ngrok_pid_alive; then
      die "ngrok exited unexpectedly. Tail of $NGROK_LOG_FILE:
$(tail -20 "$NGROK_LOG_FILE" 2>/dev/null || echo "(no log)")"
    fi
    local raw backend session
    raw="$(curl -fsS http://127.0.0.1:4040/api/tunnels 2>/dev/null || true)"
    if [[ -n "$raw" ]]; then
      backend=$(echo "$raw" | jq -r '.tunnels[] | select(.name=="nexus-backend") | .public_url' | head -1)
      session=$(echo "$raw" | jq -r '.tunnels[] | select(.name=="session-frontend") | .public_url' | head -1)
      if [[ "$backend" =~ ^https?:// && "$session" =~ ^https?:// ]]; then
        echo "${backend}|${session}"
        return 0
      fi
    fi
    sleep 1
  done
  die "Timed out waiting for ngrok tunnels (check $NGROK_LOG_FILE)"
}

backup_envs_once() {
  mkdir -p "$STATE_DIR"
  [[ -f "$BACKEND_ENV_BACKUP" ]] || cp "$BACKEND_ENV" "$BACKEND_ENV_BACKUP"
  [[ -f "$SESSION_ENV_BACKUP" ]] || cp "$SESSION_ENV" "$SESSION_ENV_BACKUP"
}

restore_envs() {
  local restored=0
  if [[ -f "$BACKEND_ENV_BACKUP" ]]; then
    cp "$BACKEND_ENV_BACKUP" "$BACKEND_ENV"
    rm "$BACKEND_ENV_BACKUP"
    restored=1
  fi
  if [[ -f "$SESSION_ENV_BACKUP" ]]; then
    cp "$SESSION_ENV_BACKUP" "$SESSION_ENV"
    rm "$SESSION_ENV_BACKUP"
    restored=1
  fi
  return $((1 - restored))
}

# Set or replace a KEY=VALUE line in a dotenv file. Uses python so values
# can contain arbitrary characters (URLs, JSON) without sed escaping hell.
set_env_var() {
  python3 - "$1" "$2" "$3" <<'PY'
import sys, pathlib
file, key, val = sys.argv[1:4]
p = pathlib.Path(file)
lines = p.read_text().splitlines()
out, seen = [], False
for line in lines:
    if line.startswith(f"{key}=") or line.startswith(f"{key} ="):
        out.append(f"{key}={val}")
        seen = True
    else:
        out.append(line)
if not seen:
    out.append(f"{key}={val}")
p.write_text("\n".join(out) + "\n")
PY
}

# Write CORS_ORIGINS as a JSON list literal. Backend's pydantic-settings
# parses CORS_ORIGINS as JSON, so the value must be valid JSON.
set_cors_origins() {
  local file="$1"; shift
  python3 - "$file" "$@" <<'PY'
import sys, json, pathlib
file = sys.argv[1]
origins = sys.argv[2:]
new_val = json.dumps(origins, separators=(",", ""))
p = pathlib.Path(file)
lines = p.read_text().splitlines()
out, seen = [], False
for line in lines:
    if line.startswith("CORS_ORIGINS="):
        out.append(f"CORS_ORIGINS={new_val}")
        seen = True
    else:
        out.append(line)
if not seen:
    out.append(f"CORS_ORIGINS={new_val}")
p.write_text("\n".join(out) + "\n")
PY
}

recreate_backend_containers() {
  # docker compose detects the .env change and recreates affected services.
  # We explicitly recreate the three nexus services (not redis — it has no
  # env dependency on our edits).
  (cd "$BACKEND_DIR" && docker compose up -d --force-recreate nexus nexus-worker nexus-engine)
}

read_env_value() {
  local file="$1" key="$2"
  grep -E "^${key}=" "$file" 2>/dev/null | head -1 | cut -d= -f2-
}

# ---------------------------------------------------------------------------
# Commands

cmd_lan() {
  require_tools
  require_files

  info "Backing up env files (if first time)…"
  backup_envs_once

  info "Starting ngrok…"
  start_ngrok

  info "Waiting for tunnels to come up…"
  local urls; urls="$(fetch_tunnel_urls)"
  local backend_url="${urls%|*}"
  local session_url="${urls#*|}"
  echo "    backend  → $backend_url"
  echo "    session  → $session_url"

  info "Rewriting backend/nexus/.env (CANDIDATE_SESSION_BASE_URL, CORS_ORIGINS)…"
  set_env_var "$BACKEND_ENV" "CANDIDATE_SESSION_BASE_URL" "$session_url"
  set_cors_origins "$BACKEND_ENV" "$session_url" "$backend_url" "${LOCAL_ORIGINS[@]}"

  info "Rewriting frontend/session/.env.local (NEXT_PUBLIC_API_URL)…"
  set_env_var "$SESSION_ENV" "NEXT_PUBLIC_API_URL" "$backend_url"

  info "Recreating backend docker containers so they read the new env…"
  recreate_backend_containers

  cat <<EOF

$(ok "LAN mode is live.")

  Candidate invite URLs will look like:
    ${session_url}/interview/<token>

  Public backend:  $backend_url
  ngrok dashboard: http://127.0.0.1:4040

Next steps:
  1. (Re)start the session app — required because Next.js reads .env.local on boot:
       (cd frontend/session && npm run dev)
  2. From your recruiter dashboard at http://localhost:3000, send an invite.
  3. Grab the candidate URL from the dry-run email log:
       docker compose -f $BACKEND_DIR/docker-compose.yml logs --tail=200 nexus \\
         | grep -E 'invite_url|email.dry_run'
  4. Open that URL on any device on the WiFi.

When you're done:  $(basename "$0") local
EOF
}

cmd_local() {
  require_tools

  if ngrok_pid_alive; then
    info "Killing ngrok…"
    kill_ngrok_if_running
  else
    info "ngrok already stopped"
  fi

  if restore_envs; then
    info "Restored env files to their pre-LAN values"
    info "Recreating backend docker containers so they read the restored env…"
    recreate_backend_containers
    ok "Local mode restored. If your session app was running, restart it to pick up NEXT_PUBLIC_API_URL=http://localhost:8000."
  else
    info "No env backup to restore — nothing to change"
  fi
}

cmd_status() {
  local mode session_api backend_csbu
  if ngrok_pid_alive; then
    mode="lan (ngrok pid $(cat "$NGROK_PID_FILE"))"
  else
    mode="local"
  fi
  session_api="$(read_env_value "$SESSION_ENV" "NEXT_PUBLIC_API_URL")"
  backend_csbu="$(read_env_value "$BACKEND_ENV" "CANDIDATE_SESSION_BASE_URL")"

  echo "Mode: $mode"
  echo
  echo "  frontend/session NEXT_PUBLIC_API_URL : ${session_api:-<unset>}"
  echo "  backend          CANDIDATE_SESSION_BASE_URL: ${backend_csbu:-<unset>}"
  echo

  if ngrok_pid_alive; then
    local raw
    raw="$(curl -fsS http://127.0.0.1:4040/api/tunnels 2>/dev/null || true)"
    if [[ -n "$raw" ]]; then
      echo "Active ngrok tunnels:"
      echo "$raw" | jq -r '.tunnels[] | "  \(.name): \(.public_url) → \(.config.addr)"'
    fi
  fi
}

# ---------------------------------------------------------------------------
# Dispatch

case "${1:-}" in
  lan)    cmd_lan ;;
  local)  cmd_local ;;
  status) cmd_status ;;
  -h|--help|help|"") usage; [[ -z "${1:-}" ]] && exit 2 || exit 0 ;;
  *)      usage; exit 2 ;;
esac
