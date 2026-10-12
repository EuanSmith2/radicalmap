-- The network made explicit, and the machinery for traversing it.
--
-- Until now the graph has been implicit — scattered across item_tags,
-- item_sounds, comments and links — which is fine for storing and useless for
-- traversing. Propagation needs to ask "what is one hop from here, along which
-- kind of tie, and how strong is it", and that question wants a real edge list.
--
-- Everything here is DERIVED. Nothing is collected into these tables; they are
-- rebuilt from the observation tables by the analyse phase, which means a
-- changed definition of "tie strength" is a recompute rather than a re-scrape.

CREATE TABLE edges (
    id             INTEGER PRIMARY KEY,
    src_type       TEXT NOT NULL,
    src_id         INTEGER NOT NULL,
    dst_type       TEXT NOT NULL,
    dst_id         INTEGER NOT NULL,
    edge_type      TEXT NOT NULL,
    -- Weight is comparable ACROSS edge types on purpose: a propagation score
    -- that mixes "shares a sound" with "shares a hashtag" needs them on one
    -- scale, so each builder normalises to roughly [0,1] before writing.
    weight         REAL NOT NULL DEFAULT 1.0,
    observed_count INTEGER NOT NULL DEFAULT 1,
    computed_at    INTEGER NOT NULL DEFAULT (unixepoch()),
    UNIQUE (src_type, src_id, dst_type, dst_id, edge_type)
) STRICT;

CREATE INDEX idx_edges_src  ON edges(src_type, src_id, edge_type, weight DESC);
CREATE INDEX idx_edges_dst  ON edges(dst_type, dst_id, edge_type, weight DESC);
CREATE INDEX idx_edges_type ON edges(edge_type, weight DESC);

CREATE TABLE node_metrics (
    node_type       TEXT NOT NULL,
    node_id         INTEGER NOT NULL,
    degree          INTEGER NOT NULL DEFAULT 0,
    weighted_degree REAL NOT NULL DEFAULT 0,
    pagerank        REAL,
    core_number     INTEGER,
    community       INTEGER,
    -- How many DISTINCT communities this node touches. A node bridging several
    -- is structurally more interesting than a high-degree node buried inside
    -- one — bridges are where a network recruits from outside itself.
    bridge_score    REAL,
    computed_at     INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (node_type, node_id)
) STRICT;

CREATE INDEX idx_node_metrics_pagerank  ON node_metrics(node_type, pagerank DESC);
CREATE INDEX idx_node_metrics_community ON node_metrics(community, node_type);
CREATE INDEX idx_node_metrics_bridge    ON node_metrics(bridge_score DESC);

-- Empirical yield per edge type: of the candidates this kind of tie proposed,
-- how many turned into anything. The propagation scorer reads this back as a
-- learned prior, so the crawl gets better at choosing where to go next instead
-- of trusting a weight someone guessed once.
CREATE TABLE edge_yield (
    edge_type    TEXT PRIMARY KEY,
    proposed     INTEGER NOT NULL DEFAULT 0,
    fetched      INTEGER NOT NULL DEFAULT 0,
    items_found  INTEGER NOT NULL DEFAULT 0,
    candidates   INTEGER NOT NULL DEFAULT 0,
    findings     INTEGER NOT NULL DEFAULT 0,
    updated_at   INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

-- One row per expansion decision, so the shape of a crawl is reconstructable
-- after the fact. Snowball sampling is only defensible if you can say exactly
-- how the sample was reached.
CREATE TABLE propagation_events (
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
                                                    'skipped_seen','skipped_score')),
    at            INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

CREATE INDEX idx_prop_events_run  ON propagation_events(run_id, at DESC);
CREATE INDEX idx_prop_events_edge ON propagation_events(edge_type, decision);

-- Discovery rate over time. When new accounts per hundred fetches flattens, the
-- community is enumerated — that is the stopping criterion that lets a paper
-- say "we mapped the network" rather than "we crawled until we stopped".
CREATE TABLE saturation_samples (
    id             INTEGER PRIMARY KEY,
    run_id         INTEGER,
    fetches        INTEGER NOT NULL,
    known_accounts INTEGER NOT NULL,
    known_items    INTEGER NOT NULL,
    known_sounds   INTEGER NOT NULL,
    new_accounts   INTEGER NOT NULL DEFAULT 0,
    at             INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

CREATE INDEX idx_saturation_run ON saturation_samples(run_id, fetches);

-- Accounts that look like the same operator: shared rare tokens, systematic
-- numbering, high character-level similarity. The lexicon already flags
-- "third account" in a bio; this finds the other two.
CREATE TABLE account_clusters (
    cluster_id   INTEGER NOT NULL,
    account_id   INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    similarity   REAL,
    evidence     TEXT NOT NULL DEFAULT '',
    computed_at  INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (cluster_id, account_id)
) STRICT;

CREATE INDEX idx_account_clusters_acct ON account_clusters(account_id);

-- Where a queued job came from, so propagation can be audited from the queue.
ALTER TABLE fetch_jobs ADD COLUMN edge_type TEXT;
ALTER TABLE fetch_jobs ADD COLUMN score REAL;
ALTER TABLE fetch_jobs ADD COLUMN drift REAL;

CREATE INDEX idx_jobs_score ON fetch_jobs(status, score DESC) WHERE status = 'pending';
