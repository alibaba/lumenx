CREATE TABLE platform_skill_versions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('system','workspace','user','project')),
    owner_id TEXT REFERENCES platform_users(id),
    project_id TEXT,
    parent_version TEXT,
    manifest_json TEXT NOT NULL,
    content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','active','revoked')),
    created_at TEXT NOT NULL,
    UNIQUE(name, version, scope, owner_id, project_id)
);

CREATE TABLE platform_skill_bindings (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('system','workspace','user','project')),
    owner_id TEXT REFERENCES platform_users(id),
    project_id TEXT,
    skill_version_id TEXT NOT NULL REFERENCES platform_skill_versions(id),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(name, scope, owner_id, project_id)
);

CREATE INDEX idx_platform_skill_versions_lookup ON platform_skill_versions(name, scope, owner_id, project_id, status);
CREATE INDEX idx_platform_skill_bindings_lookup ON platform_skill_bindings(name, scope, owner_id, project_id, active);
