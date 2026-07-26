#!/usr/bin/env bash
# wait-for-output — bounded, liveness-checked poll for a sentinel in a file.
# The correct primitive for "wait until a background task writes its result":
# it NEVER hangs forever (unlike `until grep X file; do sleep; done`).
#
#   wait-for-output <file> <sentinel-regex> [timeout_sec=900] [producer_pid] [interval=5]
#
# Exit codes: 0 found · 2 timed out · 3 producer died before the sentinel · 64 usage.
set -uo pipefail
FILE="${1:-}"; PAT="${2:-}"; TIMEOUT="${3:-900}"; PID="${4:-}"; INTERVAL="${5:-5}"
if [ -z "$FILE" ] || [ -z "$PAT" ]; then
  echo "usage: wait-for-output <file> <sentinel-regex> [timeout_sec=900] [producer_pid] [interval=5]" >&2
  exit 64
fi
elapsed=0
while :; do
  if [ -f "$FILE" ] && grep -Eq "$PAT" "$FILE" 2>/dev/null; then
    echo "wait-for-output: matched /$PAT/ in $FILE after ${elapsed}s"
    exit 0
  fi
  if [ -n "$PID" ] && ! kill -0 "$PID" 2>/dev/null; then
    echo "wait-for-output: producer pid $PID exited before /$PAT/ appeared in $FILE (waited ${elapsed}s)" >&2
    exit 3
  fi
  if [ "$elapsed" -ge "$TIMEOUT" ]; then
    echo "wait-for-output: TIMEOUT after ${TIMEOUT}s waiting for /$PAT/ in $FILE" >&2
    exit 2
  fi
  sleep "$INTERVAL"
  elapsed=$((elapsed + INTERVAL))
done
