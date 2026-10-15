"""Tiny OpenAI-compatible client (vLLM). Standard library only."""
import json, urllib.request


def _post(url, payload, timeout):
    req = urllib.request.Request(url.rstrip("/") + "/chat/completions", json.dumps(payload).encode(),
                                 {"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


def chat(url, model, messages, max_tokens=900, temperature=0.2, timeout=600, **extra) -> str:
    with _post(url, {"model": model, "messages": messages, "max_tokens": max_tokens,
                     "temperature": temperature, **extra}, timeout) as r:
        return json.load(r)["choices"][0]["message"]["content"]


def chat_stream(url, model, messages, max_tokens=900, temperature=0.2, timeout=600, **extra):
    with _post(url, {"model": model, "messages": messages, "max_tokens": max_tokens,
                     "temperature": temperature, "stream": True, **extra}, timeout) as r:
        for raw in r:
            line = raw.decode().strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                return
            delta = json.loads(data)["choices"][0].get("delta", {}).get("content")
            if delta:
                yield delta
