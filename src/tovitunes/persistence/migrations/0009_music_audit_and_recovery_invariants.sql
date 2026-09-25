CREATE TRIGGER music_reviews_no_delete BEFORE DELETE ON music_reviews
BEGIN SELECT RAISE(ABORT, 'music reviews are immutable'); END;
CREATE TRIGGER music_decisions_no_update BEFORE UPDATE ON music_decisions
BEGIN SELECT RAISE(ABORT, 'music decisions are immutable'); END;
CREATE TRIGGER music_decisions_no_delete BEFORE DELETE ON music_decisions
BEGIN SELECT RAISE(ABORT, 'music decisions are immutable'); END;
CREATE TRIGGER music_lyric_decisions_no_delete BEFORE DELETE ON music_lyric_decisions
BEGIN SELECT RAISE(ABORT, 'lyric decisions are immutable'); END;
CREATE TRIGGER music_timing_no_delete BEFORE DELETE ON music_timing
BEGIN SELECT RAISE(ABORT, 'music timing versions are immutable'); END;
CREATE TRIGGER music_timing_decisions_no_delete BEFORE DELETE ON music_timing_decisions
BEGIN SELECT RAISE(ABORT, 'timing decisions are immutable'); END;

CREATE TRIGGER music_decisions_valid_insert BEFORE INSERT ON music_decisions
WHEN NOT (
    (NEW.decision_type = 'rights' AND NEW.status IN
        ('unknown', 'commercial_use_confirmed', 'restricted'))
    OR (NEW.decision_type = 'approval' AND NEW.status IN
        ('pending', 'approved', 'rejected'))
)
BEGIN SELECT RAISE(ABORT, 'invalid music decision type/status'); END;

CREATE TRIGGER music_output_matches_receipt BEFORE INSERT ON music_outputs
WHEN NOT EXISTS (
    SELECT 1 FROM music_receipts p
    WHERE p.request_id = NEW.request_id
      AND p.sha256 = NEW.sha256
      AND NEW.relative_path = NEW.request_id || '.wav'
)
BEGIN SELECT RAISE(ABORT, 'music output must match receipt'); END;

CREATE TRIGGER music_success_requires_receipt_output_insert
BEFORE INSERT ON music_requests
WHEN NEW.status = 'succeeded'
BEGIN SELECT RAISE(ABORT, 'music success requires receipt and output'); END;

CREATE TRIGGER music_success_requires_receipt_output_update
BEFORE UPDATE ON music_requests
WHEN NEW.status = 'succeeded' AND NOT EXISTS (
    SELECT 1 FROM music_receipts p
    JOIN music_outputs o ON o.request_id = p.request_id
    WHERE p.request_id = NEW.request_id
      AND o.sha256 = p.sha256
      AND o.relative_path = NEW.request_id || '.wav'
      AND p.provider_request_id IS NEW.provider_request_id
      AND NEW.remote_started_at IS NOT NULL
      AND (SELECT count(*) FROM music_outputs x WHERE x.request_id = NEW.request_id) = 1
)
BEGIN SELECT RAISE(ABORT, 'music success requires matching receipt and output'); END;

CREATE TRIGGER music_succeeded_request_immutable
BEFORE UPDATE ON music_requests
WHEN OLD.status = 'succeeded' AND (
    NEW.status <> 'succeeded'
    OR NEW.request_id IS NOT OLD.request_id
    OR NEW.brief_id IS NOT OLD.brief_id
    OR NEW.lyric_id IS NOT OLD.lyric_id
    OR NEW.provider IS NOT OLD.provider
    OR NEW.model IS NOT OLD.model
    OR NEW.attempt IS NOT OLD.attempt
    OR NEW.input_fingerprint IS NOT OLD.input_fingerprint
    OR NEW.canonical_spec_json IS NOT OLD.canonical_spec_json
    OR NEW.translated_request_json IS NOT OLD.translated_request_json
    OR NEW.capabilities_json IS NOT OLD.capabilities_json
    OR NEW.provider_request_id IS NOT OLD.provider_request_id
    OR NEW.remote_started_at IS NOT OLD.remote_started_at
)
BEGIN SELECT RAISE(ABORT, 'succeeded music request identity is immutable'); END;
