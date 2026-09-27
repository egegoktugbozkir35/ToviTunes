CREATE TRIGGER music_audio_analysis_valid_identity BEFORE INSERT ON music_audio_analysis
WHEN NOT EXISTS (
    SELECT 1 FROM music_outputs o JOIN music_receipts p USING (request_id)
    WHERE o.blind_id = NEW.blind_id AND o.request_id = NEW.request_id
      AND o.sha256 = NEW.audio_sha256 AND p.sha256 = NEW.audio_sha256
)
OR json_extract(NEW.analysis_json, '$.blind_id') IS NOT NEW.blind_id
OR json_extract(NEW.analysis_json, '$.request_id') IS NOT NEW.request_id
OR json_extract(NEW.analysis_json, '$.audio_sha256') IS NOT NEW.audio_sha256
OR json_extract(NEW.analysis_json, '$.version') IS NOT NEW.version
OR json_extract(NEW.analysis_json, '$.analyzer_config_sha256') IS NOT NEW.analyzer_config_sha256
BEGIN SELECT RAISE(ABORT, 'music analysis identity must match retained output'); END;
