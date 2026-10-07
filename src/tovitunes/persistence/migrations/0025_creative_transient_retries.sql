-- Every runtime retry has a fresh UUID and a previous_attempt_id audit link.
-- Preserve all historical receipts; reuse the latest request for an exact contract.
ALTER TABLE generation_requests ADD COLUMN retry_index INTEGER NOT NULL DEFAULT 0
    CHECK (retry_index BETWEEN 0 AND 3);
DROP INDEX generation_requests_creative_input;
CREATE UNIQUE INDEX generation_requests_creative_input
    ON generation_requests(coalesce(episode_id, run_id), kind, input_fingerprint,
        coalesce(previous_attempt_id, ''))
    WHERE prompt_version IS NOT NULL;
