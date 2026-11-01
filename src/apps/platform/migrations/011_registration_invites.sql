CREATE TABLE registration_invites (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    max_uses INTEGER NOT NULL DEFAULT 1 CHECK (max_uses > 0),
    used_count INTEGER NOT NULL DEFAULT 0 CHECK (used_count >= 0),
    expires_at TEXT,
    created_by TEXT REFERENCES platform_users(id),
    created_at TEXT NOT NULL,
    last_used_at TEXT
);
CREATE INDEX idx_registration_invites_active ON registration_invites(code, used_count, expires_at);
