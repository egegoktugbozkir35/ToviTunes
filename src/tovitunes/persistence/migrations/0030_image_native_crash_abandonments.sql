-- Exceptional local crash evidence supplements an immutable successor grant.
-- The original ambiguous request and provider identity are never rewritten.
CREATE TABLE image_native_crash_abandonments (
    request_id TEXT PRIMARY KEY REFERENCES image_retry_authorizations(failed_request_id),
    evidence_json TEXT NOT NULL,
    evidence_sha256 TEXT NOT NULL
);
CREATE TRIGGER image_native_crash_no_update BEFORE UPDATE ON image_native_crash_abandonments
BEGIN SELECT RAISE(ABORT,'native crash abandonments are immutable'); END;
CREATE TRIGGER image_native_crash_no_delete BEFORE DELETE ON image_native_crash_abandonments
BEGIN SELECT RAISE(ABORT,'native crash abandonments are immutable'); END;
