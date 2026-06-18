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
# Scope (decided 2026-05-18; extended 2026-06-15 for self-hosted LiveKit):
#   - This script DOES touch:  ngrok lifecycle (backend + session + LiveKit
#     signaling tunnels), backend/nexus/.env (CANDIDATE_SESSION_BASE_URL,
#     CORS_ORIGINS, LIVEKIT_PUBLIC_URL), frontend/session/.env.local
#     (NEXT_PUBLIC_API_URL, NEXT_PUBLIC_LIVEKIT_WS_URL), backend docker compose
#     (incl. bringing up the self-hosted LiveKit plane AND every nexus service:
#     api, worker, engine, pdf-worker, vision-worker — so no queue is left
#     without a consumer).
#   - This script ALSO touches (added 2026-06-18): frontend/app/.env.local
#     (NEXT_PUBLIC_API_URL → http://<LAN-IP>:8000) so the recruiter app's public
#     /recordings/<token> page, opened from another same-WiFi device, can reach
#     the backend. The PDF's recordings link is pointed at http://<LAN-IP>:3000
#     via the backend's RECORDING_SHARE_BASE_URL.
#   - This script does NOT:    start `npm run dev` for any frontend, or change
#     anything Supabase-related.
#
# Self-hosted LiveKit / LAN-only note:
#   WebRTC media flows LAN-direct (UDP 50000-60000) to this host's LAN IP — ngrok
#   only carries the low-bandwidth signaling. So candidates MUST be on the same
#   WiFi as this machine. True remote (internet) candidates need LiveKit Cloud
#   or a public-VM SFU; that is out of scope for this script.
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
APP_ENV="$REPO_ROOT/frontend/app/.env.local"
NGROK_CONFIG="$REPO_ROOT/scripts/ngrok.yml"
NGROK_GLOBAL_CONFIG="$HOME/.config/ngrok/ngrok.yml"
STATE_DIR="$REPO_ROOT/scripts/.state"
NGROK_PID_FILE="$STATE_DIR/ngrok.pid"
NGROK_LOG_FILE="$STATE_DIR/ngrok.log"
BACKEND_ENV_BACKUP="$STATE_DIR/backend.env.backup"
SESSION_ENV_BACKUP="$STATE_DIR/session.env.local.backup"
APP_ENV_BACKUP="$STATE_DIR/app.env.local.backup"

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
  [[ -f "$APP_ENV" ]]            || die "Recruiter app .env.local not found at $APP_ENV"
  [[ -f "$NGROK_CONFIG" ]]       || die "ngrok config not found at $NGROK_CONFIG"
  [[ -f "$NGROK_GLOBAL_CONFIG" ]]|| die "Global ngrok config not found at $NGROK_GLOBAL_CONFIG (run \`ngrok config add-authtoken <token>\`)"
}

# Echo the host's primary LAN IPv4 (the source address used to reach the
# internet). Falls back to the first `hostname -I` address. Used so the shared
# report PDF + the recruiter app's API base point at a same-WiFi-reachable host
# instead of localhost.
detect_lan_ip() {
  local ip
  ip="$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K\S+' | head -1 || true)"
  [[ -z "$ip" ]] && ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  echo "$ip"
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

# Poll the ngrok local API until all three endpoints are visible. Returns
# "<backend>|<session>|<livekit>" on stdout.
fetch_tunnel_urls() {
  local deadline=$(( $(date +%s) + 25 ))
  while [[ "$(date +%s)" -lt $deadline ]]; do
    if ! ngrok_pid_alive; then
      die "ngrok exited unexpectedly. Tail of $NGROK_LOG_FILE:
$(tail -20 "$NGROK_LOG_FILE" 2>/dev/null || echo "(no log)")"
    fi
    local raw backend session livekit
    raw="$(curl -fsS http://127.0.0.1:4040/api/tunnels 2>/dev/null || true)"
    if [[ -n "$raw" ]]; then
      backend=$(echo "$raw" | jq -r '.tunnels[] | select(.name=="nexus-backend") | .public_url' | head -1)
      session=$(echo "$raw" | jq -r '.tunnels[] | select(.name=="session-frontend") | .public_url' | head -1)
      livekit=$(echo "$raw" | jq -r '.tunnels[] | select(.name=="livekit-signaling") | .public_url' | head -1)
      if [[ "$backend" =~ ^https?:// && "$session" =~ ^https?:// && "$livekit" =~ ^https?:// ]]; then
        echo "${backend}|${session}|${livekit}"
        return 0
      fi
    fi
    sleep 1
  done
  die "Timed out waiting for ngrok tunnels (check $NGROK_LOG_FILE).
Note: this now needs THREE simultaneous tunnels (backend + session + LiveKit).
The ngrok free tier caps simultaneous tunnels — if the log shows a limit error,
upgrade the ngrok plan or reserve domains."
}

backup_envs_once() {
  mkdir -p "$STATE_DIR"
  [[ -f "$BACKEND_ENV_BACKUP" ]] || cp "$BACKEND_ENV" "$BACKEND_ENV_BACKUP"
  [[ -f "$SESSION_ENV_BACKUP" ]] || cp "$SESSION_ENV" "$SESSION_ENV_BACKUP"
  [[ -f "$APP_ENV_BACKUP" ]]     || cp "$APP_ENV" "$APP_ENV_BACKUP"
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
  if [[ -f "$APP_ENV_BACKUP" ]]; then
    cp "$APP_ENV_BACKUP" "$APP_ENV"
    rm "$APP_ENV_BACKUP"
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

ensure_livekit_plane() {
  # The self-hosted LiveKit SFU must be running for ANY interview (local or LAN).
  # COMPOSE_FILE (.env) already merges docker-compose.livekit.yml, so these
  # service names resolve. Plain `up -d` (no --force-recreate) is idempotent and
  # won't drop an in-progress call. Without this, create_room hits a dead SFU and
  # the candidate sees AGENT_NO_SHOW.
  (cd "$BACKEND_DIR" && docker compose up -d livekit-redis livekit-server livekit-egress)
}

ensure_nexus_workers() {
  # The dedicated queue-consumer workers must be running or their jobs queue in
  # Redis forever with no consumer (the silent failure mode behind missing
  # report-share emails + proctoring/reels):
  #   nexus-pdf-worker    → report_share   (the Share-report-PDF email)
  #   nexus-vision-worker → vision + reel  (proctoring analysis + candidate reel)
  # recreate_backend_containers only handles api/worker/engine, so without this
  # those two queues had no consumer in demo mode. Plain `up -d` is idempotent
  # (won't drop an in-flight job), matching ensure_livekit_plane. The workers
  # don't consume the rewritten candidate/livekit/cors env, so they need no
  # --force-recreate on a lan/local toggle.
  info "Ensuring the report-share PDF worker is up…"
  (cd "$BACKEND_DIR" && docker compose up -d nexus-pdf-worker)

  # Vision is a heavy POC: it needs the non-commercial ONNX weights at
  # ./models/resnet34_gaze.onnx and (ideally) an NVIDIA GPU + nvidia-container-
  # toolkit. On a host missing either, `up` errors — keep it BEST-EFFORT so it
  # never aborts the demo (the rest of the stack still works without it).
  info "Ensuring the vision worker is up (best-effort — needs GPU + ONNX weights)…"
  if (cd "$BACKEND_DIR" && docker compose up -d nexus-vision-worker); then
    ok "vision worker up"
  else
    info "⚠ vision worker did not start — proctoring + reels will not generate (non-fatal)."
    info "  Needs ./models/resnet34_gaze.onnx + nvidia-container-toolkit. See backend/nexus/docker-compose.yml."
  fi
}

read_env_value() {
  local file="$1" key="$2"
  # `|| true` keeps a missing key (grep exit 1) or a head-closed-pipe SIGPIPE
  # from tripping `set -euo pipefail`. Callers treat empty as <unset>.
  grep -E "^${key}=" "$file" 2>/dev/null | head -1 | cut -d= -f2- || true
}

# ---------------------------------------------------------------------------
# Commands

cmd_lan() {
  require_tools
  require_files

  info "Backing up env files (if first time)…"
  backup_envs_once

  info "Ensuring the self-hosted LiveKit plane is up…"
  ensure_livekit_plane

  info "Starting ngrok…"
  start_ngrok

  info "Waiting for tunnels to come up…"
  local urls; urls="$(fetch_tunnel_urls)"
  local backend_url session_url livekit_url
  IFS='|' read -r backend_url session_url livekit_url <<< "$urls"

  # The candidate browser loads the session app over HTTPS, so it must reach the
  # SFU over WSS (a plain ws:// would be blocked as mixed content). Flip the
  # ngrok https endpoint to wss for signaling. The session app's CSP connect-src
  # needs BOTH schemes (the livekit-client opens a WebSocket AND issues an
  # https fetch to /rtc/v1/validate against the same host).
  local livekit_wss="${livekit_url/https:/wss:}"
  echo "    backend  → $backend_url"
  echo "    session  → $session_url"
  echo "    livekit  → $livekit_wss (signaling; media is LAN-direct)"

  local lan_ip; lan_ip="$(detect_lan_ip)"
  [[ -n "$lan_ip" ]] || die "Could not detect this host's LAN IP (needed for the recordings share link)."
  info "Detected LAN IP: $lan_ip"

  info "Rewriting backend/nexus/.env (CANDIDATE_SESSION_BASE_URL, LIVEKIT_PUBLIC_URL, RECORDING_SHARE_BASE_URL, CORS_ORIGINS)…"
  set_env_var "$BACKEND_ENV" "CANDIDATE_SESSION_BASE_URL" "$session_url"
  set_env_var "$BACKEND_ENV" "LIVEKIT_PUBLIC_URL" "$livekit_wss"
  info "Pointing the recordings share link at the recruiter app on the LAN…"
  set_env_var "$BACKEND_ENV" "RECORDING_SHARE_BASE_URL" "http://${lan_ip}:3000"
  set_cors_origins "$BACKEND_ENV" "$session_url" "$backend_url" "http://${lan_ip}:3000" "${LOCAL_ORIGINS[@]}"

  info "Rewriting frontend/session/.env.local (NEXT_PUBLIC_API_URL, NEXT_PUBLIC_LIVEKIT_WS_URL)…"
  set_env_var "$SESSION_ENV" "NEXT_PUBLIC_API_URL" "$backend_url"
  set_env_var "$SESSION_ENV" "NEXT_PUBLIC_LIVEKIT_WS_URL" "$livekit_wss $livekit_url"

  info "Rewriting frontend/app/.env.local (NEXT_PUBLIC_API_URL → LAN backend)…"
  set_env_var "$APP_ENV" "NEXT_PUBLIC_API_URL" "http://${lan_ip}:8000"

  info "Recreating backend docker containers so they read the new env…"
  recreate_backend_containers

  info "Recreating the report-share PDF worker so it reads RECORDING_SHARE_BASE_URL…"
  (cd "$BACKEND_DIR" && docker compose up -d --force-recreate nexus-pdf-worker)

  info "Ensuring the dedicated nexus queue-workers are up (pdf + vision)…"
  ensure_nexus_workers

  cat <<EOF

$(ok "LAN mode is live.")

  Candidate invite URLs will look like:
    ${session_url}/interview/<token>

  Public backend:  $backend_url
  LiveKit signal:  $livekit_wss  (media is LAN-direct — same WiFi only)
  ngrok dashboard: http://127.0.0.1:4040

Next steps:
  1. (Re)start the session app — required because Next.js reads .env.local on boot
     (this is also how it picks up the new NEXT_PUBLIC_LIVEKIT_WS_URL):
       (cd frontend/session && npm run dev)
  2. To let a PDF recipient watch the recording from another device, (re)start
     the recruiter app so it reads the rewritten NEXT_PUBLIC_API_URL, and reach
     it via the LAN IP (NOT localhost):
       (cd frontend/app && npm run dev)
       → recruiter dashboard on this machine: http://${lan_ip}:3000
     The shared report PDF's "See full session recording" link will point at
     http://${lan_ip}:3000/recordings/<token>, reachable by any same-WiFi device.
  3. From your recruiter dashboard at http://${lan_ip}:3000, send an invite.
  4. Grab the candidate URL from the dry-run email log:
       docker compose -f $BACKEND_DIR/docker-compose.yml logs --tail=200 nexus \\
         | grep -E 'invite_url|email.dry_run'
  5. Open that URL on a device that is ON THE SAME WiFi as this machine.
     (WebRTC media goes LAN-direct to this host — a phone on cellular or a
      remote laptop will connect signaling but get no audio/video.)

If the candidate joins but the interviewer never connects / no audio:
  - Confirm this host's firewall allows inbound UDP 50000-60000 (+ TCP 7881)
    from the LAN, e.g.:  sudo ss -ulnp | grep -E ':5[0-9]{4}'
  - Confirm the LiveKit plane is healthy:  docker compose ps livekit-server

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

  info "Ensuring the self-hosted LiveKit plane is up (needed for local interviews too)…"
  ensure_livekit_plane

  if restore_envs; then
    info "Restored env files to their pre-LAN values"
    info "Recreating backend docker containers so they read the restored env…"
    recreate_backend_containers
    info "Recreating the report-share PDF worker so it drops RECORDING_SHARE_BASE_URL…"
    (cd "$BACKEND_DIR" && docker compose up -d --force-recreate nexus-pdf-worker)
    info "Ensuring the dedicated nexus queue-workers are up (pdf + vision)…"
    ensure_nexus_workers
    ok "Local mode restored. If your session app was running, restart it to pick up NEXT_PUBLIC_API_URL=http://localhost:8000 and the local LIVEKIT_PUBLIC_URL."
  else
    info "No env backup to restore — nothing to change"
    info "Ensuring the dedicated nexus queue-workers are up (pdf + vision)…"
    ensure_nexus_workers
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
  local backend_lk_public session_lk_ws
  backend_lk_public="$(read_env_value "$BACKEND_ENV" "LIVEKIT_PUBLIC_URL")"
  session_lk_ws="$(read_env_value "$SESSION_ENV" "NEXT_PUBLIC_LIVEKIT_WS_URL")"
  local backend_rec_share app_api
  backend_rec_share="$(read_env_value "$BACKEND_ENV" "RECORDING_SHARE_BASE_URL")"
  app_api="$(read_env_value "$APP_ENV" "NEXT_PUBLIC_API_URL")"

  echo "Mode: $mode"
  echo
  echo "  frontend/session NEXT_PUBLIC_API_URL        : ${session_api:-<unset>}"
  echo "  frontend/session NEXT_PUBLIC_LIVEKIT_WS_URL : ${session_lk_ws:-<unset>}"
  echo "  backend          CANDIDATE_SESSION_BASE_URL : ${backend_csbu:-<unset>}"
  echo "  backend          LIVEKIT_PUBLIC_URL         : ${backend_lk_public:-<unset>}"
  echo "  backend          RECORDING_SHARE_BASE_URL   : ${backend_rec_share:-<unset>}"
  echo "  frontend/app     NEXT_PUBLIC_API_URL        : ${app_api:-<unset>}"
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
