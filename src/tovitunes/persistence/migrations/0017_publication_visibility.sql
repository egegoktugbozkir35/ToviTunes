CREATE TABLE publication_visibility_events (
    event_id TEXT PRIMARY KEY,
    upload_attempt_id TEXT NOT NULL REFERENCES publication_attempts(attempt_id),
    episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
    youtube_video_id TEXT NOT NULL,
    prior_privacy TEXT NOT NULL CHECK (prior_privacy = 'private'),
    target_privacy TEXT NOT NULL CHECK (target_privacy = 'public'),
    render_artifact_id TEXT NOT NULL REFERENCES artifact_versions(artifact_id),
    render_sha256 TEXT NOT NULL,
    metadata_artifact_id TEXT NOT NULL REFERENCES artifact_versions(artifact_id),
    metadata_fingerprint TEXT NOT NULL,
    prepared_at TEXT NOT NULL,
    remote_started_at TEXT,
    completed_at TEXT,
    outcome TEXT NOT NULL CHECK (outcome IN
        ('prepared', 'remote_started', 'succeeded', 'terminal_failure', 'ambiguous')),
    safe_error_summary TEXT
);
CREATE INDEX publication_visibility_episode ON publication_visibility_events(episode_id, prepared_at);
CREATE UNIQUE INDEX publication_visibility_success ON publication_visibility_events(upload_attempt_id)
    WHERE outcome = 'succeeded';
