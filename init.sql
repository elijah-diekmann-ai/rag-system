
-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- 1. Collections Table (Metadata Layer)
CREATE TABLE IF NOT EXISTS collections (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    sector TEXT,
    stage TEXT,
    owner TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Document Types (NEW)
CREATE TABLE IF NOT EXISTS document_types (
    id SERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,   -- e.g. 'IC_MEMO', 'BOARD_PACK'
    label TEXT NOT NULL
);

-- 3. Entities (NEW)
CREATE TABLE IF NOT EXISTS entities (
    id SERIAL PRIMARY KEY,
    entity_type TEXT NOT NULL,   -- 'company', 'fund', 'deal', 'contact'
    name TEXT NOT NULL,
    external_id TEXT,
    UNIQUE(entity_type, external_id)
);

-- Add unique index for deduplication when external_id is NULL
CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_type_key
ON entities (entity_type, COALESCE(external_id, name));

-- 4. Documents Table (EXTENDED)
-- Note: In a live migration, use ALTER TABLE. This definition is for fresh installs.
CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    collection_id INTEGER REFERENCES collections(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    checksum TEXT UNIQUE,
    upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- New metadata
    document_type_id INTEGER REFERENCES document_types(id),
    title TEXT,
    as_of_date DATE,
    reporting_period TEXT,
    version INTEGER DEFAULT 1,
    is_current BOOLEAN DEFAULT TRUE,

    -- Source & lineage
    source_system TEXT,        -- 'upload', 'gmail', 'google_drive', 'crm'
    source_external_id TEXT,   -- e.g. Gmail message ID
    source_url TEXT,
    source_path TEXT
);

-- FK Index for documents.collection_id
CREATE INDEX IF NOT EXISTS idx_documents_collection_id ON documents(collection_id);

-- 5. Document Entities (NEW)
CREATE TABLE IF NOT EXISTS document_entities (
    document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
    entity_id INTEGER REFERENCES entities(id) ON DELETE CASCADE,
    role TEXT,                   -- 'subject_company', 'fund', etc.
    PRIMARY KEY (document_id, entity_id, role)
);

-- 6. Chunks Table (EXTENDED)
CREATE TABLE IF NOT EXISTS chunks (
    id SERIAL PRIMARY KEY,
    document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    sentence_text TEXT NOT NULL,
    embedding vector(1536),
    page_number INTEGER,

    -- New fields
    chunk_type TEXT DEFAULT 'paragraph',      -- 'paragraph' | 'table' | 'header'
    section_title TEXT,
    section_path TEXT,
    table_name TEXT,
    text_tsv tsvector
);

-- Vector index
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);

-- FK Index for chunks.document_id
CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks(document_id);

-- Index for window retrieval
CREATE INDEX IF NOT EXISTS idx_chunks_doc_index ON chunks(document_id, chunk_index);

-- Lexical search index
CREATE INDEX IF NOT EXISTS idx_chunks_text_tsv ON chunks USING GIN (text_tsv);

-- Optional quick filter indexes
CREATE INDEX IF NOT EXISTS idx_chunks_chunk_type ON chunks(chunk_type);

-- 7. Text Search Trigger
CREATE OR REPLACE FUNCTION chunks_text_tsv_trigger() RETURNS trigger AS $$
BEGIN
  NEW.text_tsv := to_tsvector('english', coalesce(NEW.sentence_text, ''));
  RETURN NEW;
END
$$ LANGUAGE plpgsql;

CREATE TRIGGER chunks_text_tsv_update
BEFORE INSERT OR UPDATE ON chunks
FOR EACH ROW EXECUTE FUNCTION chunks_text_tsv_trigger();

-- 8. Ingestion Jobs (EXTENDED)
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id UUID PRIMARY KEY,
    collection_name TEXT NOT NULL,
    sector TEXT,
    stage TEXT,
    owner TEXT,
    filename TEXT,
    checksum TEXT,
    source_type TEXT,  -- NEW
    status TEXT NOT NULL DEFAULT 'queued',
    detail TEXT,
    chunks_processed INTEGER,
    collection_id INTEGER REFERENCES collections(id) ON DELETE SET NULL,
    document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
