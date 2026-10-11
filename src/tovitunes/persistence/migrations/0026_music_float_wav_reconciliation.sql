-- Additive evidence for a narrowly validated ACE-Step float WAV compatibility recovery.
CREATE TABLE music_wav_reconciliations (
    reconciliation_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE REFERENCES music_requests(request_id),
    provider_request_id TEXT NOT NULL,
    audio_sha256 TEXT NOT NULL CHECK (length(audio_sha256)=64),
    actor TEXT NOT NULL CHECK (length(trim(actor))>0),
    rationale TEXT NOT NULL CHECK (length(trim(rationale))>0),
    prior_request_json TEXT NOT NULL CHECK (json_valid(prior_request_json)),
    created_at TEXT NOT NULL
);
CREATE TRIGGER music_wav_reconciliation_valid_insert
BEFORE INSERT ON music_wav_reconciliations
WHEN NOT EXISTS (
    SELECT 1 FROM music_requests r JOIN music_receipts p USING (request_id)
    WHERE r.request_id=NEW.request_id AND r.provider='ace_step_local'
      AND r.model='acestep-v15-turbo' AND r.status='terminal_failure'
      AND r.failure_category IN ('provider','provider_retrieval')
      AND r.failure_reason='ACE-Step returned invalid WAV audio'
      AND r.remote_started_at IS NOT NULL
      AND length(trim(r.provider_request_id))>0
      AND r.provider_request_id=NEW.provider_request_id
      AND p.provider_request_id=NEW.provider_request_id AND p.sha256=NEW.audio_sha256
      AND p.mime_type='audio/wav' AND p.container='wav'
      AND p.codec IN ('pcm_f32le','pcm_f64le')
      AND json_extract(NEW.prior_request_json,'$.request_id')=r.request_id
      AND json_extract(NEW.prior_request_json,'$.status')=r.status
      AND json_extract(NEW.prior_request_json,'$.provider_request_id')=r.provider_request_id
      AND json_extract(NEW.prior_request_json,'$.failure_reason')=r.failure_reason
      AND json_extract(NEW.prior_request_json,'$.input_fingerprint')=r.input_fingerprint
)
BEGIN SELECT RAISE(ABORT, 'request lacks a validated float WAV reconciliation receipt'); END;
CREATE TRIGGER music_wav_reconciliation_no_update
BEFORE UPDATE ON music_wav_reconciliations
BEGIN SELECT RAISE(ABORT, 'music WAV reconciliations are immutable'); END;
CREATE TRIGGER music_wav_reconciliation_no_delete
BEFORE DELETE ON music_wav_reconciliations
BEGIN SELECT RAISE(ABORT, 'music WAV reconciliations are immutable'); END;
CREATE TRIGGER music_wav_reconciliation_no_replace
BEFORE INSERT ON music_wav_reconciliations
WHEN EXISTS (SELECT 1 FROM music_wav_reconciliations
             WHERE request_id=NEW.request_id OR reconciliation_id=NEW.reconciliation_id)
BEGIN SELECT RAISE(ABORT, 'music WAV reconciliations are immutable'); END;
