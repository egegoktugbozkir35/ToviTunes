CREATE TABLE publication_attempts (
    attempt_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
    platform TEXT NOT NULL CHECK (platform = 'youtube'),
    mode TEXT NOT NULL CHECK (mode = 'private_test'),
    render_artifact_id TEXT NOT NULL REFERENCES artifact_versions(artifact_id),
    render_sha256 TEXT NOT NULL,
    metadata_fingerprint TEXT NOT NULL,
    prepared_at TEXT NOT NULL,
    remote_started_at TEXT,
    completed_at TEXT,
    outcome TEXT NOT NULL CHECK (outcome IN
        ('prepared', 'remote_started', 'succeeded', 'terminal_failure', 'ambiguous')),
    youtube_video_id TEXT,
    privacy_status TEXT NOT NULL CHECK (privacy_status = 'private'),
    error_classification TEXT,
    safe_error_summary TEXT,
    CHECK (outcome != 'succeeded' OR youtube_video_id IS NOT NULL)
);
CREATE INDEX publication_attempts_episode ON publication_attempts(episode_id, prepared_at);
CREATE UNIQUE INDEX publication_attempts_success_identity ON publication_attempts
    (episode_id, render_sha256, metadata_fingerprint)
    WHERE outcome = 'succeeded';
