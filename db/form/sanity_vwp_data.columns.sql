record_id UUID PRIMARY KEY,
record_type VARCHAR(24) NOT NULL
    CHECK (record_type IN ('RUN', 'UNIT', 'FILE', 'GROUP', 'EVIDENCE')),
analysis_id UUID NOT NULL,
entity_key VARCHAR(256) NOT NULL CHECK (btrim(entity_key) <> ''),
review_unit_id VARCHAR(100),
parent_id UUID,
payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
state JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(state) = 'object'),
status VARCHAR(24) NOT NULL,
version BIGINT NOT NULL DEFAULT 0 CHECK (version >= 0),
raw_bytes BYTEA,
raw_sha256 VARCHAR(64) CHECK (raw_sha256 IS NULL OR raw_sha256 ~ '^[0-9a-f]{64}$'),
created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
UNIQUE (analysis_id, record_type, entity_key),
UNIQUE (analysis_id, record_id),
CHECK (review_unit_id IS NULL OR review_unit_id ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$'),
CHECK ((record_type = 'RUN' AND record_id = analysis_id AND parent_id IS NULL AND review_unit_id IS NULL)
    OR (record_type <> 'RUN' AND parent_id IS NOT NULL AND parent_id <> record_id)),
CHECK (record_type NOT IN ('UNIT', 'GROUP', 'EVIDENCE') OR review_unit_id IS NOT NULL),
CHECK ((record_type = 'RUN' AND status IN ('BUILDING', 'SEALED', 'FAILED'))
    OR (record_type = 'UNIT' AND status IN ('PENDING', 'COMPLETE', 'PARTIAL', 'MISSING', 'UNSUPPORTED', 'NOT_APPLICABLE'))
    OR (record_type IN ('FILE', 'GROUP', 'EVIDENCE') AND status = 'RECORDED')),
CHECK (record_type NOT IN ('FILE', 'EVIDENCE') OR (state = '{}'::jsonb AND version = 0 AND updated_at = created_at)),
CHECK (record_type = 'EVIDENCE' OR (raw_bytes IS NULL AND raw_sha256 IS NULL)),
CHECK (raw_bytes IS NULL OR (raw_sha256 IS NOT NULL
    AND octet_length(raw_bytes) BETWEEN 0 AND 67108864
    AND raw_sha256 = encode(sha256(raw_bytes), 'hex'))),
CHECK (record_type <> 'EVIDENCE' OR (raw_sha256 IS NOT NULL
    AND (raw_bytes IS NOT NULL OR COALESCE(btrim(payload->>'local_evidence_ref') <> '', false))))
