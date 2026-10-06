-- Additive local-preview admission; approval/rights decisions remain authoritative.
CREATE TABLE preview_admissions (
    artifact_id TEXT PRIMARY KEY REFERENCES artifact_versions(artifact_id),
    sha256 TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    admitted_at TEXT NOT NULL
);
CREATE TABLE production_stage_events (
    event_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE episode_music_bindings (
    episode_id TEXT PRIMARY KEY REFERENCES episodes(episode_id),
    request_id TEXT NOT NULL UNIQUE REFERENCES music_requests(request_id),
    adapter_artifact_id TEXT NOT NULL REFERENCES artifact_versions(artifact_id),
    provider_endpoint TEXT NOT NULL
);
CREATE TABLE production_image_receipts (
    request_id TEXT PRIMARY KEY REFERENCES generation_requests(request_id),
    spec_json TEXT NOT NULL,
    provider_profile_json TEXT NOT NULL,
    source_artifact_id TEXT REFERENCES artifact_versions(artifact_id),
    normalized_artifact_id TEXT REFERENCES artifact_versions(artifact_id),
    response_sha256 TEXT,
    provider_request_id TEXT,
    response_metadata_json TEXT
);
CREATE TABLE production_next_runs (
    run_id TEXT PRIMARY KEY REFERENCES creative_runs(run_id),
    episode_id TEXT REFERENCES episodes(episode_id),
    status TEXT NOT NULL CHECK(status IN ('active','complete'))
);
CREATE TABLE workflow_process_leases (
    resource_key TEXT PRIMARY KEY,
    owner_token TEXT NOT NULL
);
ALTER TABLE environment_requests ADD COLUMN episode_id TEXT REFERENCES episodes(episode_id);
CREATE TRIGGER episode_music_bindings_no_update BEFORE UPDATE ON episode_music_bindings
BEGIN SELECT RAISE(ABORT, 'episode music binding is immutable'); END;
CREATE TRIGGER episode_music_bindings_no_delete BEFORE DELETE ON episode_music_bindings
BEGIN SELECT RAISE(ABORT, 'episode music binding is immutable'); END;
CREATE TRIGGER production_stage_events_no_update BEFORE UPDATE ON production_stage_events
BEGIN SELECT RAISE(ABORT, 'production events are immutable'); END;
CREATE TRIGGER production_stage_events_no_delete BEFORE DELETE ON production_stage_events
BEGIN SELECT RAISE(ABORT, 'production events are immutable'); END;
