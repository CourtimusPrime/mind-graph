import httpx
from backend.config import OLLAMA_BASE_URL, OLLAMA_EMBED_MODEL


async def embed(text: str) -> list[float]:
    """Generate an embedding vector for the given text using Ollama."""
    url = f"{OLLAMA_BASE_URL}/api/embeddings"
    payload = {"model": OLLAMA_EMBED_MODEL, "prompt": text}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            return response.json()["embedding"]
    except httpx.ConnectError:
        raise RuntimeError(
            f"Cannot connect to Ollama at {OLLAMA_BASE_URL}. "
            "Is Ollama running? Try: ollama serve"
        )
