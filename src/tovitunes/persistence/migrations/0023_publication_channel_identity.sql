-- Record the verified configured identity for future uploads; do not rewrite old receipts.
ALTER TABLE publication_attempts ADD COLUMN expected_channel_id TEXT;
