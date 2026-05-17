#!/usr/bin/env bash
# grep_naturalness.sh — surface flagged turns from a session audit envelope.
#
# Usage:
#   ./scripts/grep_naturalness.sh <session_uuid>
#
# Prints any speaker.output event where any naturalness flag is true /
# non-empty. Empty output means the session is clean.

set -euo pipefail

if [ -z "${1:-}" ]; then
    echo "usage: $0 <session_uuid>" >&2
    exit 2
fi

SESSION="$1"
ENV_FILE="engine-events/${SESSION}.json"

if [ ! -f "$ENV_FILE" ]; then
    echo "envelope not found: $ENV_FILE" >&2
    exit 1
fi

jq '.events[]
    | select(.kind == "speaker.output")
    | select(
        .payload.naturalness_flags.repeated_opener == true
        or .payload.naturalness_flags.name_overuse == true
        or .payload.naturalness_flags.exceeded_soft_target == true
        or ((.payload.naturalness_flags.banned_phrases_emitted // []) | length > 0)
      )
    | {turn_id: .payload.turn_id,
       output: .payload.final_utterance,
       flags: .payload.naturalness_flags}
   ' "$ENV_FILE"
