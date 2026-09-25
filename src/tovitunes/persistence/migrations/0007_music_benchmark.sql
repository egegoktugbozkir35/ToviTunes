CREATE TABLE music_requests (
    request_id TEXT PRIMARY KEY,
    brief_id TEXT NOT NULL,
    lyric_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    input_fingerprint TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('prepared', 'remote_started',
        'retryable_failure', 'ambiguous', 'terminal_failure', 'succeeded')),
    canonical_spec_json TEXT NOT NULL,
    translated_request_json TEXT NOT NULL,
    capabilities_json TEXT NOT NULL,
    provider_request_id TEXT,
    remote_started_at TEXT,
    failure_category TEXT,
    failure_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX music_requests_status ON music_requests(status, provider, model);
CREATE TABLE music_receipts (
    request_id TEXT PRIMARY KEY REFERENCES music_requests(request_id),
    provider_request_id TEXT,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    byte_count INTEGER NOT NULL CHECK (byte_count > 0),
    duration_seconds REAL NOT NULL CHECK (duration_seconds > 0),
    mime_type TEXT NOT NULL,
    container TEXT,
    codec TEXT,
    usage_json TEXT,
    actual_cost_amount REAL,
    cost_currency TEXT,
    pricing_policy TEXT,
    rights_evidence_json TEXT,
    response_metadata_json TEXT NOT NULL,
    received_at TEXT NOT NULL,
    CHECK ((actual_cost_amount IS NULL AND cost_currency IS NULL)
        OR (actual_cost_amount IS NOT NULL AND cost_currency IS NOT NULL))
);
CREATE TABLE music_outputs (
    request_id TEXT PRIMARY KEY REFERENCES music_receipts(request_id),
    blind_id TEXT NOT NULL UNIQUE,
    relative_path TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL,
    rights_status TEXT NOT NULL DEFAULT 'unknown',
    approval_status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);
CREATE TABLE music_reviews (
    review_id TEXT PRIMARY KEY,
    blind_id TEXT NOT NULL REFERENCES music_outputs(blind_id),
    reviewer TEXT NOT NULL,
    scores_json TEXT NOT NULL,
    evidence TEXT NOT NULL,
    hard_failures_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (blind_id, reviewer)
);
CREATE TABLE music_decisions (
    decision_id TEXT PRIMARY KEY,
    blind_id TEXT NOT NULL REFERENCES music_outputs(blind_id),
    decision_type TEXT NOT NULL CHECK (decision_type IN ('rights', 'approval')),
    status TEXT NOT NULL,
    actor TEXT NOT NULL,
    evidence TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE music_lyric_decisions (
    decision_id TEXT PRIMARY KEY,
    lyric_id TEXT NOT NULL,
    lyric_sha256 TEXT NOT NULL CHECK (length(lyric_sha256) = 64),
    status TEXT NOT NULL CHECK (status IN ('approved', 'rejected')),
    actor TEXT NOT NULL,
    evidence TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE music_timing (
    blind_id TEXT NOT NULL REFERENCES music_outputs(blind_id),
    version INTEGER NOT NULL CHECK (version > 0),
    analysis_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (blind_id, version)
);
CREATE TRIGGER music_receipts_no_update BEFORE UPDATE ON music_receipts
BEGIN SELECT RAISE(ABORT, 'music receipts are immutable'); END;
CREATE TRIGGER music_receipts_no_delete BEFORE DELETE ON music_receipts
BEGIN SELECT RAISE(ABORT, 'music receipts are immutable'); END;
CREATE TRIGGER music_outputs_no_delete BEFORE DELETE ON music_outputs
BEGIN SELECT RAISE(ABORT, 'music outputs are immutable'); END;
CREATE TRIGGER music_output_bytes_no_update BEFORE UPDATE OF request_id, blind_id, relative_path, sha256, created_at ON music_outputs
BEGIN SELECT RAISE(ABORT, 'music output identity is immutable'); END;
CREATE TRIGGER music_reviews_no_update BEFORE UPDATE ON music_reviews
BEGIN SELECT RAISE(ABORT, 'music reviews are immutable'); END;
CREATE TRIGGER music_lyric_decisions_no_update BEFORE UPDATE ON music_lyric_decisions
BEGIN SELECT RAISE(ABORT, 'lyric decisions are immutable'); END;
CREATE TRIGGER music_timing_no_update BEFORE UPDATE ON music_timing
BEGIN SELECT RAISE(ABORT, 'music timing versions are immutable'); END;
