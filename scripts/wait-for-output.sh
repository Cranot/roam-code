#!/usr/bin/env bash
# wait-for-output — bounded, liveness-checked poll for a sentinel in a file.
# The correct primitive for "wait until a background task writes its result":
# it NEVER hangs forever (unlike `until grep X file; do sleep; done`).
#
#   wait-for-output <file> <sentinel-regex> [timeout_sec=900] [producer_pid] [interval=5]
#
# The regex matches anywhere in a line, so a log that echoes the sentinel (a command
# line, a prompt) ends the wait early: anchor it, e.g. '^DONE_RC='.
#
# Exit codes: 0 found · 2 timed out · 3 producer died before the sentinel · 64 usage.
set -uo pipefail
FILE="${1:-}"; PAT="${2:-}"; TIMEOUT="${3:-900}"; PID="${4:-}"; INTERVAL="${5:-5}"
if [ -z "$FILE" ] || [ -z "$PAT" ]; then
  echo "usage: wait-for-output <file> <sentinel-regex> [timeout_sec=900] [producer_pid] [interval=5]" >&2
  echo "       anchor the regex to the line start (e.g. '^DONE_RC=') so an echoed sentinel does not match" >&2
  exit 64
fi
found() { [ -f "$FILE" ] && grep -Eq "$PAT" "$FILE" 2>/dev/null; }
elapsed=0
while :; do
  if found; then
    echo "wait-for-output: matched /$PAT/ in $FILE after ${elapsed}s"
    exit 0
  fi
  # A producer writes its result and then exits, so it can do both between the
  # read above and this check: read once more before calling it dead.
  if [ -n "$PID" ] && ! kill -0 "$PID" 2>/dev/null && ! found; then
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
