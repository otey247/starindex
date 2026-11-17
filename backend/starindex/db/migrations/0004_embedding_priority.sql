-- Older installations may already have an embedding job behind thousands of index jobs.
UPDATE jobs SET priority=45 WHERE type='embed_pending' AND status='queued' AND priority=60;
