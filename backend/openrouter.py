import httpx
from backend.config import OPENROUTER_API_KEY, OPENROUTER_MODEL

_BASE_URL = "https://openrouter.ai/api/v1"


async def chat(messages: list[dict], context: str = "") -> str:
    """
    Send a conversation to OpenRouter and return the assistant's reply.

    If *context* is non-empty it is prepended as a system message so the
    model can ground its answer in the knowledge graph.
    """
    system_messages = []
    if context:
        system_messages.append({
            "role": "system",
            "content": (
                "You are a personal knowledge assistant. Use the following "
                "context from the user's knowledge graph to inform your "
                f"response:\n\n{context}"
            ),
        })

    payload = {
        "model": OPENROUTER_MODEL,
        "messages": system_messages + messages,
    }

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{_BASE_URL}/chat/completions",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]
