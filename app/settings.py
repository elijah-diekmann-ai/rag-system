import os
from pathlib import Path


INGESTION_STORAGE_DIR = os.getenv("INGESTION_STORAGE_DIR", "/tmp/ingest_jobs")
Path(INGESTION_STORAGE_DIR).mkdir(parents=True, exist_ok=True)

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
REDIS_QUEUE_NAME = os.getenv("REDIS_QUEUE_NAME", "vc_rag:ingest_jobs")

# ---- RAG configuration ----

# How many candidates to pull from pgvector before re-ranking
RAG_CANDIDATE_LIMIT = int(os.getenv("RAG_CANDIDATE_LIMIT", "20"))

# Final number of sources to pass to the answer generator
RAG_MAX_SOURCES = int(os.getenv("RAG_MAX_SOURCES", "5"))

# Minimum similarity score (1 - distance) to consider a hit usable
RAG_MIN_SCORE = float(os.getenv("RAG_MIN_SCORE", "0.3"))

# Minimum score for reranker to consider a hit usable
RAG_MIN_RERANK_SCORE = float(os.getenv("RAG_MIN_RERANK_SCORE", "0.3"))

# Context size controls
RAG_MAX_CHARS_PER_SOURCE = int(os.getenv("RAG_MAX_CHARS_PER_SOURCE", "1500"))
RAG_MAX_CONTEXT_CHARS = int(os.getenv("RAG_MAX_CONTEXT_CHARS", "8000"))

# Re-ranking controls
RAG_RERANK_ENABLED = os.getenv("RAG_RERANK_ENABLED", "1") == "1"
RAG_RERANK_TOP_K = int(os.getenv("RAG_RERANK_TOP_K", "5"))
RAG_RERANK_SNIPPET_CHARS = int(os.getenv("RAG_RERANK_SNIPPET_CHARS", "600"))

# ---- Chunking configuration ----

# Max sentences per semantic chunk
CHUNK_MAX_SENTENCES = int(os.getenv("CHUNK_MAX_SENTENCES", "4"))

# Max characters per chunk (hard bound)
CHUNK_MAX_CHARS = int(os.getenv("CHUNK_MAX_CHARS", "600"))

# Number of sentences to overlap between consecutive chunks
CHUNK_SENTENCE_OVERLAP = int(os.getenv("CHUNK_SENTENCE_OVERLAP", "1"))

# How many neighboring chunks to include around the hit when building window_context
# With better chunking you can start with 0 or 1.
RAG_WINDOW_CHUNKS = int(os.getenv("RAG_WINDOW_CHUNKS", "1"))
