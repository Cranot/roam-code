import fs from "node:fs";

export function loadOptions(path) {
  if (!fs.existsSync(path)) {
    return {};
  }
  try {
    return JSON.parse(fs.readFileSync(path, "utf8"));
  } catch (error) {
    return {};
  }
}
