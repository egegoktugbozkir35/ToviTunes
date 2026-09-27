-- A planning run is the owner of subject calls before an episode exists.
CREATE TABLE creative_runs (
    run_id TEXT PRIMARY KEY,
    brand_revision_id TEXT NOT NULL REFERENCES brand_revisions(revision_id),
    curriculum_revision_id TEXT NOT NULL REFERENCES curriculum_revisions(revision_id),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    input_json TEXT NOT NULL CHECK (json_valid(input_json)),
    status TEXT NOT NULL CHECK (status IN ('planning', 'selected', 'complete')),
    selected_concept_id TEXT,
    selected_subject_json TEXT,
    reserved_episode_json TEXT,
    episode_id TEXT UNIQUE REFERENCES episodes(episode_id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Rebuild only this small ledger to admit a pre-episode owner and an invalid-response state.
ALTER TABLE generation_requests RENAME TO generation_requests_legacy;
CREATE TABLE generation_requests (
    request_id TEXT PRIMARY KEY,
    episode_id TEXT REFERENCES episodes(episode_id),
    kind TEXT NOT NULL,
    slot_key TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    provider_request_id TEXT UNIQUE,
    input_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('prepared', 'remote_started', 'succeeded',
        'failed', 'ambiguous', 'succeeded_response_invalid')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    run_id TEXT REFERENCES creative_runs(run_id),
    prompt_version TEXT,
    remote_started_at TEXT,
    attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt IN (1, 2)),
    parent_request_id TEXT REFERENCES generation_requests(request_id),
    error_kind TEXT,
    error_reason TEXT,
    response_content TEXT,
    response_sha256 TEXT,
    messages_json TEXT,
    CHECK ((episode_id IS NOT NULL AND run_id IS NULL)
        OR (episode_id IS NULL AND run_id IS NOT NULL)),
    CHECK ((attempt = 1 AND parent_request_id IS NULL)
        OR (attempt = 2 AND parent_request_id IS NOT NULL))
);
INSERT INTO generation_requests (request_id, episode_id, kind, slot_key, provider, model,
    provider_request_id, input_fingerprint, status, created_at, updated_at)
SELECT request_id, episode_id, kind, slot_key, provider, model, provider_request_id,
    input_fingerprint, status, created_at, updated_at FROM generation_requests_legacy;
DROP TABLE generation_requests_legacy;
CREATE INDEX generation_requests_slot_status
    ON generation_requests(episode_id, kind, slot_key, status);
CREATE UNIQUE INDEX generation_requests_creative_input
    ON generation_requests(coalesce(episode_id, run_id), kind, input_fingerprint)
    WHERE prompt_version IS NOT NULL;
