# app/main.py

from typing import Tuple, Optional, List

import anyio
from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import nltk

from app import database, services, models, queue as job_queue, settings
import logging
import uuid
import hashlib
import os


UPLOAD_CHUNK_SIZE = 1024 * 1024

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Serendipity RAG System")

app.mount("/static", StaticFiles(directory="frontend"), name="static")

@app.get("/")
async def read_index():
    return FileResponse('frontend/index.html')

@app.on_event("startup")
async def startup():
    try:
        async with database.connection():
            logger.info("Database connection pool initialized.")
    except Exception as e:
        logger.error(f"DB Init Failed: {e}")
        raise

    try:
        await job_queue.ping()
        logger.info("Redis queue connection established.")
    except Exception as e:
        logger.error(f"Redis Init Failed: {e}")
        raise
    
    # Ensure NLTK data is available
    try:
        nltk.data.find('tokenizers/punkt')
    except LookupError:
        logger.info("Downloading NLTK punkt tokenizer...")
        nltk.download("punkt", quiet=True)
        nltk.download("punkt_tab", quiet=True)


@app.on_event("shutdown")
async def shutdown():
    await database.close_pool()
    await job_queue.close()

@app.post("/ingest")
async def ingest_document(
    company: str = Form(..., alias="company"), # Frontend might still send 'company', mapping it to collection_name
    sector: Optional[str] = Form(None),
    stage: Optional[str] = Form(None),
    owner: str = Form("general"),
    file: UploadFile = File(...)
):
    """
    Upload a document and link it to a Collection.
    """
    # Map 'company' form field to 'collection_name'
    collection_name = company
    
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDFs allowed")
    
    logger.info(f"Queued ingestion for {file.filename} ({collection_name})")

    job_id = str(uuid.uuid4())
    original_filename = file.filename

    try:
        file_path, checksum = await _persist_upload_to_storage(job_id, file)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to store uploaded file: {exc}") from exc
    finally:
        await file.close()

    existing_doc = await database.get_document_by_checksum(checksum)
    if existing_doc:
        await anyio.to_thread.run_sync(_safe_remove_file, file_path)
        return {
            "status": "duplicate",
            "document_id": existing_doc
        }
        
    await database.create_ingestion_job(
        job_id, collection_name, sector, stage, owner, original_filename, checksum, source_type="pdf_upload"
    )

    payload = {
        "job_id": job_id,
        "file_path": file_path,
        "collection_name": collection_name,
        "sector": sector,
        "stage": stage,
        "owner": owner,
        "filename": original_filename,
        "checksum": checksum,
        "source_type": "pdf_upload",
    }

    try:
        await job_queue.enqueue_ingest_job(payload)
    except Exception as exc:
        await anyio.to_thread.run_sync(_safe_remove_file, file_path)
        await database.update_ingestion_job(
            job_id,
            status="failed",
            detail="Unable to enqueue ingestion job",
        )
        raise HTTPException(status_code=503, detail="Unable to enqueue ingestion job") from exc

    return {
        "status": "queued",
        "job_id": job_id
    }


@app.get("/ingest/{job_id}/status")
async def ingest_status(job_id: str):
    job = await database.get_ingestion_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

@app.post("/chat", response_model=models.ChatResponse)
async def chat(request: models.ChatRequest):
    process_thoughts = []

    # 1. Embed query (semantic)
    process_thoughts.append("Query embedded.")
    query_vec = await services.get_embedding(request.query)

    # 2. Semantic vector search
    semantic_candidates = await database.search_similar_chunks(
        query_vec,
        limit=settings.RAG_CANDIDATE_LIMIT,
        sector=request.filter_sector,
        stage=request.filter_stage,
        document_type=request.filter_document_type,
        entity_name=request.filter_entity,
        entity_type=request.filter_entity_type,
        chunk_type=request.filter_chunk_type,
        as_of_before=request.filter_as_of_before,
        as_of_after=request.filter_as_of_after,
    )
    process_thoughts.append(f"Vector Search: Retrieved {len(semantic_candidates)} chunks.")

    # 3. Lexical search
    lexical_candidates = await database.search_lexical_chunks(
        request.query,
        limit=settings.RAG_CANDIDATE_LIMIT,
        sector=request.filter_sector,
        stage=request.filter_stage,
        document_type=request.filter_document_type,
        entity_name=request.filter_entity,
        entity_type=request.filter_entity_type,
        chunk_type=request.filter_chunk_type,
        as_of_before=request.filter_as_of_before,
        as_of_after=request.filter_as_of_after,
    )
    process_thoughts.append(f"Lexical Search: Retrieved {len(lexical_candidates)} chunks.")

    # 4. Merge + deduplicate candidates by (document_id, content)
    seen = set()
    merged: List[models.Source] = []
    for src in semantic_candidates + lexical_candidates:

        key = (src.document_id, src.metadata.page_number, src.content[:200])
        if key in seen:
            continue
        seen.add(key)
        merged.append(src)

    if not merged:
        return models.ChatResponse(answer="No documents found.", sources=[], thoughts=process_thoughts)

    # 5. Initial similarity/rank filter
    
    candidates = merged
    
    # 6. Re-ranking
    reranked = []
    if settings.RAG_RERANK_ENABLED:
        process_thoughts.append("LLM Re-ranker: Evaluating candidate relevance.")
        reranked, rerank_logs = await services.rerank_sources(
            request.query,
            candidates,
            top_k=settings.RAG_RERANK_TOP_K,
        )
        process_thoughts.extend(rerank_logs)
    else:

        candidates.sort(key=lambda s: s.score, reverse=True)
        reranked = candidates[: settings.RAG_MAX_SOURCES]

    if not reranked:
        process_thoughts.append("Re-ranker (or fallback) found no suitable candidates.")
        return models.ChatResponse(answer="No relevant documents found.", sources=[], thoughts=process_thoughts)

    # 7. Generate answer
    process_thoughts.append(f"Generating answer from top {len(reranked)} sources.")
    answer = await services.generate_answer(request.query, reranked)

    return models.ChatResponse(answer=answer, sources=reranked, thoughts=process_thoughts)


async def _persist_upload_to_storage(job_id: str, upload: UploadFile) -> Tuple[str, str]:
    destination = os.path.join(settings.INGESTION_STORAGE_DIR, f"{job_id}.pdf")
    hasher = hashlib.sha256()
    try:
        async with await anyio.open_file(destination, "wb") as outfile:
            while True:
                chunk = await upload.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                await outfile.write(chunk)
                hasher.update(chunk)
    except Exception:
        _safe_remove_file(destination)
        raise
    return destination, hasher.hexdigest()


def _safe_remove_file(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
