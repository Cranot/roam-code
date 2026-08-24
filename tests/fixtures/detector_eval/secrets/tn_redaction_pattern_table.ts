/** TN: these strings describe credential shapes; they are not credentials. */

export const REDACTION_PATTERNS = [
  { api_key: "sk-[A-Za-z0-9]{48}" }, // secretsallow
  { token: "ghp_[A-Za-z0-9_]{36,255}" }, // secretsallow
];
