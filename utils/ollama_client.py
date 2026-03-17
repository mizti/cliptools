from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OllamaChatResult:
    content: str
    raw: dict[str, Any]


def ollama_chat(
    *,
    base_url: str,
    model: str,
    messages: list[dict[str, Any]],
    options: dict[str, Any] | None = None,
    think: bool | None = None,
    timeout_s: float = 120.0,
) -> OllamaChatResult:
    url = base_url.rstrip("/") + "/api/chat"

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    # Some models support "thinking". If enabled, the API may separate the
    # model's thinking from the final content (message.thinking vs message.content),
    # and thinking can add large latency. Callers can explicitly disable it.
    if think is not None:
        payload["think"] = think
    if options:
        payload["options"] = options

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
        data = json.loads(resp.read().decode("utf-8"))

    message = data.get("message") or {}
    content = message.get("content")
    if not isinstance(content, str):
        raise ValueError("Unexpected Ollama response: missing message.content")

    return OllamaChatResult(content=content, raw=data)
