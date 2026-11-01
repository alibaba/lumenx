ALTER TABLE platform_skill_runs ADD COLUMN lease_owner TEXT;
ALTER TABLE platform_skill_runs ADD COLUMN lease_expires_at TEXT;
ALTER TABLE platform_skill_runs ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0);

CREATE INDEX idx_platform_skill_runs_claim ON platform_skill_runs(status, lease_expires_at, created_at);
