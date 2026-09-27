CREATE TABLE music_audio_analysis (
    blind_id TEXT NOT NULL REFERENCES music_outputs(blind_id),
    version INTEGER NOT NULL CHECK (version > 0),
    request_id TEXT NOT NULL REFERENCES music_requests(request_id),
    audio_sha256 TEXT NOT NULL CHECK (length(audio_sha256) = 64),
    analyzer_config_sha256 TEXT NOT NULL CHECK (length(analyzer_config_sha256) = 64),
    analysis_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (blind_id, version)
);
CREATE TRIGGER music_audio_analysis_no_update BEFORE UPDATE ON music_audio_analysis
BEGIN SELECT RAISE(ABORT, 'music audio analysis versions are immutable'); END;
CREATE TRIGGER music_audio_analysis_no_delete BEFORE DELETE ON music_audio_analysis
BEGIN SELECT RAISE(ABORT, 'music audio analysis versions are immutable'); END;
