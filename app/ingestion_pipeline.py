# app/ingestion_pipeline.py

from dataclasses import dataclass
from typing import Protocol, List, Dict, Any, Optional

from app import utils, services, database


@dataclass
class IngestionContext:
    job_id: str
    collection_name: str
    sector: Optional[str]
    stage: Optional[str]
    owner: str
    filename: str
    checksum: str
    source_type: str
    file_path: Optional[str] = None
    source_system: str = "upload"
    source_external_id: Optional[str] = None
    source_url: Optional[str] = None
    source_path: Optional[str] = None
    document_type_code: Optional[str] = None  # e.g. 'BOARD_PACK'
    as_of_date: Optional[str] = None          # parse upstream if available
    reporting_period: Optional[str] = None


class IngestionAdapter(Protocol):
    async def extract_elements(self, ctx: IngestionContext) -> List[Dict[str, Any]]:
        """
        Return a list of structured elements suitable for utils.build_chunks().
        Each element must have: 'type', 'text', 'page'.
        """
        ...


class PdfUploadAdapter:
    async def extract_elements(self, ctx: IngestionContext) -> List[Dict[str, Any]]:
        if not ctx.file_path:
            raise ValueError("file_path is required for PdfUploadAdapter")
        return await utils.extract_text_from_pdf_async(ctx.file_path)


async def run_ingestion(ctx: IngestionContext, adapter: IngestionAdapter) -> int:
    """
    Execute the core ingestion pipeline:
    elements -> chunks -> embeddings -> DB

    Returns the number of chunks inserted.
    """
    # 1. Extract structured elements
    elements = await adapter.extract_elements(ctx)
    if not elements:
        raise ValueError("Unable to extract any content from source")

    # 2. Build semantic chunks
    chunks_data = utils.build_chunks(elements)
    if not chunks_data:
        raise ValueError("Unable to create semantic chunks from content")

    # 3. Embeddings
    chunk_texts = [c["text"] for c in chunks_data]
    embeddings = await services.get_batch_embeddings(chunk_texts)

    # 4. Persist to DB
    collection_id = await database.create_collection_if_not_exists(
        ctx.collection_name, ctx.sector, ctx.stage, ctx.owner
    )

    # Document type support
    document_type_id = None
    if ctx.document_type_code:
        document_type_id = await database.get_or_create_document_type(ctx.document_type_code)

    doc_id = await database.save_document_metadata(
        collection_id=collection_id,
        filename=ctx.filename,
        checksum=ctx.checksum,
        document_type_id=document_type_id,
        source_system=ctx.source_system,
        source_external_id=ctx.source_external_id,
        source_url=ctx.source_url,
        source_path=ctx.source_path,
        as_of_date=ctx.as_of_date,
        reporting_period=ctx.reporting_period,
    )

    inserted_count = await database.insert_chunks(doc_id, chunks_data, embeddings)

    # Optional entity wiring: treat collection_name as a company entity
    entity_id = await database.get_or_create_entity(
        entity_type="company",
        name=ctx.collection_name,
        external_id=None,
    )

    await database.link_document_entity(
        document_id=doc_id,
        entity_id=entity_id,
        role="subject_company",
    )

    return inserted_count


def get_adapter_for_source_type(source_type: str) -> IngestionAdapter:
    if source_type == "pdf_upload":
        return PdfUploadAdapter()
    
    # Additional source types will use this factory
    raise ValueError(f"Unknown source_type: {source_type}")

