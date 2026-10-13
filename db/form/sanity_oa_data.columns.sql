record_id UUID PRIMARY KEY,
record_type VARCHAR(24) NOT NULL CHECK (record_type IN
    ('RECEIVED_BUNDLE', 'REVIEW_UNIT', 'REVIEW_EVENT', 'REPORT')),
project VARCHAR(256) NOT NULL CHECK (btrim(project) <> ''),
run_id VARCHAR(100) NOT NULL CHECK (run_id ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$'),
bundle_id VARCHAR(100) NOT NULL CHECK (bundle_id ~ '^b-[0-9a-f]{64}$'),
review_unit_id VARCHAR(100),
review_revision BIGINT CHECK (review_revision IS NULL OR review_revision >= 0),
request_id UUID,
payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
payload_bytes BYTEA,
payload_sha256 VARCHAR(64),
state JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(state) = 'object'),
status VARCHAR(16) NOT NULL DEFAULT 'RECORDED' CHECK (status = 'RECORDED'),
version BIGINT NOT NULL DEFAULT 0 CHECK (version >= 0),
created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
CHECK (review_unit_id IS NULL OR review_unit_id ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$'),
CHECK ((record_type = 'REVIEW_UNIT' AND review_unit_id IS NOT NULL)
    OR (record_type <> 'REVIEW_UNIT' AND review_unit_id IS NULL)),
CHECK ((record_type IN ('REVIEW_EVENT', 'REPORT') AND review_revision IS NOT NULL)
    OR (record_type NOT IN ('REVIEW_EVENT', 'REPORT') AND review_revision IS NULL)),
CHECK ((record_type = 'REVIEW_EVENT' AND
        ((review_revision = 0 AND request_id IS NULL) OR (review_revision > 0 AND request_id IS NOT NULL)))
    OR (record_type <> 'REVIEW_EVENT' AND request_id IS NULL)),
CHECK ((payload_bytes IS NULL AND payload_sha256 IS NULL)
    OR (payload_bytes IS NOT NULL AND payload_sha256 IS NOT NULL
        AND octet_length(payload_bytes) BETWEEN 1 AND 67108864
        AND payload_sha256 = encode(sha256(payload_bytes), 'hex'))),
CHECK (record_type <> 'REPORT' OR payload_bytes IS NOT NULL),
CHECK (record_type NOT IN ('REVIEW_UNIT', 'REVIEW_EVENT', 'REPORT')
    OR (state = '{}'::jsonb AND version = 0 AND updated_at = created_at))
