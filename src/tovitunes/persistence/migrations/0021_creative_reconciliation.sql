-- Additive operator decisions; no historical request, receipt or artifact is rewritten.
CREATE TABLE creative_request_reconciliations (
    reconciliation_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE REFERENCES generation_requests(request_id),
    action TEXT NOT NULL CHECK (action = 'abandon_remote_result'),
    actor TEXT NOT NULL CHECK (length(trim(actor)) > 0),
    rationale TEXT NOT NULL CHECK (length(trim(rationale)) > 0),
    evidence_uri TEXT,
    created_at TEXT NOT NULL
);

CREATE TRIGGER creative_reconciliation_valid_insert
BEFORE INSERT ON creative_request_reconciliations
WHEN NOT EXISTS (
    SELECT 1 FROM generation_requests r
    WHERE r.request_id=NEW.request_id AND r.status='ambiguous'
      AND r.prompt_version IS NOT NULL AND length(trim(r.prompt_version)) > 0
      AND r.messages_json IS NOT NULL AND json_valid(r.messages_json)
      AND r.response_content IS NULL AND r.response_sha256 IS NULL
      AND r.kind IN ('subject_pool', 'subject_pool_repair',
          'episode_spec', 'episode_spec_repair', 'lyrics', 'lyrics_repair',
          'music_spec', 'music_spec_repair', 'episode_visual_plan',
          'episode_visual_plan_repair', 'publication_metadata', 'publication_metadata_repair')
      AND ((r.episode_id IS NOT NULL AND EXISTS (
          SELECT 1 FROM episodes e WHERE e.episode_id=r.episode_id))
          OR (r.run_id IS NOT NULL AND EXISTS (
          SELECT 1 FROM creative_runs c WHERE c.run_id=r.run_id)))
) OR EXISTS (
    SELECT 1 FROM creative_request_reconciliations WHERE request_id=NEW.request_id
) OR EXISTS (
    WITH RECURSIVE replacements(request_id, status) AS (
        SELECT request_id,status FROM generation_requests WHERE request_id=NEW.request_id
        UNION
        SELECT r.request_id,r.status FROM generation_requests r
        JOIN replacements p ON r.previous_attempt_id=p.request_id
            OR r.parent_request_id=p.request_id
    ) SELECT 1 FROM replacements WHERE status='succeeded'
)
BEGIN SELECT RAISE(ABORT, 'request is not eligible for creative abandonment'); END;

CREATE TRIGGER creative_reconciliation_no_update
BEFORE UPDATE ON creative_request_reconciliations
BEGIN SELECT RAISE(ABORT, 'creative reconciliations are immutable'); END;
CREATE TRIGGER creative_reconciliation_no_delete
BEFORE DELETE ON creative_request_reconciliations
BEGIN SELECT RAISE(ABORT, 'creative reconciliations are immutable'); END;

-- Even an alternate ledger API cannot rewrite or delete an abandoned ambiguous receipt.
CREATE TRIGGER creative_reconciled_request_no_update
BEFORE UPDATE ON generation_requests
WHEN EXISTS (SELECT 1 FROM creative_request_reconciliations WHERE request_id=OLD.request_id)
BEGIN SELECT RAISE(ABORT, 'reconciled creative requests are immutable'); END;
CREATE TRIGGER creative_reconciled_request_no_delete
BEFORE DELETE ON generation_requests
WHEN EXISTS (SELECT 1 FROM creative_request_reconciliations WHERE request_id=OLD.request_id)
BEGIN SELECT RAISE(ABORT, 'reconciled creative requests are immutable'); END;
CREATE TRIGGER creative_reconciled_request_no_replace
BEFORE INSERT ON generation_requests
WHEN EXISTS (SELECT 1 FROM creative_request_reconciliations WHERE request_id=NEW.request_id)
BEGIN SELECT RAISE(ABORT, 'reconciled creative requests are immutable'); END;
