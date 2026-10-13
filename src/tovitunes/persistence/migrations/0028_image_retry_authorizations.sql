-- Explicit retry grants create new request identities and preserve every old receipt.
CREATE TABLE image_retry_authorizations (
    failed_request_id TEXT PRIMARY KEY REFERENCES generation_requests(request_id),
    new_request_id TEXT NOT NULL UNIQUE REFERENCES generation_requests(request_id),
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TRIGGER image_retry_immutable_update BEFORE UPDATE ON image_retry_authorizations
BEGIN SELECT RAISE(ABORT,'image retry authorizations are immutable'); END;
CREATE TRIGGER image_retry_immutable_delete BEFORE DELETE ON image_retry_authorizations
BEGIN SELECT RAISE(ABORT,'image retry authorizations are immutable'); END;
