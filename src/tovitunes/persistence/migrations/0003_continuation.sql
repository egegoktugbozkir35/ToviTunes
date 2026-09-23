CREATE TABLE execution_leases (
    resource_key TEXT PRIMARY KEY,
    owner_token TEXT NOT NULL,
    acquired_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    CHECK (expires_at > acquired_at)
);

CREATE TABLE generation_requests (
    request_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
    kind TEXT NOT NULL,
    slot_key TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    provider_request_id TEXT UNIQUE,
    input_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN
        ('prepared', 'remote_started', 'succeeded', 'failed', 'ambiguous')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX generation_requests_slot_status
    ON generation_requests(episode_id, kind, slot_key, status);

