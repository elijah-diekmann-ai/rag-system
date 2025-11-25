# app/services.py

import os
import json
from typing import List, Tuple
import logging

from openai import AsyncOpenAI

from app.models import Source
from app import settings


logger = logging.getLogger(__name__)

# OpenRouter configuration
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_HTTP_REFERER = os.getenv("OPENROUTER_HTTP_REFERER", "http://localhost:8000")
OPENROUTER_APP_TITLE = os.getenv("OPENROUTER_APP_TITLE", "VC RAG System")

if not OPENROUTER_API_KEY:
    raise RuntimeError("OPENROUTER_API_KEY is not set")

# Async client prevents event loop blocking during network I/O
client = AsyncOpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url=OPENROUTER_BASE_URL,
    default_headers={
        "HTTP-Referer": OPENROUTER_HTTP_REFERER,
        "X-Title": OPENROUTER_APP_TITLE,
    },
)


async def get_embedding(text: str) -> List[float]:
    text = text.replace("\n", " ")
    response = await client.embeddings.create(
        input=[text],
        model="openai/text-embedding-3-small"
    )
    return response.data[0].embedding


EMBED_BATCH_SIZE = 80  # conservative batch size to stay under payload limits


async def get_batch_embeddings(texts: List[str]) -> List[List[float]]:
    if not texts:
        return []

    embeddings: List[List[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[start:start + EMBED_BATCH_SIZE]
        response = await client.embeddings.create(
            input=batch,
            model="openai/text-embedding-3-small"
        )
        sorted_data = sorted(response.data, key=lambda x: x.index)
        embeddings.extend([item.embedding for item in sorted_data])

    return embeddings


async def rerank_sources(query: str, sources: List[Source], top_k: int) -> Tuple[List[Source], List[str]]:
    """
    Returns: (List[Source], List[str]) -> The filtered sources AND the log lines.
    """
    logs = []
    
    if not sources:
        return [], ["No sources provided to re-ranker."]

    logs.append(f"Re-ranking {len(sources)} candidates.")

    # Limit how many candidates we send to the model
    max_candidates = min(len(sources), settings.RAG_CANDIDATE_LIMIT)
    candidates = sources[:max_candidates]

    payload_candidates = []
    for idx, src in enumerate(candidates):
        payload_candidates.append(
            {
                "id": idx,
                "filename": src.filename,
                "collection": src.metadata.name,
                "sector": src.metadata.sector,
                "stage": src.metadata.stage,
                "content": src.content[: settings.RAG_RERANK_SNIPPET_CHARS],
            }
        )

    system_prompt = """
    You are ranking passages. Assign a relevance score between 0.0 and 1.0.
    Return JSON: { "results": [ {"id": int, "score": float} ] }
    """

    message_payload = {
        "question": query,
        "candidates": payload_candidates,
    }

    try:
        response = await client.chat.completions.create(
            model="openai/gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(message_payload)},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        data = json.loads(content)
    except Exception as e:
        logs.append(f"Refinement skipped ({str(e)}). Using original order.")
        return candidates[:top_k], logs

    results = data.get("results", [])
    id_to_score = {}

    for item in results:
        try:
            cid = int(item["id"])
            score = float(item["score"])
            if 0 <= cid < len(candidates):
                id_to_score[cid] = max(0.0, min(1.0, score))
        except (KeyError, ValueError, TypeError):
            continue

    ordered_ids = sorted(id_to_score.keys(), key=lambda i: id_to_score[i], reverse=True)

    reranked: List[Source] = []
    for cid in ordered_ids:
        score = id_to_score[cid]
        doc_name = candidates[cid].filename
        page_num = candidates[cid].metadata.page_number
        
        # Construct a user-friendly log message
        location_info = f"Page {page_num}" if page_num else "excerpt"
        
        if score < settings.RAG_MIN_RERANK_SCORE:
            logs.append(f"Rejected {location_info} ('{doc_name}') | Score: {score:.2f}")
            continue
            
        logs.append(f"Selected {location_info} ('{doc_name}') | Score: {score:.2f}")
        reranked.append(candidates[cid])
        if len(reranked) >= top_k:
            break
            
    return reranked, logs


async def generate_answer(query: str, sources: List[Source]) -> str:
    """
    Generate an answer strictly from the given sources.
    Uses claim-level JSON so we can enforce grounding programmatically.
    """
    if not sources:
        return "No documents were supplied to answer this question."

    # Build bounded context block
    context_block = ""
    total_chars = 0
    for i, src in enumerate(sources, 1):
        snippet = src.content[: settings.RAG_MAX_CHARS_PER_SOURCE]
        # Include page number in the context header if available
        page_info = f" (Page {src.metadata.page_number})" if src.metadata.page_number else ""
        addition = f"Source {i} (File: {src.filename}{page_info}):\n{snippet}\n\n"
        
        if total_chars + len(addition) > settings.RAG_MAX_CONTEXT_CHARS:
            break
            
        context_block += addition
        total_chars += len(addition)

    system_prompt = """
You are an expert internal analyst.
Answer strictly using the provided corporate knowledge base.
Break your answer into atomic factual claims. Only include a claim if it is clearly
supported by at least one source.

When answering questions about where information is found (e.g. "which page"),
refer to the Page number provided in the Source header (e.g. "Page 18").
If the page number is not available, explicitly state that the page is unknown.

When you reference numeric values (amounts, figures, percentages, dates, counts):
- Copy the values exactly as they appear in the sources.
- Do NOT round, re-scale, or normalize numbers.
- Do NOT infer or estimate numbers that are not explicitly present.
- If a numeric value is not explicitly present, omit that claim.

Return a JSON object:
{
  "claims": [
    {
      "text": "single concise factual statement",
      "sources": [1, 2]
    }
  ]
}

Rules:
- "text" must be self-contained and precise.
- "sources" must reference the numeric Source N from the context (1-based).
- Do NOT include a claim if it is not clearly supported by the sources.
- Do NOT use outside knowledge beyond the provided sources.
- If the sources do not answer the question, return "claims": [].
"""

    response = await client.chat.completions.create(
        model="openai/gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Context:\n{context_block}\n\nQuestion: {query}"},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        # Fallback: return raw content if parsing failed
        return content

    claims = payload.get("claims", [])
    if not isinstance(claims, list) or not claims:
        return "The documents do not provide a clear, supported answer to this question."

    answer_lines = []
    citation_lines = []

    for idx, claim in enumerate(claims, 1):
        text = str(claim.get("text", "")).strip()
        sources_idx = claim.get("sources", [])

        if not text or not isinstance(sources_idx, list) or not sources_idx:
            # Hard guardrail: ignore claims without explicit supporting sources
            continue

        answer_lines.append(text)

        # Build human-readable citation line
        src_labels = []
        for s_idx in sources_idx:
            try:
                s_int = int(s_idx)
            except (TypeError, ValueError):
                continue
            
            if 1 <= s_int <= len(sources):
                src_obj = sources[s_int - 1]
                src_labels.append(f"Source {s_int} ({src_obj.filename})")

        if src_labels:
            citation_lines.append(f"- {text}  —  supported by {', '.join(src_labels)}")

    if not answer_lines:
        return "The documents do not provide a clear, supported answer to this question."

    answer_text = " ".join(answer_lines)
    if citation_lines:
        # Add newlines for better separation
        answer_text += "\n\nCitations:\n" + "\n".join(citation_lines)

    return answer_text
