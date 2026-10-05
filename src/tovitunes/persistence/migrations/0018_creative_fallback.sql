-- Add audit information without changing historical receipts or request identities.
ALTER TABLE generation_requests ADD COLUMN requested_provider TEXT;
ALTER TABLE generation_requests ADD COLUMN requested_model TEXT;
ALTER TABLE generation_requests ADD COLUMN fallback_reason TEXT;
ALTER TABLE generation_requests ADD COLUMN fallback_index INTEGER CHECK (fallback_index >= 0);
ALTER TABLE generation_requests ADD COLUMN previous_attempt_id TEXT REFERENCES generation_requests(request_id);
