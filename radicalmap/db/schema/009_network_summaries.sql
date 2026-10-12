-- Summaries for the console's Network tab, written by network.rebuild().
--
-- edge_types() and communities() aggregated whole tables on every request:
-- 120 ms over 2M edges at 100k items, and 782 ms over 1.17M nodes at 1M.
-- Edges and node metrics only change in a graph build, and every build path
-- ends with network.rebuild(), so both summaries are written there, in the
-- same transaction as node_metrics, and always describe the same build.

CREATE TABLE edge_type_stats (
    edge_type   TEXT PRIMARY KEY,
    n           INTEGER NOT NULL,
    avg_weight  REAL,
    max_weight  REAL,
    computed_at INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

CREATE TABLE community_stats (
    community    INTEGER PRIMARY KEY,
    members      INTEGER NOT NULL,
    accounts     INTEGER NOT NULL,
    tags         INTEGER NOT NULL,
    avg_pagerank REAL,
    max_bridge   REAL,
    computed_at  INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

CREATE INDEX idx_community_stats_members ON community_stats(members DESC);
