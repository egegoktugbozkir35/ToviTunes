-- Additive; historical migration checksums and all retained evidence are preserved.
CREATE TABLE production_execution_lease (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    owner_token TEXT NOT NULL,
    operation TEXT NOT NULL,
    item_id TEXT,
    acquired_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
-- A request is an identity/intent, never a second lifecycle. Completion is computed from facts.
CREATE TABLE production_requests (
    request_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE REFERENCES creative_runs(run_id),
    target TEXT NOT NULL CHECK (target IN ('draft','render','publish')),
    created_at TEXT NOT NULL
);
CREATE TABLE production_runs (
    run_id TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    target TEXT NOT NULL,
    episode_key TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    outcome TEXT NOT NULL
);

-- Read historical intent once; never consult the superseded lifecycle in production.
INSERT INTO production_requests (request_id,run_id,target,created_at)
SELECT 'migrated:' || p.run_id,p.run_id,'publish',c.created_at
FROM production_next_runs p JOIN creative_runs c ON c.run_id=p.run_id
WHERE p.episode_id IS NOT NULL;

-- An immutable migration snapshot fences pre-MPT episode identities from new effects.
-- Existing historical tables and bytes remain untouched. New episodes are never inserted here.
CREATE TABLE historical_production_episodes (
    episode_id TEXT PRIMARY KEY REFERENCES episodes(episode_id)
);
INSERT INTO historical_production_episodes SELECT episode_id FROM episodes;
CREATE TRIGGER historical_production_episodes_no_update BEFORE UPDATE ON historical_production_episodes
BEGIN SELECT RAISE(ABORT, 'historical production identity is immutable'); END;
CREATE TRIGGER historical_production_episodes_no_delete BEFORE DELETE ON historical_production_episodes
BEGIN SELECT RAISE(ABORT, 'historical production identity is immutable'); END;

CREATE TRIGGER historical_production_episodes_no_replace BEFORE INSERT ON historical_production_episodes
WHEN EXISTS (SELECT 1 FROM historical_production_episodes WHERE episode_id=NEW.episode_id)
BEGIN SELECT RAISE(ABORT, 'historical production identity is immutable'); END;
