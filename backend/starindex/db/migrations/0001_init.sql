-- StarIndex schema v1.
-- Repository identity is GitHub's immutable numeric repository id; names are attributes.

CREATE TABLE repos (
    id               INTEGER PRIMARY KEY,          -- GitHub repository id (immutable)
    node_id          TEXT,
    full_name        TEXT NOT NULL,
    owner            TEXT NOT NULL,
    name             TEXT NOT NULL,
    owner_type       TEXT,
    owner_avatar_url TEXT,
    description      TEXT,
    homepage         TEXT,
    html_url         TEXT NOT NULL,
    clone_url        TEXT,
    topics           TEXT NOT NULL DEFAULT '[]',   -- JSON array
    language         TEXT,
    languages        TEXT NOT NULL DEFAULT '{}',   -- JSON {language: bytes}
    stars            INTEGER NOT NULL DEFAULT 0,
    forks            INTEGER NOT NULL DEFAULT 0,
    watchers         INTEGER NOT NULL DEFAULT 0,
    open_issues      INTEGER NOT NULL DEFAULT 0,
    license_spdx     TEXT,
    license_name     TEXT,
    default_branch   TEXT,
    size_kb          INTEGER NOT NULL DEFAULT 0,
    is_fork          INTEGER NOT NULL DEFAULT 0,
    is_archived      INTEGER NOT NULL DEFAULT 0,
    is_disabled      INTEGER NOT NULL DEFAULT 0,
    is_private       INTEGER NOT NULL DEFAULT 0,
    is_template      INTEGER NOT NULL DEFAULT 0,
    parent_full_name TEXT,
    created_at       TEXT,
    updated_at       TEXT,
    pushed_at        TEXT,
    starred_at       TEXT,
    -- synchronisation state
    star_state       TEXT NOT NULL DEFAULT 'starred',   -- starred | unstarred
    unstarred_at     TEXT,
    access_state     TEXT NOT NULL DEFAULT 'ok',        -- ok | not_found | blocked | error
    access_checked_at TEXT,
    first_seen_at    TEXT NOT NULL,
    last_seen_at     TEXT NOT NULL,
    last_seen_run    INTEGER,
    metadata_hash    TEXT,
    -- indexing state
    index_level      TEXT NOT NULL DEFAULT 'docs',      -- requested: metadata | docs | deep
    indexed_level    TEXT,                              -- achieved level
    index_status     TEXT NOT NULL DEFAULT 'pending',   -- pending | queued | indexing | indexed | failed | skipped
    index_error      TEXT,
    indexed_commit_sha TEXT,
    indexed_at       TEXT,
    tree_truncated   INTEGER NOT NULL DEFAULT 0,
    doc_count        INTEGER NOT NULL DEFAULT 0,
    chunk_count      INTEGER NOT NULL DEFAULT 0,
    -- repository-level embedding bookkeeping
    embed_text_hash  TEXT,
    embedded_model_key TEXT
);
CREATE INDEX idx_repos_full_name ON repos(full_name COLLATE NOCASE);
CREATE INDEX idx_repos_star_state ON repos(star_state, starred_at DESC);
CREATE INDEX idx_repos_language ON repos(language);
CREATE INDEX idx_repos_index_status ON repos(index_status);
CREATE INDEX idx_repos_pushed ON repos(pushed_at);

CREATE TABLE repo_name_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id       INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    old_full_name TEXT NOT NULL,
    new_full_name TEXT NOT NULL,
    changed_at    TEXT NOT NULL
);
CREATE INDEX idx_name_history_old ON repo_name_history(old_full_name COLLATE NOCASE);

-- Personal knowledge. Nothing in these tables is ever written by GitHub synchronisation.
CREATE TABLE repo_annotations (
    repo_id        INTEGER PRIMARY KEY REFERENCES repos(id) ON DELETE CASCADE,
    note           TEXT NOT NULL DEFAULT '',
    favorite       INTEGER NOT NULL DEFAULT 0,
    priority       INTEGER NOT NULL DEFAULT 0,
    last_opened_at TEXT,
    open_count     INTEGER NOT NULL DEFAULT 0,
    updated_at     TEXT NOT NULL
);

CREATE TABLE tags (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);
CREATE TABLE repo_tags (
    repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    tag_id  INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (repo_id, tag_id)
) WITHOUT ROWID;

CREATE TABLE collections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'manual',   -- manual | smart
    smart_query TEXT,                             -- JSON SearchRequest for smart collections
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE collection_repos (
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    repo_id       INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    added_at      TEXT NOT NULL,
    PRIMARY KEY (collection_id, repo_id)
) WITHOUT ROWID;

CREATE TABLE categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slug        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    keywords    TEXT NOT NULL DEFAULT '[]',   -- JSON array used by the rule classifier
    is_builtin  INTEGER NOT NULL DEFAULT 0,
    sort_order  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE repo_categories (
    repo_id     INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    source      TEXT NOT NULL,                -- rule | embedding | llm | user
    confidence  REAL NOT NULL DEFAULT 1.0,
    excluded    INTEGER NOT NULL DEFAULT 0,   -- 1 = user removed it; classifiers must not re-add
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (repo_id, category_id)
) WITHOUT ROWID;

-- Indexed content.
CREATE TABLE documents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id      INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    path         TEXT NOT NULL,
    kind         TEXT NOT NULL,     -- readme | doc | manifest | config | api | example | code
    language     TEXT,
    size_bytes   INTEGER NOT NULL DEFAULT 0,
    blob_sha     TEXT,
    content_hash TEXT NOT NULL,
    commit_sha   TEXT,
    level        TEXT NOT NULL,     -- docs | deep
    content      TEXT NOT NULL,
    truncated    INTEGER NOT NULL DEFAULT 0,
    indexed_at   TEXT NOT NULL,
    UNIQUE (repo_id, path)
);
CREATE INDEX idx_documents_repo ON documents(repo_id, kind);

CREATE TABLE chunks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    uid            TEXT NOT NULL UNIQUE,     -- stable UUIDv5 of (repo id, path, content hash, duplicate ordinal)
    repo_id        INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    document_id    INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal        INTEGER NOT NULL,
    kind           TEXT NOT NULL,
    path           TEXT NOT NULL,
    heading        TEXT NOT NULL DEFAULT '',
    start_line     INTEGER NOT NULL,
    end_line       INTEGER NOT NULL,
    content        TEXT NOT NULL,
    content_hash   TEXT NOT NULL,
    injection_flag INTEGER NOT NULL DEFAULT 0,
    embedded_model_key TEXT              -- NULL = not yet in the vector index for the active model
);
CREATE INDEX idx_chunks_repo ON chunks(repo_id);
CREATE INDEX idx_chunks_document ON chunks(document_id, ordinal);
CREATE INDEX idx_chunks_pending ON chunks(embedded_model_key);
CREATE INDEX idx_chunks_hash ON chunks(content_hash);

CREATE VIRTUAL TABLE chunk_fts USING fts5(
    content, heading, path,
    content='chunks', content_rowid='id',
    tokenize='porter unicode61 remove_diacritics 2'
);
CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunk_fts(rowid, content, heading, path) VALUES (new.id, new.content, new.heading, new.path);
END;
CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunk_fts(chunk_fts, rowid, content, heading, path) VALUES ('delete', old.id, old.content, old.heading, old.path);
END;
CREATE TRIGGER chunks_au AFTER UPDATE OF content, heading, path ON chunks BEGIN
    INSERT INTO chunk_fts(chunk_fts, rowid, content, heading, path) VALUES ('delete', old.id, old.content, old.heading, old.path);
    INSERT INTO chunk_fts(rowid, content, heading, path) VALUES (new.id, new.content, new.heading, new.path);
END;

-- Repository-level lexical document (rowid = repo id). Maintained by search.lexical.
CREATE VIRTUAL TABLE repo_fts USING fts5(
    name, owner, description, topics, categories, readme, notes,
    tokenize='porter unicode61 remove_diacritics 2',
    prefix='2 3'
);
-- Substring / fuzzy name matching.
CREATE VIRTUAL TABLE repo_name_trgm USING fts5(full_name, tokenize='trigram');
-- Vocabulary for typo correction (unstemmed terms with document frequency).
CREATE TABLE search_terms (
    term TEXT PRIMARY KEY,
    df   INTEGER NOT NULL
) WITHOUT ROWID;

-- Embeddings are stored here as the source of truth; the vector index is rebuildable from them.
CREATE TABLE embeddings (
    content_hash TEXT NOT NULL,
    model_key    TEXT NOT NULL,
    dim          INTEGER NOT NULL,
    vector       BLOB NOT NULL,     -- float32 little-endian
    created_at   TEXT NOT NULL,
    PRIMARY KEY (content_hash, model_key)
) WITHOUT ROWID;

CREATE TABLE repo_dependencies (
    repo_id       INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    ecosystem     TEXT NOT NULL,     -- npm | pypi | cargo | go | maven | rubygems | composer | nuget | docker
    name          TEXT NOT NULL,
    version_spec  TEXT NOT NULL DEFAULT '',
    scope         TEXT NOT NULL DEFAULT 'runtime',   -- runtime | dev | optional | peer | build
    manifest_path TEXT NOT NULL,
    PRIMARY KEY (repo_id, ecosystem, name, manifest_path, scope)
) WITHOUT ROWID;
CREATE INDEX idx_deps_name ON repo_dependencies(ecosystem, name);

CREATE TABLE repo_summaries (
    repo_id     INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,       -- summary | usage_ideas
    source_hash TEXT NOT NULL,       -- hash of the material the text was generated from
    provider    TEXT NOT NULL,
    model       TEXT NOT NULL,
    content     TEXT NOT NULL,
    evidence    TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL,
    PRIMARY KEY (repo_id, kind)
) WITHOUT ROWID;

-- Background work.
CREATE TABLE jobs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    type             TEXT NOT NULL,
    payload          TEXT NOT NULL DEFAULT '{}',
    state            TEXT NOT NULL DEFAULT '{}',     -- handler checkpoint, survives restarts
    status           TEXT NOT NULL DEFAULT 'queued', -- queued | running | succeeded | failed | cancelled
    priority         INTEGER NOT NULL DEFAULT 100,   -- lower runs first
    dedupe_key       TEXT,
    attempts         INTEGER NOT NULL DEFAULT 0,
    max_attempts     INTEGER NOT NULL DEFAULT 4,
    run_after        TEXT,
    lease_owner      TEXT,
    lease_expires_at TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    progress_current INTEGER NOT NULL DEFAULT 0,
    progress_total   INTEGER NOT NULL DEFAULT 0,
    progress_message TEXT NOT NULL DEFAULT '',
    error            TEXT,
    result           TEXT,
    created_at       TEXT NOT NULL,
    started_at       TEXT,
    finished_at      TEXT
);
CREATE INDEX idx_jobs_claim ON jobs(status, priority, id);
CREATE INDEX idx_jobs_type ON jobs(type, status);
CREATE UNIQUE INDEX idx_jobs_dedupe ON jobs(dedupe_key) WHERE dedupe_key IS NOT NULL AND status IN ('queued', 'running');

CREATE TABLE worker_heartbeats (
    worker_id  TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    info       TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE schedules (
    name             TEXT PRIMARY KEY,
    job_type         TEXT NOT NULL,
    payload          TEXT NOT NULL DEFAULT '{}',
    interval_minutes INTEGER NOT NULL,
    enabled          INTEGER NOT NULL DEFAULT 1,
    next_run_at      TEXT,
    last_run_at      TEXT
);

CREATE TABLE sync_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,            -- full | incremental
    status        TEXT NOT NULL,            -- running | succeeded | failed | cancelled | interrupted
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    pages_fetched INTEGER NOT NULL DEFAULT 0,
    repos_seen    INTEGER NOT NULL DEFAULT 0,
    added         INTEGER NOT NULL DEFAULT 0,
    updated       INTEGER NOT NULL DEFAULT 0,
    renamed       INTEGER NOT NULL DEFAULT 0,
    unstarred     INTEGER NOT NULL DEFAULT 0,
    restarred     INTEGER NOT NULL DEFAULT 0,
    not_modified  INTEGER NOT NULL DEFAULT 0,
    total_expected INTEGER,
    checkpoint    TEXT NOT NULL DEFAULT '{}',
    error         TEXT
);

CREATE TABLE http_cache (
    url           TEXT PRIMARY KEY,
    etag          TEXT,
    last_modified TEXT,
    updated_at    TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
) WITHOUT ROWID;

-- Search persistence.
CREATE TABLE search_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    query        TEXT NOT NULL,
    mode         TEXT NOT NULL,
    scope        TEXT NOT NULL DEFAULT 'repos',
    filters      TEXT NOT NULL DEFAULT '{}',
    result_count INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);
CREATE INDEX idx_search_history_created ON search_history(created_at DESC);

CREATE TABLE saved_searches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    request     TEXT NOT NULL,     -- JSON SearchRequest
    created_at  TEXT NOT NULL,
    last_run_at TEXT
);

-- Ask answers, research reports, comparisons and usage ideas share one store.
CREATE TABLE reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,          -- ask | research | compare | usage_ideas
    title       TEXT NOT NULL,
    question    TEXT NOT NULL,
    status      TEXT NOT NULL,          -- running | completed | failed | cancelled
    saved       INTEGER NOT NULL DEFAULT 0,
    provider_id TEXT,
    provider_kind TEXT,
    model       TEXT,
    answer_md   TEXT NOT NULL DEFAULT '',
    evidence    TEXT NOT NULL DEFAULT '[]',
    trace       TEXT NOT NULL DEFAULT '[]',
    data        TEXT NOT NULL DEFAULT '{}',   -- structured payload (comparison table, limits, warnings)
    usage       TEXT NOT NULL DEFAULT '{}',
    repo_ids    TEXT NOT NULL DEFAULT '[]',
    error       TEXT,
    created_at  TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX idx_reports_kind ON reports(kind, created_at DESC);
CREATE TABLE collection_reports (
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    report_id     INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    PRIMARY KEY (collection_id, report_id)
) WITHOUT ROWID;

-- Model providers. Secrets are never stored here; see starindex.secrets.
CREATE TABLE providers (
    id         TEXT PRIMARY KEY,           -- slug, e.g. "ollama", "openai", "my-vllm"
    kind       TEXT NOT NULL,              -- ollama | lmstudio | vllm | openai_compat | openai | anthropic | gemini | azure_openai | chatgpt_plan
    name       TEXT NOT NULL,
    base_url   TEXT,
    enabled    INTEGER NOT NULL DEFAULT 1,
    options    TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE usage_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,
    provider_id   TEXT NOT NULL,
    provider_kind TEXT NOT NULL,
    model         TEXT NOT NULL,
    task          TEXT NOT NULL,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    estimated     INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL,
    report_id     INTEGER
);
CREATE INDEX idx_usage_created ON usage_log(created_at DESC);

-- ChatGPT plan registrations (non-secret part; tokens live in the secret store).
CREATE TABLE chatgpt_accounts (
    id                 TEXT PRIMARY KEY,       -- local uuid
    label              TEXT NOT NULL,
    email              TEXT,
    subject            TEXT NOT NULL,
    issuer             TEXT NOT NULL,
    client_id          TEXT NOT NULL,          -- issued client id (never "dynamic_agent_client")
    scopes             TEXT NOT NULL DEFAULT '[]',
    plan_usage_enabled INTEGER NOT NULL DEFAULT 0,
    is_active          INTEGER NOT NULL DEFAULT 0,
    signed_in          INTEGER NOT NULL DEFAULT 1,
    welcome_shown      INTEGER NOT NULL DEFAULT 0,
    last_error         TEXT,
    saved_at           TEXT NOT NULL,
    expires_at         TEXT
);
