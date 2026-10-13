message_id VARCHAR(128) PRIMARY KEY CHECK (btrim(message_id) <> ''),
direction VARCHAR(16) NOT NULL DEFAULT 'VWP_TO_OA' CHECK (direction = 'VWP_TO_OA'),
message_type VARCHAR(64) NOT NULL DEFAULT 'PING'
    CHECK (message_type IN ('PING', 'ANALYSIS_BUNDLE', 'EVIDENCE_RESPONSE')),
payload JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(payload) = 'object'),
status VARCHAR(16) NOT NULL DEFAULT 'READY'
    CHECK (status IN ('READY', 'PROCESSING', 'RETRY', 'RECEIVED', 'REJECTED')),
error_message TEXT CHECK (error_message IS NULL OR length(error_message) <= 1000),
created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
project VARCHAR(256) NOT NULL CHECK (btrim(project) <> ''),
run_id VARCHAR(100) NOT NULL CHECK (run_id ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$'),
bundle_id VARCHAR(100) CHECK (bundle_id IS NULL OR bundle_id ~ '^b-[0-9a-f]{64}$'),
correlation_id VARCHAR(128) CHECK (correlation_id IS NULL OR btrim(correlation_id) <> ''),
payload_gzip BYTEA,
payload_sha256 VARCHAR(64),
uncompressed_bytes BIGINT,
attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
lease_token UUID,
lease_owner VARCHAR(100),
lease_expires_at TIMESTAMPTZ,
completed_at TIMESTAMPTZ,
CHECK ((payload_gzip IS NULL AND payload_sha256 IS NULL AND uncompressed_bytes IS NULL)
    OR (payload_gzip IS NOT NULL AND payload_sha256 IS NOT NULL AND uncompressed_bytes IS NOT NULL
        AND octet_length(payload_gzip) BETWEEN 1 AND 67108864
        AND uncompressed_bytes BETWEEN 1 AND 67108864
        AND payload_sha256 = encode(sha256(payload_gzip), 'hex'))),
CHECK ((message_type = 'PING' AND bundle_id IS NULL AND correlation_id IS NULL AND payload_gzip IS NULL)
    OR (message_type = 'ANALYSIS_BUNDLE' AND bundle_id IS NOT NULL AND correlation_id IS NULL AND payload_gzip IS NOT NULL)
    OR (message_type = 'EVIDENCE_RESPONSE' AND bundle_id IS NOT NULL AND correlation_id IS NOT NULL
        AND COALESCE(payload->>'result' IN ('FULFILLED', 'REJECTED', 'EXPIRED'), false)
        AND ((payload->>'result' = 'FULFILLED') = (payload_gzip IS NOT NULL)))),
CHECK ((status = 'PROCESSING' AND lease_token IS NOT NULL AND lease_owner IS NOT NULL
        AND btrim(lease_owner) <> '' AND lease_expires_at IS NOT NULL)
    OR (status <> 'PROCESSING' AND lease_token IS NULL AND lease_owner IS NULL AND lease_expires_at IS NULL)),
CHECK ((status IN ('RECEIVED', 'REJECTED')) = (completed_at IS NOT NULL))
