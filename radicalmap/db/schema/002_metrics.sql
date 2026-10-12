-- Derived metrics, rebuilt by the analyse phase.
--
-- Nothing in here is collected; every row is recomputable from the observation
-- tables. That is the point. v1 maintained hashtag co-occurrence incrementally
-- DURING ingest, at one connection-commit-fsync per pair, which is O(n^2) per
-- video and means the numbers can never be recomputed after a lexicon change
-- without re-scraping.
--
-- The benchmark is the argument: computing tag co-occurrence live over 100k
-- items takes ~300ms, well past the point where a UI feels responsive. Read
-- from a table that the analyse phase refreshed and it is a sub-millisecond
-- index lookup.

CREATE TABLE metrics_runs (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,
    started_at  INTEGER NOT NULL DEFAULT (unixepoch()),
    finished_at INTEGER,
    row_count   INTEGER NOT NULL DEFAULT 0,
    notes       TEXT NOT NULL DEFAULT ''
) STRICT;

CREATE TABLE tag_cooccurrence (
    tag_a_id    INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    tag_b_id    INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    count       INTEGER NOT NULL,
    -- Raw co-occurrence over-rewards ubiquitous tags. Jaccard normalises for
    -- how common each tag is on its own, so a pair that genuinely travels
    -- together outranks a pair that merely both appear everywhere.
    jaccard     REAL,
    computed_at INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (tag_a_id, tag_b_id),
    CHECK (tag_a_id < tag_b_id)
) STRICT;

CREATE INDEX idx_cooc_count   ON tag_cooccurrence(count DESC);
CREATE INDEX idx_cooc_jaccard ON tag_cooccurrence(jaccard DESC);
CREATE INDEX idx_cooc_b       ON tag_cooccurrence(tag_b_id, tag_a_id);

CREATE TABLE tag_stats (
    tag_id       INTEGER PRIMARY KEY REFERENCES tags(id) ON DELETE CASCADE,
    item_count   INTEGER NOT NULL DEFAULT 0,
    account_count INTEGER NOT NULL DEFAULT 0,
    first_used   INTEGER,
    last_used    INTEGER,
    total_views  INTEGER,
    computed_at  INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

CREATE INDEX idx_tag_stats_items ON tag_stats(item_count DESC);

-- Sound reach is deliberately measured in distinct ACCOUNTS, not uses: a sound
-- used 500 times by one account is spam, the same sound used 50 times across
-- 50 accounts is a movement. Ranking by raw use count cannot tell them apart.
CREATE TABLE sound_stats (
    sound_id      INTEGER PRIMARY KEY REFERENCES sounds(id) ON DELETE CASCADE,
    item_count    INTEGER NOT NULL DEFAULT 0,
    account_count INTEGER NOT NULL DEFAULT 0,
    first_used    INTEGER,
    last_used     INTEGER,
    total_views   INTEGER,
    computed_at   INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

CREATE INDEX idx_sound_stats_accounts ON sound_stats(account_count DESC);
CREATE INDEX idx_sound_stats_items    ON sound_stats(item_count DESC);

CREATE TABLE account_stats (
    account_id     INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    item_count     INTEGER NOT NULL DEFAULT 0,
    comment_count  INTEGER NOT NULL DEFAULT 0,
    total_views    INTEGER,
    median_views   INTEGER,
    first_posted   INTEGER,
    last_posted    INTEGER,
    distinct_tags  INTEGER NOT NULL DEFAULT 0,
    distinct_sounds INTEGER NOT NULL DEFAULT 0,
    computed_at    INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

CREATE INDEX idx_account_stats_items ON account_stats(item_count DESC);
CREATE INDEX idx_account_stats_views ON account_stats(total_views DESC);

-- Pre-bucketed so the dashboard's time series is a range scan rather than a
-- GROUP BY over every item in the corpus.
CREATE TABLE daily_volume (
    day          INTEGER NOT NULL,        -- days since epoch
    platform_id  INTEGER NOT NULL REFERENCES platforms(id),
    item_count   INTEGER NOT NULL DEFAULT 0,
    account_count INTEGER NOT NULL DEFAULT 0,
    total_views  INTEGER,
    computed_at  INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (day, platform_id)
) STRICT;
