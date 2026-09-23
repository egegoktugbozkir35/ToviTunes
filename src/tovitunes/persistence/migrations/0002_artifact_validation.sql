CREATE TABLE artifact_validation (
    validation_id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL REFERENCES artifact_versions(artifact_id),
    validator TEXT NOT NULL,
    result TEXT NOT NULL CHECK (result IN ('passed', 'failed')),
    observed_sha256 TEXT NOT NULL,
    media_facts_json TEXT NOT NULL,
    checked_at TEXT NOT NULL
);

CREATE INDEX artifact_validation_artifact_time
    ON artifact_validation(artifact_id, checked_at);

CREATE TRIGGER artifact_versions_no_update BEFORE UPDATE ON artifact_versions
BEGIN SELECT RAISE(ABORT, 'artifact versions are immutable'); END;

CREATE TRIGGER artifact_versions_no_delete BEFORE DELETE ON artifact_versions
BEGIN SELECT RAISE(ABORT, 'artifact versions are immutable'); END;

CREATE TRIGGER artifact_dependencies_no_update BEFORE UPDATE ON artifact_dependencies
BEGIN SELECT RAISE(ABORT, 'artifact dependencies are immutable'); END;

CREATE TRIGGER artifact_dependencies_no_delete BEFORE DELETE ON artifact_dependencies
BEGIN SELECT RAISE(ABORT, 'artifact dependencies are immutable'); END;

CREATE TRIGGER rights_decisions_no_update BEFORE UPDATE ON rights_decisions
BEGIN SELECT RAISE(ABORT, 'rights decisions are append-only'); END;

CREATE TRIGGER rights_decisions_no_delete BEFORE DELETE ON rights_decisions
BEGIN SELECT RAISE(ABORT, 'rights decisions are append-only'); END;

CREATE TRIGGER approval_decisions_no_update BEFORE UPDATE ON approval_decisions
BEGIN SELECT RAISE(ABORT, 'approval decisions are append-only'); END;

CREATE TRIGGER approval_decisions_no_delete BEFORE DELETE ON approval_decisions
BEGIN SELECT RAISE(ABORT, 'approval decisions are append-only'); END;

