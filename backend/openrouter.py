import json
from collections.abc import AsyncGenerator

import httpx
from backend.config import OPENROUTER_API_KEY, OPENROUTER_MODEL, OPENROUTER_WEB_SEARCH

_BASE_URL = "https://openrouter.ai/api/v1"


def _plugins() -> list[dict] | None:
    return [{"id": "web"}] if OPENROUTER_WEB_SEARCH else None


def _system_prompt(context: str) -> str:
    if context:
        return (
            "You are a personal knowledge assistant with access to the user's "
            "knowledge graph. Answer using ONLY the context below. "
            'If the answer is not present in the context, say '
            '"I don\'t have that in my notes yet" — do not use outside knowledge '
            "to fill in gaps.\n\n"
            f"Context from knowledge graph:\n{context}"
        )
    return (
        "You are a personal knowledge assistant. "
        "You have no relevant notes for this query. "
        "Answer from general knowledge and note that this isn't from the user's graph."
    )


def _build_messages(messages: list[dict], context: str) -> list[dict]:
    system = {"role": "system", "content": _system_prompt(context)}
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
    if plugins := _plugins():
        payload["plugins"] = plugins
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
    if plugins := _plugins():
        payload["plugins"] = plugins
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
