-- Record the unreachable decision too.
--
-- propagation_events existed to make a crawl's shape reconstructable, and had a
-- hole in it: a candidate whose node could not be turned into a fetchable
-- address was dropped with no row written. That is the one outcome most worth
-- seeing, because it means the graph and the adapter disagree about what is
-- addressable — and in a real run it was the majority outcome without anyone
-- being able to tell.
--
-- SQLite cannot alter a CHECK constraint, so the table is rebuilt. It is an
-- audit table of derived decisions, so carrying the rows over is enough.

CREATE TABLE propagation_events_new (
    id            INTEGER PRIMARY KEY,
    run_id        INTEGER,
    src_type      TEXT NOT NULL,
    src_id        INTEGER,
    edge_type     TEXT NOT NULL,
    target        TEXT NOT NULL,
    depth         INTEGER NOT NULL DEFAULT 0,
    score         REAL,
    drift         REAL,
    decision      TEXT NOT NULL CHECK (decision IN ('enqueued','skipped_drift','skipped_budget',
                                                    'skipped_seen','skipped_score',
                                                    'skipped_unreachable','skipped_depth')),
    at            INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

INSERT INTO propagation_events_new
    SELECT id, run_id, src_type, src_id, edge_type, target, depth, score, drift, decision, at
      FROM propagation_events;

DROP TABLE propagation_events;
ALTER TABLE propagation_events_new RENAME TO propagation_events;

CREATE INDEX idx_prop_events_run  ON propagation_events(run_id, at DESC);
CREATE INDEX idx_prop_events_edge ON propagation_events(edge_type, decision);
