/** TN: this table defines how credential-bearing URLs are scrubbed. */

export const URL_CREDENTIAL_REDACTIONS = [
  [
    new RegExp("postgresql://.*user.*:.*password.*@.*host.*"),
    "[redacted]",
  ],
];
