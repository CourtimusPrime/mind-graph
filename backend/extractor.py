import json
import httpx
from backend.config import OPENROUTER_API_KEY, OPENROUTER_MODEL

_BASE_URL = "https://openrouter.ai/api/v1"

_SYSTEM_PROMPT = """You are a knowledge graph extraction engine.
Given a piece of text, extract all meaningful entities and the relationships between them.

Respond ONLY with valid JSON in this exact structure:
{
  "nodes": [
    {"name": "...", "type": "Concept|Project|Note|Tag", "content": "optional description"}
  ],
  "relationships": [
    {"source": "node name", "target": "node name", "type": "RELATIONSHIP_TYPE"}
  ]
}

Guidelines:
- Use "Concept" for ideas, topics, technologies, people, places.
- Use "Project" for named projects or initiatives.
- Use "Note" for specific facts, observations, or statements.
- Use "Tag" for categories or labels.
- Relationship types should be UPPER_SNAKE_CASE (e.g. RELATED_TO, PART_OF, USED_BY).
- Keep names concise and canonical (prefer "Machine Learning" over "ML").
- Only extract entities explicitly mentioned or strongly implied.
- Return {"nodes": [], "relationships": []} if nothing is extractable."""


async def extract_entities(text: str, project_hint: str | None = None) -> dict:
    """
    Use OpenRouter to extract a structured node/relationship graph from *text*.
    Returns {"nodes": [...], "relationships": [...]}.
    """
    prefix = f'[Context: this note relates to the project "{project_hint}"]\n\n' if project_hint else ""
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prefix + text},
        ],
        "response_format": {"type": "json_object"},
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
        content = response.json()["choices"][0]["message"]["content"]

    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        # Graceful degradation — return empty graph rather than crashing
        result = {"nodes": [], "relationships": []}

    return result
