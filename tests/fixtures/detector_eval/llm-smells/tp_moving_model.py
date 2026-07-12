"""TP: an LLM call uses a moving model alias without version pinning."""

from openai import OpenAI

client = OpenAI()
client.chat.completions.create(model="gpt-4", messages=[{"role": "user", "content": "hi"}])
