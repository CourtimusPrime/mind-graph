import os
from dotenv import load_dotenv

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")

EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "768"))
DEDUP_THRESHOLD = float(os.getenv("DEDUP_THRESHOLD", "0.92"))

OPENROUTER_WEB_SEARCH = os.getenv("OPENROUTER_WEB_SEARCH", "false").lower() == "true"

# Extraction error tracking
EXTRACTION_RETRY_LIMIT = int(os.getenv("EXTRACTION_RETRY_LIMIT", "3"))

# Composite retrieval weights (must sum to ~1.0)
RETRIEVAL_SEMANTIC_WEIGHT   = float(os.getenv("RETRIEVAL_SEMANTIC_WEIGHT",   "0.60"))
RETRIEVAL_CENTRALITY_WEIGHT = float(os.getenv("RETRIEVAL_CENTRALITY_WEIGHT", "0.25"))
RETRIEVAL_RECENCY_WEIGHT    = float(os.getenv("RETRIEVAL_RECENCY_WEIGHT",    "0.15"))

# RAG context character budget
RAG_CONTEXT_CHARS = int(os.getenv("RAG_CONTEXT_CHARS", "2500"))

# Community detection: rerun after this many new nodes
COMMUNITY_RERUN_THRESHOLD = int(os.getenv("COMMUNITY_RERUN_THRESHOLD", "10"))

# Eval API (disabled by default for security)
ENABLE_EVAL = os.getenv("ENABLE_EVAL", "false").lower() == "true"
