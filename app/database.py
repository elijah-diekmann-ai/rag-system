# app/database.py

import os
from contextlib import asynccontextmanager
from typing import List, Optional, Dict, Any
from datetime import date

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.models import Source, SourceMetadata
from app import settings


user = os.getenv("POSTGRES_USER")
password = os.getenv("POSTGRES_PASSWORD")
host = os.getenv("DB_HOST")
db = os.getenv("POSTGRES_DB")

if not all([user, password, host, db]):
    raise RuntimeError("Database environment variables (POSTGRES_USER, POSTGRES_PASSWORD, DB_HOST, POSTGRES_DB) are not fully set")

DB_DSN = f"postgresql://{user}:{password}@{host}/{db}"

pool = AsyncConnectionPool(DB_DSN, open=False)


def _to_pgvector(vec: List[float]) -> str:
    """
    Format a Python list[float] as a pgvector literal, e.g. [0.1,0.2,...]
    """
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


async def init_pool() -> None:
    if pool.closed:
        await pool.open()


async def close_pool() -> None:
    if not pool.closed:
        await pool.close()


async def get_db_connection():
    await init_pool()
    conn = await pool.getconn()
    return conn


async def release_conn(conn) -> None:
    await pool.putconn(conn)


@asynccontextmanager
async def connection(commit: bool = False):
    conn = await get_db_connection()
    try:
        yield conn
        if commit:
            await conn.commit()
        else:
            await conn.rollback()
    except Exception:
        await conn.rollback()
        raise
    finally:
        await release_conn(conn)


async def get_or_create_document_type(code: str) -> int:
    async with connection(commit=True) as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT id FROM document_types WHERE code = %s", (code,))
            row = await cur.fetchone()
            if row:
                return row[0]

            await cur.execute(
                """
                INSERT INTO document_types (code, label)
                VALUES (%s, %s)
                RETURNING id
                """,
                (code, code.replace("_", " ").title()),
            )
            row = await cur.fetchone()
            return row[0]


async def get_or_create_entity(entity_type: str, name: str, external_id: Optional[str]) -> int:
    async with connection(commit=True) as conn:
        async with conn.cursor() as cur:
            # Check if exists using the same effective key logic as the unique index
            # Index: (entity_type, COALESCE(external_id, name))
            effective_key = external_id if external_id else name
            
            await cur.execute(
                "SELECT id FROM entities WHERE entity_type = %s AND COALESCE(external_id, name) = %s",
                (entity_type, effective_key),
            )
            row = await cur.fetchone()
            if row:
                return row[0]

            # Insert (fallback to letting DB raise UniqueViolation if race condition occurs)
            await cur.execute(
                """
                INSERT INTO entities (entity_type, name, external_id)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (entity_type, name, external_id),
            )
            row = await cur.fetchone()
            return row[0]


async def link_document_entity(document_id: int, entity_id: int, role: str) -> None:
    async with connection(commit=True) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO document_entities (document_id, entity_id, role)
                VALUES (%s, %s, %s)
                ON CONFLICT (document_id, entity_id, role) DO NOTHING
                """,
                (document_id, entity_id, role),
            )


async def create_ingestion_job(
    job_id: str,
    collection_name: str,
    sector: Optional[str],
    stage: Optional[str],
    owner: str,
    filename: str,
    checksum: str,
    source_type: str,
):
    async with connection(commit=True) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO ingestion_jobs (
                    id, collection_name, sector, stage,
                    owner, filename, checksum, source_type, status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'queued')
                """,
                (job_id, collection_name, sector, stage, owner, filename, checksum, source_type),
            )


async def update_ingestion_job(job_id: str, *, status: Optional[str] = None, detail: Optional[str] = None,
                               chunks_processed: Optional[int] = None, collection_id: Optional[int] = None,
                               document_id: Optional[int] = None):
    fields = []
    params: List[Any] = []

    if status is not None:
        fields.append("status = %s")
        params.append(status)
    if detail is not None:
        fields.append("detail = %s")
        params.append(detail)
    if chunks_processed is not None:
        fields.append("chunks_processed = %s")
        params.append(chunks_processed)
    if collection_id is not None:
        fields.append("collection_id = %s")
        params.append(collection_id)
    if document_id is not None:
        fields.append("document_id = %s")
        params.append(document_id)

    if not fields:
        return

    fields.append("updated_at = CURRENT_TIMESTAMP")

    query = f"UPDATE ingestion_jobs SET {', '.join(fields)} WHERE id = %s"
    params.append(job_id)

    async with connection(commit=True) as conn:
        async with conn.cursor() as cur:
            await cur.execute(query, params)


async def get_ingestion_job(job_id: str) -> Optional[Dict[str, Any]]:
    async with connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM ingestion_jobs WHERE id = %s", (job_id,))
            row = await cur.fetchone()
            return dict(row) if row else None


async def create_collection_if_not_exists(name: str, sector: Optional[str], stage: Optional[str], owner: str) -> int:
    async with connection(commit=True) as conn:
        async with conn.cursor() as cur:
            # Check if exists
            await cur.execute("SELECT id FROM collections WHERE name = %s", (name,))
            res = await cur.fetchone()
            if res:
                return res[0]

            # Try insert, handle race condition via ON CONFLICT
            await cur.execute(
                """
                INSERT INTO collections (name, sector, stage, owner)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (name) DO UPDATE SET sector = COALESCE(EXCLUDED.sector, collections.sector), stage = COALESCE(EXCLUDED.stage, collections.stage)
                RETURNING id
                """,
                (name, sector, stage, owner)
            )
            new_id_row = await cur.fetchone()
            return new_id_row[0]


async def save_document_metadata(
    collection_id: int,
    filename: str,
    checksum: str,
    document_type_id: Optional[int] = None,
    source_system: Optional[str] = None,
    source_external_id: Optional[str] = None,
    source_url: Optional[str] = None,
    source_path: Optional[str] = None,
    as_of_date: Optional[str] = None,
    reporting_period: Optional[str] = None,
) -> int:
    async with connection(commit=True) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO documents (
                    collection_id, filename, checksum,
                    document_type_id, source_system, source_external_id,
                    source_url, source_path, as_of_date, reporting_period
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (checksum) DO UPDATE SET
                    filename = EXCLUDED.filename,
                    document_type_id = COALESCE(EXCLUDED.document_type_id, documents.document_type_id),
                    source_system = COALESCE(EXCLUDED.source_system, documents.source_system),
                    source_external_id = COALESCE(EXCLUDED.source_external_id, documents.source_external_id),
                    source_url = COALESCE(EXCLUDED.source_url, documents.source_url),
                    source_path = COALESCE(EXCLUDED.source_path, documents.source_path),
                    as_of_date = COALESCE(EXCLUDED.as_of_date, documents.as_of_date),
                    reporting_period = COALESCE(EXCLUDED.reporting_period, documents.reporting_period)
                RETURNING id
                """,
                (
                    collection_id,
                    filename,
                    checksum,
                    document_type_id,
                    source_system,
                    source_external_id,
                    source_url,
                    source_path,
                    as_of_date,
                    reporting_period,
                ),
            )
            row = await cur.fetchone()
            return row[0]


async def get_document_by_checksum(checksum: str) -> Optional[int]:
    async with connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT id FROM documents WHERE checksum = %s", (checksum,))
            res = await cur.fetchone()
            return res[0] if res else None


async def get_collection_id_for_document(document_id: int) -> Optional[int]:
    async with connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT collection_id FROM documents WHERE id = %s",
                (document_id,),
            )
            row = await cur.fetchone()
            return row[0] if row else None


async def insert_chunks(doc_id: int, chunks_data: List[Dict[str, Any]], embeddings: List[List[float]]) -> int:
    """
    Insert semantic chunks and their embeddings.
    chunks_data: List of dicts containing 'text' and 'page'
    embeddings: Corresponding embeddings
    """
    async with connection(commit=True) as conn:
        async with conn.cursor() as cur:
            data = []
            for i, (chunk, vec) in enumerate(zip(chunks_data, embeddings)):
                data.append(
                    (
                        doc_id,
                        i,
                        chunk["text"],
                        _to_pgvector(vec),
                        chunk.get("page"),
                        chunk.get("chunk_type", "paragraph"),
                        chunk.get("section_title"),
                        chunk.get("section_path"),
                        chunk.get("table_name"),
                    )
                )

            if not data:
                return 0

            await cur.executemany(
                """
                INSERT INTO chunks (
                    document_id,
                    chunk_index,
                    sentence_text,
                    embedding,
                    page_number,
                    chunk_type,
                    section_title,
                    section_path,
                    table_name
                )
                VALUES (%s, %s, %s, %s::vector, %s, %s, %s, %s, %s)
                """,
                data,
            )
            return len(data)


async def search_similar_chunks(
    query_embedding: List[float],
    limit: int = 5,
    sector: Optional[str] = None,
    stage: Optional[str] = None,
    document_type: Optional[str] = None,
    entity_name: Optional[str] = None,
    entity_type: Optional[str] = None,
    chunk_type: Optional[str] = None,
    as_of_before: Optional[date] = None,
    as_of_after: Optional[date] = None,
) -> List[Source]:
    window = max(0, settings.RAG_WINDOW_CHUNKS)
    
    async with connection() as conn:
        async with conn.cursor() as cur:
            sql = """
                WITH top_chunks AS (
                    SELECT c.id, c.document_id, c.chunk_index, (c.embedding <=> %s::vector) as distance
                    FROM chunks c
                    JOIN documents d ON c.document_id = d.id
                    JOIN collections col ON d.collection_id = col.id
                    LEFT JOIN document_types dt ON d.document_type_id = dt.id
                    LEFT JOIN document_entities de ON d.id = de.document_id
                    LEFT JOIN entities e ON de.entity_id = e.id
                    WHERE 1=1
            """
            params: List[Any] = [_to_pgvector(query_embedding)]

            if sector:
                sql += " AND col.sector = %s"
                params.append(sector)

            if stage:
                sql += " AND col.stage = %s"
                params.append(stage)

            if document_type:
                sql += " AND dt.code = %s"
                params.append(document_type)

            if entity_name:
                sql += " AND e.name = %s"
                params.append(entity_name)

            if entity_type:
                sql += " AND e.entity_type = %s"
                params.append(entity_type)

            if chunk_type:
                sql += " AND c.chunk_type = %s"
                params.append(chunk_type)
            
            if as_of_before:
                sql += " AND d.as_of_date <= %s"
                params.append(as_of_before)

            if as_of_after:
                sql += " AND d.as_of_date >= %s"
                params.append(as_of_after)

            sql += f"""
                    ORDER BY distance ASC LIMIT %s
                )
                SELECT
                    tc.document_id,
                    d.filename,
                    (
                        SELECT string_agg(c2.sentence_text, ' ' ORDER BY c2.chunk_index)
                        FROM chunks c2
                        WHERE c2.document_id = tc.document_id
                          AND c2.chunk_index BETWEEN tc.chunk_index - {window} AND tc.chunk_index + {window}
                    ) as window_context,
                    tc.distance,
                    col.name,
                    col.sector,
                    col.stage,
                    (
                        SELECT c3.page_number 
                        FROM chunks c3 
                        WHERE c3.id = tc.id
                    ) as page_number,
                    dt.code,
                    d.as_of_date,
                    c.section_title,
                    c.section_path,
                    c.chunk_type
                FROM top_chunks tc
                JOIN documents d ON tc.document_id = d.id
                JOIN collections col ON d.collection_id = col.id
                LEFT JOIN document_types dt ON d.document_type_id = dt.id
                JOIN chunks c ON c.id = tc.id
                ORDER BY tc.distance ASC
            """
            params.append(limit)

            await cur.execute(sql, params)
            rows = await cur.fetchall()

            return [
                Source(
                    document_id=row[0],
                    filename=row[1],
                    content=row[2],
                    score=max(0.0, 1 - row[3]),
                    metadata=SourceMetadata(
                        name=row[4],
                        sector=row[5],
                        stage=row[6],
                        page_number=row[7],
                        document_type=row[8],
                        as_of_date=row[9],
                        section_title=row[10],
                        section_path=row[11],
                        chunk_type=row[12],
                    )
                )
                for row in rows
            ]


async def search_lexical_chunks(
    query_text: str,
    limit: int = 5,
    sector: Optional[str] = None,
    stage: Optional[str] = None,
    document_type: Optional[str] = None,
    entity_name: Optional[str] = None,
    entity_type: Optional[str] = None,
    chunk_type: Optional[str] = None,
    as_of_before: Optional[date] = None,
    as_of_after: Optional[date] = None,
) -> List[Source]:
    """
    Lexical search using Postgres full-text search (tsvector).
    """
    async with connection() as conn:
        async with conn.cursor() as cur:
            sql = """
                WITH matches AS (
                    SELECT
                        c.id,
                        c.document_id,
                        c.chunk_index,
                        ts_rank_cd(c.text_tsv, plainto_tsquery('english', %s)) AS rank
                    FROM chunks c
                    JOIN documents d ON c.document_id = d.id
                    JOIN collections col ON d.collection_id = col.id
                    LEFT JOIN document_types dt ON d.document_type_id = dt.id
                    LEFT JOIN document_entities de ON d.id = de.document_id
                    LEFT JOIN entities e ON de.entity_id = e.id
                    WHERE c.text_tsv @@ plainto_tsquery('english', %s)
            """
            params: List[Any] = [query_text, query_text]

            if sector:
                sql += " AND col.sector = %s"
                params.append(sector)

            if stage:
                sql += " AND col.stage = %s"
                params.append(stage)

            if document_type:
                sql += " AND dt.code = %s"
                params.append(document_type)

            if entity_name:
                sql += " AND e.name = %s"
                params.append(entity_name)

            if entity_type:
                sql += " AND e.entity_type = %s"
                params.append(entity_type)

            if chunk_type:
                sql += " AND c.chunk_type = %s"
                params.append(chunk_type)

            if as_of_before:
                sql += " AND d.as_of_date <= %s"
                params.append(as_of_before)

            if as_of_after:
                sql += " AND d.as_of_date >= %s"
                params.append(as_of_after)

            sql += """
                ORDER BY rank DESC
                LIMIT %s
            )
            SELECT
                m.document_id,
                d.filename,
                (
                    SELECT string_agg(c2.sentence_text, ' ' ORDER BY c2.chunk_index)
                    FROM chunks c2
                    WHERE c2.document_id = m.document_id
                      AND c2.chunk_index BETWEEN m.chunk_index - %s AND m.chunk_index + %s
                ) AS window_context,
                m.rank,
                col.name,
                col.sector,
                col.stage,
                (
                    SELECT c3.page_number
                    FROM chunks c3
                    WHERE c3.id = m.id
                ) AS page_number,
                dt.code,
                d.as_of_date,
                c.section_title,
                c.section_path,
                c.chunk_type
            FROM matches m
            JOIN chunks c ON c.id = m.id
            JOIN documents d ON m.document_id = d.id
            JOIN collections col ON d.collection_id = col.id
            LEFT JOIN document_types dt ON d.document_type_id = dt.id
            ORDER BY m.rank DESC
            """

            params.extend([limit, settings.RAG_WINDOW_CHUNKS, settings.RAG_WINDOW_CHUNKS])

            await cur.execute(sql, params)
            rows = await cur.fetchall()

            sources: List[Source] = []
            for row in rows:
                sources.append(
                    Source(
                        document_id=row[0],
                        filename=row[1],
                        content=row[2],
                        score=float(row[3]),  # rank
                        metadata=SourceMetadata(
                            name=row[4],
                            sector=row[5],
                            stage=row[6],
                            page_number=row[7],
                            document_type=row[8],
                            as_of_date=row[9],
                            section_title=row[10],
                            section_path=row[11],
                            chunk_type=row[12],
                        ),
                    )
                )
            return sources
