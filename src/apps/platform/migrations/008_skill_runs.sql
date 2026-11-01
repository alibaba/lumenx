CREATE TABLE platform_skill_runs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES platform_users(id),
    project_id TEXT NOT NULL,
    skill_name TEXT NOT NULL,
    skill_version TEXT NOT NULL,
    skill_content_hash TEXT NOT NULL CHECK (length(skill_content_hash) = 64),
    request_fingerprint TEXT NOT NULL CHECK (length(request_fingerprint) = 64),
    status TEXT NOT NULL CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
    input_hash TEXT,
    output_hash TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, request_fingerprint)
);

CREATE INDEX idx_platform_skill_runs_user ON platform_skill_runs(user_id, created_at);
CREATE INDEX idx_platform_skill_runs_project ON platform_skill_runs(project_id, created_at);
