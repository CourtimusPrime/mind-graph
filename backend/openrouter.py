import json
from collections.abc import AsyncGenerator

import httpx
from backend.config import OPENROUTER_API_KEY, OPENROUTER_MODEL

_BASE_URL = "https://openrouter.ai/api/v1"


def _build_messages(messages: list[dict], context: str) -> list[dict]:
    if not context:
        return messages
    system = {
        "role": "system",
        "content": (
            "You are a personal knowledge assistant. Use the following "
            "context from the user's knowledge graph to inform your "
            f"response:\n\n{context}"
        ),
    }
    return [system] + messages


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }


async def chat(messages: list[dict], context: str = "") -> str:
    """Send a conversation to OpenRouter and return the full reply."""
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": _build_messages(messages, context),
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{_BASE_URL}/chat/completions",
            json=payload,
            headers=_headers(),
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]


async def chat_stream(
    messages: list[dict], context: str = ""
) -> AsyncGenerator[str, None]:
    """Yield text chunks from OpenRouter's streaming API."""
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": _build_messages(messages, context),
        "stream": True,
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream(
            "POST",
            f"{_BASE_URL}/chat/completions",
            json=payload,
            headers=_headers(),
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    delta = chunk["choices"][0]["delta"].get("content") or ""
                    if delta:
                        yield delta
                except (json.JSONDecodeError, KeyError):
                    continue
