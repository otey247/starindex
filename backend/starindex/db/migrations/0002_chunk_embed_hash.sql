-- Remember which cached embedding belongs to each chunk, so filtered semantic search can read
-- vectors straight from SQLite (exact search over a restricted set of repositories).
ALTER TABLE chunks ADD COLUMN embed_hash TEXT;
CREATE INDEX idx_chunks_repo_kind ON chunks(repo_id, kind);
-- Chunks embedded before this column existed are re-linked by the next embedding pass
-- (their vectors are still in the cache, so nothing is recomputed).
UPDATE chunks SET embedded_model_key=NULL WHERE embed_hash IS NULL;
