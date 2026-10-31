CREATE TABLE platform_credentials (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES platform_users(id),
    provider TEXT NOT NULL,
    label TEXT NOT NULL,
    ciphertext_b64 TEXT NOT NULL,
    nonce_b64 TEXT NOT NULL,
    key_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_verified_at TEXT,
    revoked_at TEXT
);
CREATE INDEX idx_platform_credentials_user ON platform_credentials(user_id, status);
