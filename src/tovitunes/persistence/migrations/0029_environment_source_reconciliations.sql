-- Re-admission of a hash-matched returned source never overwrites its failed receipt.
CREATE TABLE environment_source_reconciliations (
    request_id TEXT PRIMARY KEY REFERENCES environment_requests(request_id),
    source_artifact_id TEXT NOT NULL REFERENCES artifact_versions(artifact_id),
    artifact_id TEXT NOT NULL REFERENCES artifact_versions(artifact_id),
    response_metadata_json TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TRIGGER environment_source_reconciliation_no_update
BEFORE UPDATE ON environment_source_reconciliations
BEGIN SELECT RAISE(ABORT,'environment source reconciliations are immutable'); END;
CREATE TRIGGER environment_source_reconciliation_no_delete
BEFORE DELETE ON environment_source_reconciliations
BEGIN SELECT RAISE(ABORT,'environment source reconciliations are immutable'); END;
