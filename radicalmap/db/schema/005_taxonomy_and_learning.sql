-- Three-axis taxonomy, and the tables a learning loop needs.
--
-- 001-004 assumed v1's shape: one flat `label` per verdict, drawn from nine
-- far-right categories. That cannot express the corpus this is being pointed at
-- now. Adding "communism" and "self_harm" to the same list would record lawful
-- political speech and a person's health disclosure under the same mechanism
-- that records incitement.
--
-- So: domain (what it IS) x harm_type (what makes it a finding) x stance (the
-- speaker's relation to it). A finding needs all three. See radicalmap/taxonomy.py.

CREATE TABLE domains (
    slug          TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    family        TEXT NOT NULL,
    note          TEXT NOT NULL DEFAULT '',
    requires_dpia INTEGER NOT NULL DEFAULT 0 CHECK (requires_dpia IN (0,1))
) STRICT;

CREATE TABLE harm_types (
    slug             TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    severity_ceiling TEXT NOT NULL CHECK (severity_ceiling IN ('low','medium','high')),
    note             TEXT NOT NULL DEFAULT '',
    requires_dpia    INTEGER NOT NULL DEFAULT 0 CHECK (requires_dpia IN (0,1))
) STRICT;

CREATE TABLE stances (
    slug             TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    finding_eligible INTEGER NOT NULL CHECK (finding_eligible IN (0,1)),
    note             TEXT NOT NULL DEFAULT ''
) STRICT;

-- The three axes on every verdict and every candidate.
ALTER TABLE classifications ADD COLUMN domain    TEXT REFERENCES domains(slug);
ALTER TABLE classifications ADD COLUMN harm_type TEXT REFERENCES harm_types(slug);
ALTER TABLE classifications ADD COLUMN stance    TEXT REFERENCES stances(slug);
-- Denormalised so "is this a finding" is an indexed lookup rather than a
-- three-column predicate repeated in every query that asks.
ALTER TABLE classifications ADD COLUMN is_finding INTEGER NOT NULL DEFAULT 0
    CHECK (is_finding IN (0,1));

CREATE INDEX idx_class_axes    ON classifications(domain, harm_type, stance);
CREATE INDEX idx_class_finding ON classifications(is_finding, domain) WHERE is_finding = 1;

ALTER TABLE lexicon_entries ADD COLUMN domain      TEXT REFERENCES domains(slug);
ALTER TABLE lexicon_entries ADD COLUMN harm_type   TEXT REFERENCES harm_types(slug);
ALTER TABLE lexicon_entries ADD COLUMN stance_hint TEXT REFERENCES stances(slug);

ALTER TABLE gold_annotations ADD COLUMN domain    TEXT REFERENCES domains(slug);
ALTER TABLE gold_annotations ADD COLUMN harm_type TEXT REFERENCES harm_types(slug);
ALTER TABLE gold_annotations ADD COLUMN stance    TEXT REFERENCES stances(slug);

-- ─── the learning loop ──────────────────────────────────────────────────────

-- A trained model is an artefact with provenance, exactly like a lexicon
-- version. Weights are stored so any stored prediction can be reproduced.
CREATE TABLE model_versions (
    id              INTEGER PRIMARY KEY,
    version         TEXT NOT NULL UNIQUE,
    algorithm       TEXT NOT NULL,
    feature_spec    TEXT NOT NULL,
    n_train         INTEGER NOT NULL DEFAULT 0,
    n_labels        INTEGER NOT NULL DEFAULT 0,
    trained_at      INTEGER NOT NULL DEFAULT (unixepoch()),
    gold_run        TEXT,
    metrics_json    TEXT,
    weights_path    TEXT,
    -- Provenance of the training data. A model trained partly on its own
    -- predecessor's output is not the same object as one trained on human
    -- labels, and confusing the two is how a self-improving loop quietly
    -- becomes a self-confirming one.
    train_source    TEXT NOT NULL DEFAULT 'gold'
                    CHECK (train_source IN ('gold','gold+adjudicated','adjudicated')),
    notes           TEXT NOT NULL DEFAULT ''
) STRICT;

-- One row per round of: score everything, pick the most informative items,
-- have a human label them, retrain, measure.
CREATE TABLE active_rounds (
    id               INTEGER PRIMARY KEY,
    round_no         INTEGER NOT NULL,
    strategy         TEXT NOT NULL,
    model_version_id INTEGER REFERENCES model_versions(id),
    n_pool           INTEGER NOT NULL DEFAULT 0,
    n_selected       INTEGER NOT NULL DEFAULT 0,
    n_annotated      INTEGER NOT NULL DEFAULT 0,
    metrics_json     TEXT,
    started_at       INTEGER NOT NULL DEFAULT (unixepoch()),
    finished_at      INTEGER,
    notes            TEXT NOT NULL DEFAULT ''
) STRICT;

CREATE INDEX idx_active_rounds_no ON active_rounds(round_no DESC);

-- What the selector asked a human to look at, and why.
CREATE TABLE active_queue (
    id           INTEGER PRIMARY KEY,
    round_id     INTEGER NOT NULL REFERENCES active_rounds(id) ON DELETE CASCADE,
    target_type  TEXT NOT NULL,
    target_id    INTEGER NOT NULL,
    score        REAL,
    reason       TEXT NOT NULL DEFAULT '',
    annotated    INTEGER NOT NULL DEFAULT 0 CHECK (annotated IN (0,1)),
    UNIQUE (round_id, target_type, target_id)
) STRICT;

CREATE INDEX idx_active_queue_round ON active_queue(round_id, annotated, score DESC);

-- Terms the model's own weights suggest, kept apart from the corpus-frequency
-- proposals in term_candidates so the two lines of evidence stay separable.
ALTER TABLE term_candidates ADD COLUMN source TEXT NOT NULL DEFAULT 'keyness'
    CHECK (source IN ('keyness','model_weight','manual'));
ALTER TABLE term_candidates ADD COLUMN weight REAL;
ALTER TABLE term_candidates ADD COLUMN domain TEXT REFERENCES domains(slug);

-- Audit trail for the Art. 9 gate: every time restricted processing was
-- enabled, under which assessment reference.
CREATE TABLE governance_events (
    id           INTEGER PRIMARY KEY,
    event        TEXT NOT NULL,
    dpia_ref     TEXT,
    detail       TEXT NOT NULL DEFAULT '',
    at           INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;
