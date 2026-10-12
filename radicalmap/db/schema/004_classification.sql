-- Classification, stage two.
--
-- 001 gave every verdict a versioned run to belong to. This adds what the
-- two-stage design needs on top: a tier on each lexicon entry, a link from a
-- verdict back to the exact pattern that produced it, a link from an
-- adjudication back to the candidate it adjudicated, and an explicit decision
-- separate from the category under consideration.
--
-- The reason for the last one: "this text was considered for the label
-- antisemitic and REJECTED" and "this text is antisemitic" must be different
-- rows, not the same row with a different meaning. v1 could not express the
-- first at all, so a false positive was indistinguishable from a finding.

ALTER TABLE lexicon_entries ADD COLUMN tier TEXT
    CHECK (tier IN ('A', 'B', 'C'));
ALTER TABLE lexicon_entries ADD COLUMN slug TEXT;
ALTER TABLE lexicon_entries ADD COLUMN lang TEXT NOT NULL DEFAULT 'en';

CREATE INDEX idx_lexicon_entries_slug ON lexicon_entries(slug);
CREATE INDEX idx_lexicon_entries_tier ON lexicon_entries(lexicon_version_id, tier);

-- Which pattern fired. Without this there is no per-pattern precision, and
-- with no per-pattern precision there is no principled way to retire a bad
-- pattern — which is how v1 ended up with a rule matching one specific video.
ALTER TABLE classifications ADD COLUMN lexicon_entry_id INTEGER
    REFERENCES lexicon_entries(id);

-- The adjudication chain: llm verdict -> lexicon candidate it ruled on.
ALTER TABLE classifications ADD COLUMN parent_id INTEGER
    REFERENCES classifications(id);

ALTER TABLE classifications ADD COLUMN verdict TEXT NOT NULL DEFAULT 'candidate'
    CHECK (verdict IN ('candidate', 'confirmed', 'rejected', 'uncertain'));

CREATE INDEX idx_class_entry   ON classifications(lexicon_entry_id, verdict);
CREATE INDEX idx_class_parent  ON classifications(parent_id);
CREATE INDEX idx_class_verdict ON classifications(run_id, verdict, label);

-- Terms the corpus suggests but the lexicon does not yet contain.
--
-- At 100k items the dictionary cannot be grown by hand-writing regexes for
-- terms someone happened to notice. Keyness analysis over adjudicated-positive
-- text against the background corpus proposes them; a human promotes them.
-- Nothing here is ever applied automatically.
CREATE TABLE term_candidates (
    id                INTEGER PRIMARY KEY,
    term              TEXT NOT NULL,
    run_id            INTEGER REFERENCES classifier_runs(id) ON DELETE CASCADE,
    positive_count    INTEGER NOT NULL DEFAULT 0,
    background_count  INTEGER NOT NULL DEFAULT 0,
    log_likelihood    REAL,
    ratio             REAL,
    example           TEXT,
    status            TEXT NOT NULL DEFAULT 'proposed'
                      CHECK (status IN ('proposed', 'promoted', 'rejected', 'deferred')),
    reviewed_by       TEXT,
    reviewed_at       INTEGER,
    created_at        INTEGER NOT NULL DEFAULT (unixepoch()),
    UNIQUE (term, run_id)
) STRICT;

CREATE INDEX idx_term_candidates_score ON term_candidates(log_likelihood DESC);
CREATE INDEX idx_term_candidates_status ON term_candidates(status);
