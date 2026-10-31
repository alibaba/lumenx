CREATE TABLE platform_token_usage (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES platform_users(id),
    product TEXT NOT NULL CHECK (product IN ('studio','atelier','core')),
    operation TEXT NOT NULL,
    provider TEXT NOT NULL,
    requested_model TEXT,
    provider_model TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0 CHECK (prompt_tokens >= 0),
    completion_tokens INTEGER NOT NULL DEFAULT 0 CHECK (completion_tokens >= 0),
    total_tokens INTEGER NOT NULL DEFAULT 0 CHECK (total_tokens >= 0),
    request_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX idx_platform_token_usage_user_time ON platform_token_usage(user_id, created_at DESC);
CREATE INDEX idx_platform_token_usage_product_time ON platform_token_usage(product, created_at DESC);
