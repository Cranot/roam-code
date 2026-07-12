"""TN: the provider call uses an explicit dated model version."""

from openai import OpenAI

client = OpenAI()
client.chat.completions.create(
    model="gpt-4-0613",
    messages=[{"role": "user", "content": "hi"}],
)
