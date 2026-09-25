CREATE TABLE visual_benchmark_receipts (
    request_id TEXT PRIMARY KEY REFERENCES visual_benchmark_requests(request_id),
    provider_request_id TEXT,
    returned_sha256 TEXT NOT NULL CHECK (length(returned_sha256) = 64),
    returned_byte_count INTEGER NOT NULL CHECK (returned_byte_count > 0),
    mime_type TEXT NOT NULL,
    latency_seconds REAL NOT NULL CHECK (latency_seconds >= 0),
    usage_json TEXT,
    actual_cost_amount REAL,
    cost_currency TEXT,
    pricing_policy TEXT,
    response_metadata_json TEXT NOT NULL,
    received_at TEXT NOT NULL,
    CHECK ((actual_cost_amount IS NULL AND cost_currency IS NULL)
        OR (actual_cost_amount IS NOT NULL AND cost_currency IS NOT NULL))
);

CREATE TRIGGER visual_benchmark_receipts_no_update
BEFORE UPDATE ON visual_benchmark_receipts
BEGIN SELECT RAISE(ABORT, 'visual benchmark receipts are immutable'); END;

CREATE TRIGGER visual_benchmark_receipts_no_delete
BEFORE DELETE ON visual_benchmark_receipts
BEGIN SELECT RAISE(ABORT, 'visual benchmark receipts are immutable'); END;

CREATE TRIGGER visual_benchmark_success_requires_output
BEFORE UPDATE OF status ON visual_benchmark_requests
WHEN NEW.status = 'succeeded' AND (
    (SELECT count(*) FROM visual_benchmark_outputs WHERE request_id = NEW.request_id) <> 1
    OR NOT EXISTS (
        SELECT 1 FROM visual_benchmark_outputs o
        JOIN artifact_versions a ON a.artifact_id = o.artifact_id
        JOIN visual_benchmark_receipts r ON r.request_id = o.request_id
        WHERE o.request_id = NEW.request_id
          AND a.kind = 'benchmark_image'
          AND a.sha256 = r.returned_sha256
          AND a.byte_count = r.returned_byte_count
          AND a.mime_type = r.mime_type
          AND o.mime_type = r.mime_type
          AND a.owner_scope = 'brand'
          AND a.brand_revision_id = NEW.brand_revision_id
          AND json_extract(a.provenance_json, '$.local_request_id') = NEW.request_id
          AND json_extract(a.provenance_json, '$.request_id') IS r.provider_request_id
          AND json_extract(a.provenance_json, '$.provider') = NEW.provider
          AND json_extract(a.provenance_json, '$.model') = NEW.model
          AND json_extract(a.provenance_json, '$.prompt_version') = NEW.prompt_version
          AND (SELECT count(*) FROM artifact_dependencies d
               WHERE d.consumer_artifact_id = a.artifact_id) =
              json_array_length(NEW.canonical_spec_json, '$.references')
          AND (SELECT count(*) FROM artifact_dependencies d
               JOIN json_each(NEW.canonical_spec_json, '$.references') ref
                 ON d.input_artifact_id = json_extract(ref.value, '$.artifact_id')
                AND d.input_sha256 = json_extract(ref.value, '$.sha256')
                AND d.purpose = 'benchmark reference ' || json_extract(ref.value, '$.role')
               WHERE d.consumer_artifact_id = a.artifact_id) =
              json_array_length(NEW.canonical_spec_json, '$.references')
    )
)
BEGIN SELECT RAISE(ABORT, 'benchmark success requires matching receipt and output'); END;
