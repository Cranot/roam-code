/** TP: an application imports Anthropic's TypeScript agent SDK. */

import { query } from "@anthropic-ai/claude-agent-sdk";

export async function answer(prompt: string) {
  return query({ prompt });
}
