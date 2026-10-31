CREATE TABLE credit_pricing (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    pricing_version TEXT NOT NULL,
    input_credits_per_1k INTEGER NOT NULL CHECK (input_credits_per_1k >= 0),
    output_credits_per_1k INTEGER NOT NULL CHECK (output_credits_per_1k >= 0),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    created_at TEXT NOT NULL,
    UNIQUE(provider, model, pricing_version)
);

CREATE TABLE credit_ledger (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES platform_users(id),
    amount_credits INTEGER NOT NULL CHECK (amount_credits > 0),
    direction TEXT NOT NULL CHECK (direction IN ('credit','debit','release','refund')),
    reference_type TEXT NOT NULL,
    reference_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE credit_reservations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES platform_users(id),
    generation_id TEXT NOT NULL,
    estimated_credits INTEGER NOT NULL CHECK (estimated_credits > 0),
    settled_credits INTEGER NOT NULL DEFAULT 0 CHECK (settled_credits >= 0),
    released_credits INTEGER NOT NULL DEFAULT 0 CHECK (released_credits >= 0),
    pricing_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'reserved' CHECK (status IN ('reserved','settled','released','refunded')),
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_credit_ledger_user_time ON credit_ledger(user_id, created_at DESC);
CREATE INDEX idx_credit_reservations_user_status ON credit_reservations(user_id, status);

INSERT INTO credit_pricing VALUES
('pricing-dashscope-qwen37-plus-v1','dashscope','qwen3.7-plus','2026-10-31.1',1,4,1,'2026-10-31T00:00:00+00:00'),
('pricing-dashscope-qwen36-plus-v1','dashscope','qwen3.6-plus','2026-10-31.1',1,4,1,'2026-10-31T00:00:00+00:00'),
('pricing-dashscope-qwen-plus-v1','dashscope','qwen-plus','2026-10-31.1',1,3,1,'2026-10-31T00:00:00+00:00');
