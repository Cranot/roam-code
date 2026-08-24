import fs from "node:fs";

export function localLatency(raw) {
  const latency = parseInt(raw, 10) || 0;
  return latency;
}

export function returnedSize(path) {
  return fs.statSync(path).size ?? 0;
}
