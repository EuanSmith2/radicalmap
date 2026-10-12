-- RadicalMap v2 — initial schema.
-- Platform-agnostic. STRICT tables throughout: v1 silently accepted mistyped
-- values (save_count drifted into the live DB untyped and undeclared), and
-- STRICT makes that a write-time error instead of a query-time surprise.
--
-- Design rules enforced here:
--   1. Observation and interpretation are separate. Tables below record what
--      was collected; classification lives in classifications/classifier_runs
--      and is always attributable to a versioned run.
--   2. Everything collected carries provenance (source_id, collected_at,
--      collection_method, raw_json) so any row can be traced to how it arrived.
--   3. Identity is pseudonymous by default. Comment authors are stored as a
--      salted hash; the plaintext mapping lives in one side table that can be
--      withheld or dropped without damaging the corpus.
--   4. Nothing is maintained incrementally at ingest that can be derived later.
--      v1 wrote hashtag co-occurrence one connection per pair during ingest;
--      here it is a derived metrics table rebuilt by the analyse phase.


-- ─── provenance ──────────────────────────────────────────────────────────────

CREATE TABLE sources (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL CHECK (kind IN ('seed_list','import','api','frontier','manual','v1_migration')),
    description TEXT NOT NULL DEFAULT '',
    added_at    INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

CREATE TABLE platforms (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE,
    base_url TEXT NOT NULL DEFAULT ''
) STRICT;

-- ─── actors ──────────────────────────────────────────────────────────────────

CREATE TABLE accounts (
    id                  INTEGER PRIMARY KEY,
    platform_id         INTEGER NOT NULL REFERENCES platforms(id),
    -- The platform's stable opaque id, NOT the handle. v1 keyed accounts on
    -- username, so a rename silently forked one actor into two.
    platform_account_id TEXT NOT NULL,
    handle              TEXT,
    display_name        TEXT,
    bio                 TEXT,
    follower_count      INTEGER,
    following_count     INTEGER,
    item_count          INTEGER,
    verified            INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0,1)),
    created_at          INTEGER,            -- account creation, platform-reported
    is_deleted          INTEGER NOT NULL DEFAULT 0 CHECK (is_deleted IN (0,1)),
    deleted_detected_at INTEGER,
    source_id           INTEGER REFERENCES sources(id),
    collection_method   TEXT,
    first_seen_at       INTEGER NOT NULL DEFAULT (unixepoch()),
    last_seen_at        INTEGER NOT NULL DEFAULT (unixepoch()),
    collected_at        INTEGER NOT NULL DEFAULT (unixepoch()),
    raw_json            TEXT,
    UNIQUE (platform_id, platform_account_id)
) STRICT;

CREATE INDEX idx_accounts_handle      ON accounts(platform_id, handle);
CREATE INDEX idx_accounts_followers   ON accounts(follower_count DESC);
CREATE INDEX idx_accounts_deleted     ON accounts(is_deleted) WHERE is_deleted = 1;

-- Handles change, often deliberately after a takedown. Keeping the history is
-- a research signal in itself, and costs one narrow table.
CREATE TABLE account_handles (
    account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    handle      TEXT NOT NULL,
    first_seen  INTEGER NOT NULL DEFAULT (unixepoch()),
    last_seen   INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (account_id, handle)
) STRICT;

-- ─── content ─────────────────────────────────────────────────────────────────

CREATE TABLE items (
    id                INTEGER PRIMARY KEY,
    platform_id       INTEGER NOT NULL REFERENCES platforms(id),
    platform_item_id  TEXT NOT NULL,
    account_id        INTEGER REFERENCES accounts(id),
    kind              TEXT NOT NULL DEFAULT 'video'
                      CHECK (kind IN ('video','post','article','image','audio')),
    url               TEXT,
    title             TEXT,
    caption           TEXT,
    description       TEXT,
    -- posted_at is the single most important column v1 lacks: it only ever
    -- stored scraped_at, so no time series of CONTENT was possible, only of
    -- collection. For TikTok this is free — the top 32 bits of the numeric
    -- item id are a unix timestamp.
    posted_at         INTEGER,
    duration_s        REAL,
    language          TEXT,
    view_count        INTEGER,
    like_count        INTEGER,
    comment_count     INTEGER,
    share_count       INTEGER,
    save_count        INTEGER,
    is_deleted        INTEGER NOT NULL DEFAULT 0 CHECK (is_deleted IN (0,1)),
    deleted_detected_at INTEGER,
    source_id         INTEGER REFERENCES sources(id),
    collection_method TEXT,
    first_seen_at     INTEGER NOT NULL DEFAULT (unixepoch()),
    last_seen_at      INTEGER NOT NULL DEFAULT (unixepoch()),
    collected_at      INTEGER NOT NULL DEFAULT (unixepoch()),
    raw_json          TEXT,
    UNIQUE (platform_id, platform_item_id)
) STRICT;

CREATE INDEX idx_items_account   ON items(account_id);
CREATE INDEX idx_items_posted    ON items(platform_id, posted_at DESC);
CREATE INDEX idx_items_collected ON items(collected_at DESC);
CREATE INDEX idx_items_views     ON items(view_count DESC);
CREATE INDEX idx_items_url       ON items(url);
CREATE INDEX idx_items_deleted   ON items(is_deleted) WHERE is_deleted = 1;

-- Engagement is a trajectory, not a scalar. v1's refresh_stats.py overwrote the
-- previous values, destroying exactly the signal that shows a video going viral.
-- Latest values stay denormalised on items for fast filtering; history lands here.
CREATE TABLE item_metrics (
    item_id       INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    observed_at   INTEGER NOT NULL,
    view_count    INTEGER,
    like_count    INTEGER,
    comment_count INTEGER,
    share_count   INTEGER,
    save_count    INTEGER,
    PRIMARY KEY (item_id, observed_at)
) STRICT;

-- ─── audio ───────────────────────────────────────────────────────────────────

-- Sounds are shared across items, so this is one row per distinct sound, not
-- per use. On TikTok a shared sound is often a stronger tie than a shared
-- hashtag: it is the actual propagation mechanism, and unlike a hashtag it
-- cannot be casually altered to evade detection.
CREATE TABLE sounds (
    id                INTEGER PRIMARY KEY,
    platform_id       INTEGER NOT NULL REFERENCES platforms(id),
    platform_sound_id TEXT NOT NULL,
    title             TEXT,
    author_name       TEXT,
    duration_s        REAL,
    is_original       INTEGER NOT NULL DEFAULT 0 CHECK (is_original IN (0,1)),
    original_item_id  INTEGER REFERENCES items(id),
    use_count         INTEGER,             -- platform-reported, not our count
    local_path        TEXT,
    audio_sha256      TEXT,                -- exact-file dedup
    -- Acoustic fingerprint, so a re-upload that carries a NEW sound id but
    -- identical audio still collapses to one sound. That laundering is the
    -- standard post-takedown move and is invisible to hashtag analysis.
    fingerprint       TEXT,
    peaks_json        TEXT,                -- precomputed waveform for the UI
    source_id         INTEGER REFERENCES sources(id),
    first_seen_at     INTEGER NOT NULL DEFAULT (unixepoch()),
    collected_at      INTEGER NOT NULL DEFAULT (unixepoch()),
    raw_json          TEXT,
    UNIQUE (platform_id, platform_sound_id)
) STRICT;

CREATE INDEX idx_sounds_sha256      ON sounds(audio_sha256);
CREATE INDEX idx_sounds_fingerprint ON sounds(fingerprint);
CREATE INDEX idx_sounds_original    ON sounds(original_item_id);

CREATE TABLE item_sounds (
    item_id  INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    sound_id INTEGER NOT NULL REFERENCES sounds(id) ON DELETE CASCADE,
    PRIMARY KEY (item_id, sound_id)
) STRICT;

CREATE INDEX idx_item_sounds_sound ON item_sounds(sound_id, item_id);

-- ─── tags ────────────────────────────────────────────────────────────────────

-- Pure observation: what tags exist and where. Category and severity are NOT
-- stored here. In v1 they were columns on the hashtag row, which conflated a
-- collected fact with an analytical judgement and made reclassification a
-- destructive UPDATE. Interpretation now lives in the lexicon + classifications.
CREATE TABLE tags (
    id            INTEGER PRIMARY KEY,
    tag           TEXT NOT NULL,
    normalised    TEXT NOT NULL UNIQUE,   -- lowercased, stripped
    display_name  TEXT,
    first_seen_at INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

CREATE TABLE item_tags (
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    tag_id  INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (item_id, tag_id)
) STRICT;

CREATE INDEX idx_item_tags_tag ON item_tags(tag_id, item_id);

-- ─── comments ────────────────────────────────────────────────────────────────

CREATE TABLE comments (
    id                  INTEGER PRIMARY KEY,
    item_id             INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    platform_comment_id TEXT,
    parent_comment_id   INTEGER REFERENCES comments(id),   -- v1 flattened replies
    -- Pseudonymous by default: salted hash here, plaintext only in
    -- author_identities, which can be withheld from any export or dropped
    -- outright without harming the analysable corpus.
    author_hash         TEXT,
    author_account_id   INTEGER REFERENCES accounts(id),
    text                TEXT NOT NULL,
    like_count          INTEGER,
    reply_count         INTEGER,
    posted_at           INTEGER,
    source_id           INTEGER REFERENCES sources(id),
    collection_method   TEXT,
    collected_at        INTEGER NOT NULL DEFAULT (unixepoch()),
    raw_json            TEXT,
    -- v1 used UNIQUE(video_id, author, text), which silently discarded genuine
    -- duplicate comments (two people saying "based" on the same video).
    UNIQUE (item_id, platform_comment_id)
) STRICT;

CREATE INDEX idx_comments_item   ON comments(item_id);
CREATE INDEX idx_comments_author ON comments(author_hash);
CREATE INDEX idx_comments_posted ON comments(posted_at DESC);
CREATE INDEX idx_comments_parent ON comments(parent_comment_id);

-- Re-identification side table. Separate on purpose; treat as restricted.
CREATE TABLE author_identities (
    author_hash TEXT PRIMARY KEY,
    handle      TEXT NOT NULL,
    platform_id INTEGER REFERENCES platforms(id),
    first_seen  INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

-- ─── attachments ─────────────────────────────────────────────────────────────

CREATE TABLE transcripts (
    id           INTEGER PRIMARY KEY,
    item_id      INTEGER NOT NULL UNIQUE REFERENCES items(id) ON DELETE CASCADE,
    content      TEXT NOT NULL,
    language     TEXT NOT NULL DEFAULT 'en',
    source       TEXT NOT NULL DEFAULT 'auto' CHECK (source IN ('auto','manual','asr')),
    confidence   REAL,
    collected_at INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

CREATE TABLE stickers (
    id           INTEGER PRIMARY KEY,
    item_id      INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    sticker_id   TEXT,
    sticker_type TEXT,
    description  TEXT,
    text_content TEXT,
    collected_at INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

CREATE INDEX idx_stickers_item ON stickers(item_id);

CREATE TABLE links (
    id              INTEGER PRIMARY KEY,
    source_type     TEXT NOT NULL CHECK (source_type IN ('item','account','comment','transcript')),
    source_id       INTEGER NOT NULL,
    -- v1 scanned only bios and captions, never comment text, which is why its
    -- external_links table was empty despite a caption reading "telegram group
    -- in comments!". Recording the origin makes that coverage gap auditable.
    extracted_from  TEXT NOT NULL CHECK (extracted_from IN ('caption','bio','comment','transcript','description')),
    platform        TEXT,
    url             TEXT NOT NULL,
    normalised_url  TEXT,
    domain          TEXT,
    context         TEXT,
    collected_at    INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

CREATE INDEX idx_links_source ON links(source_type, source_id);
CREATE INDEX idx_links_domain ON links(domain);

-- ─── classification ──────────────────────────────────────────────────────────

CREATE TABLE lexicon_versions (
    id         INTEGER PRIMARY KEY,
    version    TEXT NOT NULL UNIQUE,
    hash       TEXT NOT NULL,
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    notes      TEXT NOT NULL DEFAULT ''
) STRICT;

CREATE TABLE lexicon_entries (
    id                 INTEGER PRIMARY KEY,
    lexicon_version_id INTEGER NOT NULL REFERENCES lexicon_versions(id) ON DELETE CASCADE,
    pattern            TEXT NOT NULL,
    pattern_type       TEXT NOT NULL DEFAULT 'regex' CHECK (pattern_type IN ('regex','literal')),
    category           TEXT NOT NULL,
    severity           TEXT CHECK (severity IN ('low','medium','high')),
    definition         TEXT,
    origin             TEXT,
    notes              TEXT NOT NULL DEFAULT ''
) STRICT;

CREATE INDEX idx_lexicon_entries_version ON lexicon_entries(lexicon_version_id);

CREATE TABLE classifier_runs (
    id                 INTEGER PRIMARY KEY,
    stage              TEXT NOT NULL CHECK (stage IN ('lexicon','llm','human','ensemble')),
    version            TEXT NOT NULL,
    lexicon_version_id INTEGER REFERENCES lexicon_versions(id),
    model              TEXT,
    prompt_hash        TEXT,
    params_json        TEXT,
    started_at         INTEGER NOT NULL DEFAULT (unixepoch()),
    finished_at        INTEGER,
    target_count       INTEGER NOT NULL DEFAULT 0,
    notes              TEXT NOT NULL DEFAULT ''
) STRICT;

-- One row per (target, run) verdict. Keeping every stage's output rather than
-- collapsing to a single label is what lets lexicon/LLM disagreement stay
-- visible in the UI instead of being silently resolved at write time.
CREATE TABLE classifications (
    id            INTEGER PRIMARY KEY,
    target_type   TEXT NOT NULL CHECK (target_type IN ('item','comment','account','tag','sound')),
    target_id     INTEGER NOT NULL,
    run_id        INTEGER NOT NULL REFERENCES classifier_runs(id) ON DELETE CASCADE,
    stage         TEXT NOT NULL CHECK (stage IN ('lexicon','llm','human','ensemble')),
    label         TEXT NOT NULL,
    severity      TEXT CHECK (severity IN ('low','medium','high')),
    confidence    REAL,
    evidence_span TEXT,
    evidence_start INTEGER,
    evidence_end   INTEGER,
    rationale     TEXT,
    created_at    INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

CREATE INDEX idx_class_target   ON classifications(target_type, target_id, run_id);
CREATE INDEX idx_class_run      ON classifications(run_id, label, severity);
CREATE INDEX idx_class_label    ON classifications(label, severity);

-- The gold set lives in the database, not a loose CSV, so evaluation is a join
-- and every reported precision/recall figure is reproducible from the DB alone.
CREATE TABLE gold_annotations (
    id          INTEGER PRIMARY KEY,
    target_type TEXT NOT NULL CHECK (target_type IN ('item','comment','account','tag','sound')),
    target_id   INTEGER NOT NULL,
    label       TEXT NOT NULL,
    severity    TEXT CHECK (severity IN ('low','medium','high')),
    is_positive INTEGER NOT NULL DEFAULT 1 CHECK (is_positive IN (0,1)),
    annotator   TEXT NOT NULL,
    annotated_at INTEGER NOT NULL DEFAULT (unixepoch()),
    notes       TEXT NOT NULL DEFAULT '',
    UNIQUE (target_type, target_id, label, annotator)
) STRICT;

-- ─── collection queue ────────────────────────────────────────────────────────

CREATE TABLE fetch_jobs (
    id              INTEGER PRIMARY KEY,
    platform_id     INTEGER NOT NULL REFERENCES platforms(id),
    job_type        TEXT NOT NULL CHECK (job_type IN
                      ('item','account','account_timeline','tag_page','sound_page','comments','transcript','audio')),
    target          TEXT NOT NULL,
    priority        INTEGER NOT NULL DEFAULT 100,
    status          TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending','running','done','failed','dead','skipped')),
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    next_attempt_at INTEGER NOT NULL DEFAULT (unixepoch()),
    depth           INTEGER NOT NULL DEFAULT 0,
    parent_job_id   INTEGER REFERENCES fetch_jobs(id),
    source_id       INTEGER REFERENCES sources(id),
    lease_expires_at INTEGER,
    created_at      INTEGER NOT NULL DEFAULT (unixepoch()),
    started_at      INTEGER,
    finished_at     INTEGER,
    UNIQUE (platform_id, job_type, target)
) STRICT;

-- The queue-pop query. Partial index keeps it small as done rows accumulate.
CREATE INDEX idx_jobs_claim ON fetch_jobs(next_attempt_at, priority, id)
    WHERE status = 'pending';
CREATE INDEX idx_jobs_status ON fetch_jobs(status, job_type);
CREATE INDEX idx_jobs_lease  ON fetch_jobs(lease_expires_at) WHERE status = 'running';

CREATE TABLE collection_events (
    id       INTEGER PRIMARY KEY,
    job_id   INTEGER REFERENCES fetch_jobs(id) ON DELETE SET NULL,
    level    TEXT NOT NULL DEFAULT 'info' CHECK (level IN ('debug','info','warn','error')),
    event    TEXT NOT NULL,
    detail   TEXT,
    at       INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

CREATE INDEX idx_events_at  ON collection_events(at DESC);
CREATE INDEX idx_events_job ON collection_events(job_id);

-- ─── full-text search ────────────────────────────────────────────────────────
-- External-content FTS5: the index stores only the inverted index, not a second
-- copy of the text. This is what makes the query bar instant at a million rows;
-- v1 had no index of any kind and regex-scanned full tables in Python.

CREATE VIRTUAL TABLE items_fts USING fts5(
    caption, title, description,
    content = 'items', content_rowid = 'id',
    tokenize = "unicode61 remove_diacritics 2"
);

CREATE TRIGGER items_fts_ai AFTER INSERT ON items BEGIN
    INSERT INTO items_fts(rowid, caption, title, description)
    VALUES (new.id, new.caption, new.title, new.description);
END;
CREATE TRIGGER items_fts_ad AFTER DELETE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, caption, title, description)
    VALUES ('delete', old.id, old.caption, old.title, old.description);
END;
CREATE TRIGGER items_fts_au AFTER UPDATE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, caption, title, description)
    VALUES ('delete', old.id, old.caption, old.title, old.description);
    INSERT INTO items_fts(rowid, caption, title, description)
    VALUES (new.id, new.caption, new.title, new.description);
END;

CREATE VIRTUAL TABLE comments_fts USING fts5(
    text,
    content = 'comments', content_rowid = 'id',
    tokenize = "unicode61 remove_diacritics 2"
);

CREATE TRIGGER comments_fts_ai AFTER INSERT ON comments BEGIN
    INSERT INTO comments_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER comments_fts_ad AFTER DELETE ON comments BEGIN
    INSERT INTO comments_fts(comments_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER comments_fts_au AFTER UPDATE ON comments BEGIN
    INSERT INTO comments_fts(comments_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO comments_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE VIRTUAL TABLE transcripts_fts USING fts5(
    content,
    content = 'transcripts', content_rowid = 'id',
    tokenize = "unicode61 remove_diacritics 2"
);

CREATE TRIGGER transcripts_fts_ai AFTER INSERT ON transcripts BEGIN
    INSERT INTO transcripts_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER transcripts_fts_ad AFTER DELETE ON transcripts BEGIN
    INSERT INTO transcripts_fts(transcripts_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER transcripts_fts_au AFTER UPDATE ON transcripts BEGIN
    INSERT INTO transcripts_fts(transcripts_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO transcripts_fts(rowid, content) VALUES (new.id, new.content);
END;

-- ─── seed data ───────────────────────────────────────────────────────────────

INSERT INTO platforms (name, base_url) VALUES
    ('tiktok',   'https://www.tiktok.com'),
    ('youtube',  'https://www.youtube.com'),
    ('telegram', 'https://t.me'),
    ('rumble',   'https://rumble.com'),
    ('bitchute', 'https://www.bitchute.com'),
    ('odysee',   'https://odysee.com');
