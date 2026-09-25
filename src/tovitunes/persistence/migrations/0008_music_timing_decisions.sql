CREATE TABLE music_timing_decisions (
    decision_id TEXT PRIMARY KEY,
    blind_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('approved', 'rejected')),
    actor TEXT NOT NULL,
    evidence TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (blind_id, version) REFERENCES music_timing(blind_id, version)
);
CREATE TRIGGER music_timing_decisions_no_update BEFORE UPDATE ON music_timing_decisions
BEGIN SELECT RAISE(ABORT, 'timing decisions are immutable'); END;
