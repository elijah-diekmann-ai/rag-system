# app/tasks.py

import os
import logging
from typing import List, Optional

import anyio

from . import utils, services, database
from .ingestion_pipeline import IngestionContext, get_adapter_for_source_type, run_ingestion

logger = logging.getLogger(__name__)


async def process_ingestion_job(
    job_id: str,
    file_path: str,
    collection_name: str,
    sector: Optional[str],
    stage: Optional[str],
    owner: str,
    filename: str,
    checksum: str,
    source_type: str = "pdf_upload",
    document_type_code: Optional[str] = None,
    as_of_date: Optional[str] = None,
    reporting_period: Optional[str] = None,
    source_system: str = "upload",
    source_external_id: Optional[str] = None,
    source_url: Optional[str] = None,
    source_path: Optional[str] = None,
):
    """
    Background task that performs the heavy ingestion workflow.
    """
    await database.update_ingestion_job(job_id, status="processing")

    try:
        ctx = IngestionContext(
            job_id=job_id,
            collection_name=collection_name,
            sector=sector,
            stage=stage,
            owner=owner,
            filename=filename,
            checksum=checksum,
            source_type=source_type,
            file_path=file_path,
            source_system=source_system,
            source_external_id=source_external_id,
            source_url=source_url,
            source_path=source_path,
            document_type_code=document_type_code,
            as_of_date=as_of_date,
            reporting_period=reporting_period,
        )

        adapter = get_adapter_for_source_type(source_type)
        inserted_count = await run_ingestion(ctx, adapter)

        # Look up collection_id and document_id for job tracking
        job_doc = await database.get_document_by_checksum(checksum)
        collection_id = await database.get_collection_id_for_document(job_doc) if job_doc else None

        await database.update_ingestion_job(
            job_id,
            status="completed",
            detail="Ingestion completed successfully",
            chunks_processed=inserted_count,
            collection_id=collection_id,
            document_id=job_doc,
        )
        logger.info("Ingestion job %s completed", job_id)
    except Exception as exc:
        logger.exception("Ingestion job %s failed", job_id)
        await database.update_ingestion_job(
            job_id,
            status="failed",
            detail=str(exc),
        )
    finally:
        await anyio.to_thread.run_sync(_safe_remove_file, file_path)


def _safe_remove_file(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        logger.warning("Temporary file %s could not be removed", path)
