CREATE TABLE music_policy_evaluations (
    evaluation_id TEXT PRIMARY KEY,
    subject_type TEXT NOT NULL CHECK (subject_type IN ('lyrics', 'audio', 'rights', 'timing')),
    subject_id TEXT NOT NULL,
    subject_sha256 TEXT NOT NULL CHECK (length(subject_sha256) = 64),
    policy_id TEXT NOT NULL,
    policy_version INTEGER NOT NULL CHECK (policy_version > 0),
    evaluator TEXT NOT NULL,
    evaluator_type TEXT NOT NULL CHECK (evaluator_type IN ('machine', 'human')),
    status TEXT NOT NULL CHECK (status IN ('pass', 'fail', 'blocked')),
    evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
    thresholds_json TEXT NOT NULL CHECK (json_valid(thresholds_json)),
    created_at TEXT NOT NULL
);
CREATE INDEX music_policy_evaluations_subject
ON music_policy_evaluations(subject_type, subject_id, policy_id, policy_version, created_at);
CREATE TRIGGER music_policy_evaluations_no_update BEFORE UPDATE ON music_policy_evaluations
BEGIN SELECT RAISE(ABORT, 'policy evaluations are immutable'); END;
CREATE TRIGGER music_policy_evaluations_no_delete BEFORE DELETE ON music_policy_evaluations
BEGIN SELECT RAISE(ABORT, 'policy evaluations are immutable'); END;
ALTER TABLE music_decisions ADD COLUMN actor_type TEXT NOT NULL DEFAULT 'legacy_unclassified'
CHECK (actor_type IN ('human', 'machine', 'legacy_unclassified'));
ALTER TABLE music_lyric_decisions ADD COLUMN actor_type TEXT NOT NULL DEFAULT 'legacy_unclassified'
CHECK (actor_type IN ('human', 'machine', 'legacy_unclassified'));
ALTER TABLE music_timing_decisions ADD COLUMN actor_type TEXT NOT NULL DEFAULT 'legacy_unclassified'
CHECK (actor_type IN ('human', 'machine', 'legacy_unclassified'));
