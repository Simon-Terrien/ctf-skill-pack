"""
Schema DDL — observations + episodes + memory_evidence for the L3A slice.

Schema version 3 adds the memory_evidence table. Bootstrap is idempotent:
running it on any prior version advances to v3 in place.

Per the ADR non-goals list, the beliefs table (L3B) remains deferred
to Block 5.

Uniqueness constraint
---------------------
memory_evidence enforces UNIQUE (user_id, source_kind, source_id, rule_id)
as a schema-level backstop for the service's idempotency check. If the
fast-path check in EvidenceService is ever bypassed or races, the INSERT
will fail rather than produce a duplicate record.
"""

# ── Version 1 (L1 slice) ─────────────────────────────────────────────

OBSERVATIONS_DDL: str = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS observations (
    obs_id          TEXT    PRIMARY KEY,
    user_id         TEXT    NOT NULL,
    session_id      TEXT    NOT NULL,
    turn_id         TEXT    NOT NULL,
    created_at      TEXT    NOT NULL,
    raw_text        TEXT    NOT NULL,
    language        TEXT,
    cms_real_json   TEXT    NOT NULL,
    cms_imag_json   TEXT    NOT NULL,
    temporal_phase  REAL    NOT NULL,
    features_json   TEXT    NOT NULL,
    tags_json       TEXT    NOT NULL,
    entities_json   TEXT    NOT NULL,
    quality_json    TEXT    NOT NULL,
    metadata_json   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_obs_user_session
    ON observations(user_id, session_id);

CREATE INDEX IF NOT EXISTS idx_obs_user_created
    ON observations(user_id, created_at);

INSERT OR IGNORE INTO schema_version (version, applied_at)
    VALUES (1, datetime('now'));
"""

# ── Version 2 (L2 slice) ─────────────────────────────────────────────

EPISODES_DDL: str = """
CREATE TABLE IF NOT EXISTS episodes (
    episode_id              TEXT    PRIMARY KEY,
    user_id                 TEXT    NOT NULL,
    session_id              TEXT    NOT NULL,
    created_at              TEXT    NOT NULL,
    start_at                TEXT    NOT NULL,
    end_at                  TEXT    NOT NULL,
    obs_ids_json            TEXT    NOT NULL,
    trajectory_signature_json TEXT  NOT NULL,
    surprise_score          REAL    NOT NULL,
    drift_score             REAL    NOT NULL,
    confidence_score        REAL    NOT NULL,
    closure_reason          TEXT    NOT NULL,
    metadata_json           TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_episode_user_session
    ON episodes(user_id, session_id);

CREATE INDEX IF NOT EXISTS idx_episode_user_created
    ON episodes(user_id, created_at);

CREATE INDEX IF NOT EXISTS idx_episode_user_start
    ON episodes(user_id, start_at);

INSERT OR IGNORE INTO schema_version (version, applied_at)
    VALUES (2, datetime('now'));
"""

# ── Version 3 (L3A slice — memory evidence) ──────────────────────────

EVIDENCE_DDL: str = """
CREATE TABLE IF NOT EXISTS memory_evidence (
    memory_id            TEXT    PRIMARY KEY,
    user_id              TEXT    NOT NULL,
    created_at           TEXT    NOT NULL,

    -- Provenance (mandatory — part of idempotency key)
    source_kind          TEXT    NOT NULL CHECK (source_kind IN ('observation', 'episode')),
    source_id            TEXT    NOT NULL,
    rule_id              TEXT    NOT NULL,

    -- Scope (soft-canonical; service polices the allowed values)
    scope                TEXT    NOT NULL,
    subscope             TEXT,
    tags_json            TEXT    NOT NULL,

    -- Content
    summary              TEXT    NOT NULL,

    -- Scoring
    support_score        REAL    NOT NULL,
    relevance_score      REAL    NOT NULL,

    -- Audit trail
    feature_snapshot_json TEXT   NOT NULL,

    -- Contradiction fields (Block 5 — persisted empty in Block 3)
    supersedes_json      TEXT    NOT NULL,
    contradicted_by_json TEXT    NOT NULL,

    -- Extension
    metadata_json        TEXT    NOT NULL,

    -- Idempotency backstop at the schema level
    UNIQUE (user_id, source_kind, source_id, rule_id)
);

CREATE INDEX IF NOT EXISTS idx_evidence_user_created
    ON memory_evidence(user_id, created_at);

CREATE INDEX IF NOT EXISTS idx_evidence_user_scope
    ON memory_evidence(user_id, scope);

CREATE INDEX IF NOT EXISTS idx_evidence_source
    ON memory_evidence(user_id, source_kind, source_id);

INSERT OR IGNORE INTO schema_version (version, applied_at)
    VALUES (3, datetime('now'));
"""

# ── Version 4 (L3B slice — profile beliefs) ──────────────────────────

BELIEFS_DDL: str = """
CREATE TABLE IF NOT EXISTS profile_beliefs (
    belief_id            TEXT    PRIMARY KEY,
    user_id              TEXT    NOT NULL,
    dimension            TEXT    NOT NULL,

    value                REAL    NOT NULL,
    confidence           REAL    NOT NULL,
    stability            REAL    NOT NULL,

    status               TEXT    NOT NULL CHECK (status IN
        ('tentative', 'active', 'stale', 'invalidated')),

    created_at           TEXT    NOT NULL,
    updated_at           TEXT    NOT NULL,

    supporting_memory_ids_json TEXT NOT NULL,
    counterevidence_ids_json   TEXT NOT NULL,

    metadata_json        TEXT    NOT NULL
    -- v5 removes the old UNIQUE (user_id, dimension); uniqueness now
    -- lives in idx_belief_user_dim_ctx_unique (created by v5 migration)
    -- which treats (user_id, dimension, context_key) as the key, with
    -- NULL context_key handled via COALESCE.
);

CREATE INDEX IF NOT EXISTS idx_belief_user
    ON profile_beliefs(user_id);

CREATE INDEX IF NOT EXISTS idx_belief_user_status
    ON profile_beliefs(user_id, status);

CREATE INDEX IF NOT EXISTS idx_belief_user_dimension
    ON profile_beliefs(user_id, dimension);

INSERT OR IGNORE INTO schema_version (version, applied_at)
    VALUES (4, datetime('now'));
"""

# ── Version 5 (Block 6 — scoped beliefs + context_key on evidence) ──
#
# Two changes at once, because they are the same conceptual move:
#   1. memory_evidence gains a nullable context_key column
#   2. profile_beliefs gains a nullable context_key column, and
#      uniqueness widens from (user_id, dimension) to
#      (user_id, dimension, context_key)
#
# SQLite-specific note: ALTER TABLE ADD COLUMN has no IF NOT EXISTS
# variant. We can't put raw ALTER statements in FULL_SCHEMA_DDL because
# re-running the bootstrap would fail with "duplicate column name".
# So FULL_SCHEMA_DDL stops at v4 and v5 is applied via migration steps.
#
# The bootstrap_full_schema() helper handles the full flow.

# Migration steps executed one at a time so the runner can tolerate
# "duplicate column name" errors when re-applying v5 on a database
# that already has it.
MIGRATION_V5_STEPS: tuple[str, ...] = (
    "ALTER TABLE memory_evidence ADD COLUMN context_key TEXT",
    "ALTER TABLE profile_beliefs ADD COLUMN context_key TEXT",
    # Widen belief uniqueness: create composite unique index that treats
    # NULL context_key as a sentinel via COALESCE. SQLite treats NULLs
    # as distinct in UNIQUE constraints; COALESCE normalizes them so
    # two global beliefs for the same (user, dimension) properly conflict.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_belief_user_dim_ctx_unique
        ON profile_beliefs(user_id, dimension, COALESCE(context_key, ''))
    """,
    "CREATE INDEX IF NOT EXISTS idx_belief_user_ctx ON profile_beliefs(user_id, context_key)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_user_ctx ON memory_evidence(user_id, context_key)",
    "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (5, datetime('now'))",
)


# ── Combined bootstrap ───────────────────────────────────────────────

FULL_SCHEMA_DDL: str = OBSERVATIONS_DDL + EPISODES_DDL + EVIDENCE_DDL + BELIEFS_DDL
"""Full schema bootstrap — applies v1 + v2 + v3 + v4 idempotently.

v5 is applied via MIGRATION_V5_STEPS. Both must run to get a
Block-6-ready database. Use bootstrap_full_schema() to do both.
"""


def bootstrap_full_schema(backend) -> None:
    """Apply all schema versions including Block 6 migrations.

    Safe to run on fresh or existing databases. This is the canonical
    entry point — do not call bootstrap_schema(FULL_SCHEMA_DDL) directly
    unless you know you don't need v5 (tests from earlier blocks, etc.).
    """
    backend.bootstrap_schema(FULL_SCHEMA_DDL)
    backend.apply_migration_steps(MIGRATION_V5_STEPS)
