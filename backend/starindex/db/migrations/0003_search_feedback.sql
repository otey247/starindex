-- Relevance feedback on search results: the user's own evaluation set.
CREATE TABLE search_feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    query       TEXT NOT NULL,                 -- as typed
    query_norm  TEXT NOT NULL,                 -- lower-cased, whitespace-collapsed
    repo_id     INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    verdict     TEXT NOT NULL,                 -- relevant | not_relevant | missing
    mode        TEXT NOT NULL DEFAULT 'hybrid',
    rank        INTEGER,                       -- position when judged (NULL for "missing")
    created_at  TEXT NOT NULL,
    UNIQUE (query_norm, repo_id)
);
CREATE INDEX idx_search_feedback_query ON search_feedback(query_norm);

CREATE TABLE eval_runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    queries    INTEGER NOT NULL,
    judgments  INTEGER NOT NULL,
    result     TEXT NOT NULL                   -- JSON EvalRun
);
