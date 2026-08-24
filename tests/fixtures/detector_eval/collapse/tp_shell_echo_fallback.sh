#!/usr/bin/env bash

latency="$(measure_latency --format integer || echo 0)"
name="$(read_name || echo '')"
