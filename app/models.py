# app/models.py

from pydantic import BaseModel
from typing import List, Optional
from datetime import date


class SourceMetadata(BaseModel):
    name: str
    sector: Optional[str] = None
    stage: Optional[str] = None
    page_number: Optional[int] = None

    # New metadata fields
    document_type: Optional[str] = None
    entities: List[str] = []
    as_of_date: Optional[date] = None
    section_title: Optional[str] = None
    section_path: Optional[str] = None
    chunk_type: Optional[str] = None


class Source(BaseModel):
    document_id: int
    filename: str
    content: str
    score: float
    metadata: SourceMetadata


class ChatRequest(BaseModel):
    query: str
    filter_sector: Optional[str] = None
    filter_stage: Optional[str] = None

    # New filters
    filter_document_type: Optional[str] = None
    filter_entity: Optional[str] = None
    filter_entity_type: Optional[str] = None  # e.g. 'company', 'fund'
    filter_as_of_before: Optional[date] = None
    filter_as_of_after: Optional[date] = None
    filter_chunk_type: Optional[str] = None  # e.g. 'table' for numeric queries


class ChatResponse(BaseModel):
    answer: str
    sources: List[Source]
    thoughts: List[str] = []
