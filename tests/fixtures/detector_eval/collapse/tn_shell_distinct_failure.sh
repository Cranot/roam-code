#!/usr/bin/env bash

if latency="$(measure_latency --format integer)"; then
  printf '%s\n' "$latency"
else
  printf '%s\n' "measurement-unavailable" >&2
  exit 1
fi
