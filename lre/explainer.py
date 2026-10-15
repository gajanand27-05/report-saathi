import json
from .checks import SYSTEM_PROMPT
from .llm import chat_stream


def stream(inp: dict, url: str, model: str):
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": json.dumps(inp, ensure_ascii=False)}]
    yield from chat_stream(url, model, msgs, max_tokens=900, temperature=0.2)
