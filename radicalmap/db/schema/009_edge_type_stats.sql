-- Per-edge-type summary for the console's Network tab.
--
-- edge_types() aggregated the whole edges table on every request: 120 ms at
-- 100k items (2M edges), and it grows with the edge count. Edges only change
-- in a graph build, and every build path ends with network.rebuild(), so the
-- summary is written there, alongside node_metrics, and both describe the same
-- build.

CREATE TABLE edge_type_stats (
    edge_type   TEXT PRIMARY KEY,
    n           INTEGER NOT NULL,
    avg_weight  REAL,
    max_weight  REAL,
    computed_at INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;
