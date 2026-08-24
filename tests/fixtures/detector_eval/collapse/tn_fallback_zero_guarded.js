import fs from "node:fs";

export function fileSize(path) {
  if (!fs.existsSync(path)) {
    return 0;
  }
  const size = fs.statSync(path).size || 0;
  return size;
}
