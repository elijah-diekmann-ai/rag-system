
-- 1. Document Types
CREATE TABLE IF NOT EXISTS document_types (
    id SERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL
);

-- 2. Entities
CREATE TABLE IF NOT EXISTS entities (
    id SERIAL PRIMARY KEY,
    entity_type TEXT NOT NULL,
    name TEXT NOT NULL,
    external_id TEXT,
    UNIQUE(entity_type, external_id)
);

-- 3. Extend Documents
ALTER TABLE documents 
    ADD COLUMN IF NOT EXISTS document_type_id INTEGER REFERENCES document_types(id),
    ADD COLUMN IF NOT EXISTS title TEXT,
    ADD COLUMN IF NOT EXISTS as_of_date DATE,
    ADD COLUMN IF NOT EXISTS reporting_period TEXT,
    ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1,
    ADD COLUMN IF NOT EXISTS is_current BOOLEAN DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS source_system TEXT,
    ADD COLUMN IF NOT EXISTS source_external_id TEXT,
    ADD COLUMN IF NOT EXISTS source_url TEXT,
    ADD COLUMN IF NOT EXISTS source_path TEXT;

-- 4. Document Entities
CREATE TABLE IF NOT EXISTS document_entities (
    document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
    entity_id INTEGER REFERENCES entities(id) ON DELETE CASCADE,
    role TEXT,
    PRIMARY KEY (document_id, entity_id, role)
);

-- 5. Extend Chunks
ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS chunk_type TEXT DEFAULT 'paragraph',
    ADD COLUMN IF NOT EXISTS section_title TEXT,
    ADD COLUMN IF NOT EXISTS section_path TEXT,
    ADD COLUMN IF NOT EXISTS table_name TEXT,
    ADD COLUMN IF NOT EXISTS text_tsv tsvector;

-- Populate text_tsv for existing chunks
UPDATE chunks SET text_tsv = to_tsvector('english', coalesce(sentence_text, '')) WHERE text_tsv IS NULL;

-- 6. Indexes
CREATE INDEX IF NOT EXISTS idx_chunks_text_tsv ON chunks USING GIN (text_tsv);
CREATE INDEX IF NOT EXISTS idx_chunks_chunk_type ON chunks(chunk_type);
-- (Existing indexes should be fine, but ensure they exist if not)
CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_doc_index ON chunks(document_id, chunk_index);

-- 7. Trigger for text_tsv
CREATE OR REPLACE FUNCTION chunks_text_tsv_trigger() RETURNS trigger AS $$
BEGIN
  NEW.text_tsv := to_tsvector('english', coalesce(NEW.sentence_text, ''));
  RETURN NEW;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS chunks_text_tsv_update ON chunks;
CREATE TRIGGER chunks_text_tsv_update
BEFORE INSERT OR UPDATE ON chunks
FOR EACH ROW EXECUTE FUNCTION chunks_text_tsv_trigger();

-- 8. Extend Ingestion Jobs
ALTER TABLE ingestion_jobs
    ADD COLUMN IF NOT EXISTS source_type TEXT;

-- 9. Entity Deduplication Index
CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_type_key
ON entities (entity_type, COALESCE(external_id, name));
