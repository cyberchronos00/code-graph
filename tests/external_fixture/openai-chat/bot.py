import os

from openai import OpenAI

KEY = os.environ["OPENAI_API_KEY"]


def ask(q):
    return OpenAI().chat.completions.create(model="gpt-4.1", messages=[{"role": "user", "content": q}])
