CREATE TABLE visual_benchmark_requests (
    request_id TEXT PRIMARY KEY,
    benchmark_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    pack_revision_id TEXT NOT NULL REFERENCES character_pack_revisions(revision_id),
    brand_revision_id TEXT NOT NULL REFERENCES brand_revisions(revision_id),
    case_id TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'prepared', 'remote_started', 'succeeded', 'retryable_failure',
        'terminal_failure', 'ambiguous'
    )),
    canonical_spec_json TEXT NOT NULL,
    translated_request_json TEXT NOT NULL,
    capabilities_json TEXT NOT NULL,
    provider_request_id TEXT,
    latency_seconds REAL CHECK (latency_seconds IS NULL OR latency_seconds >= 0),
    usage_json TEXT,
    actual_cost_amount REAL CHECK (actual_cost_amount IS NULL OR actual_cost_amount >= 0),
    cost_currency TEXT,
    pricing_policy TEXT,
    response_metadata_json TEXT,
    error_kind TEXT,
    error_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (provider, model, case_id, attempt, input_fingerprint),
    CHECK ((actual_cost_amount IS NULL AND cost_currency IS NULL)
        OR (actual_cost_amount IS NOT NULL AND cost_currency IS NOT NULL)),
    CHECK (status <> 'succeeded' OR latency_seconds IS NOT NULL)
);

CREATE INDEX visual_benchmark_requests_status
    ON visual_benchmark_requests(status, provider, model, case_id);

CREATE TABLE visual_benchmark_outputs (
    request_id TEXT PRIMARY KEY REFERENCES visual_benchmark_requests(request_id),
    artifact_id TEXT NOT NULL UNIQUE REFERENCES artifact_versions(artifact_id),
    blind_id TEXT NOT NULL UNIQUE,
    width INTEGER NOT NULL CHECK (width > 0),
    height INTEGER NOT NULL CHECK (height > 0),
    mime_type TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE visual_benchmark_reviews (
    review_id TEXT PRIMARY KEY,
    blind_id TEXT NOT NULL REFERENCES visual_benchmark_outputs(blind_id),
    reviewer TEXT NOT NULL,
    review_role TEXT NOT NULL CHECK (review_role IN ('initial', 'adjudication')),
    scores_json TEXT NOT NULL,
    notes TEXT,
    evidence_note TEXT,
    hard_failure_reasons_json TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    UNIQUE (blind_id, reviewer)
);

CREATE INDEX visual_benchmark_reviews_blind_id
    ON visual_benchmark_reviews(blind_id, reviewed_at);

CREATE TRIGGER visual_benchmark_outputs_no_update
BEFORE UPDATE ON visual_benchmark_outputs
BEGIN SELECT RAISE(ABORT, 'visual benchmark outputs are immutable'); END;

CREATE TRIGGER visual_benchmark_outputs_no_delete
BEFORE DELETE ON visual_benchmark_outputs
BEGIN SELECT RAISE(ABORT, 'visual benchmark outputs are immutable'); END;

CREATE TRIGGER visual_benchmark_reviews_no_update
BEFORE UPDATE ON visual_benchmark_reviews
BEGIN SELECT RAISE(ABORT, 'visual benchmark reviews are immutable'); END;

CREATE TRIGGER visual_benchmark_reviews_no_delete
BEFORE DELETE ON visual_benchmark_reviews
BEGIN SELECT RAISE(ABORT, 'visual benchmark reviews are immutable'); END;
