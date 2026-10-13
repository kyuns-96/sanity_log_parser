message_id VARCHAR(128) PRIMARY KEY CHECK (btrim(message_id) <> ''),
direction VARCHAR(16) NOT NULL DEFAULT 'OA_TO_VWP' CHECK (direction = 'OA_TO_VWP'),
message_type VARCHAR(64) NOT NULL DEFAULT 'PING' CHECK (message_type IN ('PING', 'EVIDENCE_REQUEST')),
payload JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(payload) = 'object'),
status VARCHAR(16) NOT NULL DEFAULT 'READY'
    CHECK (status IN ('READY', 'PROCESSING', 'RETRY', 'RECEIVED', 'REJECTED')),
error_message TEXT CHECK (error_message IS NULL OR length(error_message) <= 1000),
created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
project VARCHAR(256) NOT NULL CHECK (btrim(project) <> ''),
run_id VARCHAR(100) NOT NULL CHECK (run_id ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$'),
bundle_id VARCHAR(100) CHECK (bundle_id IS NULL OR bundle_id ~ '^b-[0-9a-f]{64}$'),
expires_at TIMESTAMPTZ,
attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
lease_token UUID,
lease_owner VARCHAR(100),
lease_expires_at TIMESTAMPTZ,
completed_at TIMESTAMPTZ,
CHECK ((message_type = 'PING' AND bundle_id IS NULL AND expires_at IS NULL)
    OR (message_type = 'EVIDENCE_REQUEST' AND bundle_id IS NOT NULL AND expires_at IS NOT NULL AND expires_at > created_at)),
CHECK ((status = 'PROCESSING' AND lease_token IS NOT NULL AND lease_owner IS NOT NULL
        AND btrim(lease_owner) <> '' AND lease_expires_at IS NOT NULL)
    OR (status <> 'PROCESSING' AND lease_token IS NULL AND lease_owner IS NULL AND lease_expires_at IS NULL)),
CHECK ((status IN ('RECEIVED', 'REJECTED')) = (completed_at IS NOT NULL))
