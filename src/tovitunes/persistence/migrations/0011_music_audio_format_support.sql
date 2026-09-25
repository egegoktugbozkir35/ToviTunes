-- Forward-only extension for validated provider-original MP3 alongside existing PCM WAV.
DROP TRIGGER music_output_matches_receipt;
DROP TRIGGER music_success_requires_receipt_output_update;

CREATE TRIGGER music_output_matches_receipt BEFORE INSERT ON music_outputs
WHEN NOT EXISTS (
    SELECT 1 FROM music_receipts p
    WHERE p.request_id = NEW.request_id AND p.sha256 = NEW.sha256
      AND ((p.mime_type = 'audio/wav' AND p.container = 'wav'
            AND NEW.relative_path = NEW.request_id || '.wav')
        OR (p.mime_type = 'audio/mpeg' AND p.container = 'mp3' AND p.codec = 'mp3'
            AND NEW.relative_path = NEW.request_id || '.mp3'))
)
BEGIN SELECT RAISE(ABORT, 'music output must match receipt'); END;

CREATE TRIGGER music_success_requires_receipt_output_update
BEFORE UPDATE ON music_requests
WHEN NEW.status = 'succeeded' AND NOT EXISTS (
    SELECT 1 FROM music_receipts p
    JOIN music_outputs o ON o.request_id = p.request_id
    WHERE p.request_id = NEW.request_id AND o.sha256 = p.sha256
      AND ((p.mime_type = 'audio/wav' AND p.container = 'wav'
            AND o.relative_path = NEW.request_id || '.wav')
        OR (p.mime_type = 'audio/mpeg' AND p.container = 'mp3' AND p.codec = 'mp3'
            AND o.relative_path = NEW.request_id || '.mp3'))
      AND p.provider_request_id IS NEW.provider_request_id
      AND NEW.remote_started_at IS NOT NULL
      AND (SELECT count(*) FROM music_outputs x WHERE x.request_id = NEW.request_id) = 1
)
BEGIN SELECT RAISE(ABORT, 'music success requires matching receipt and output'); END;
