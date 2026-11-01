CREATE TABLE competitions (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, slug TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '', rules_markdown TEXT NOT NULL DEFAULT '',
    whitelist_required INTEGER NOT NULL DEFAULT 1 CHECK (whitelist_required IN (0,1)),
    allocated_credits INTEGER NOT NULL DEFAULT 0 CHECK (allocated_credits >= 0),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','enrolling','ongoing','judging','ended')),
    enroll_deadline TEXT, submit_deadline TEXT, created_at TEXT NOT NULL
);
CREATE TABLE competition_whitelists (
    id TEXT PRIMARY KEY, competition_id TEXT NOT NULL REFERENCES competitions(id),
    user_id TEXT NOT NULL REFERENCES platform_users(id), invite_code TEXT,
    status TEXT NOT NULL DEFAULT 'approved' CHECK (status IN ('pending','approved','rejected')),
    created_at TEXT NOT NULL, UNIQUE(competition_id,user_id)
);
CREATE TABLE platform_submissions (
    id TEXT PRIMARY KEY, target_type TEXT NOT NULL CHECK (target_type IN ('competition','certification_exam')),
    target_id TEXT NOT NULL, user_id TEXT NOT NULL REFERENCES platform_users(id),
    artifact_id TEXT NOT NULL, artifact_version TEXT NOT NULL, sha256 TEXT NOT NULL,
    media_type TEXT NOT NULL, project_id TEXT NOT NULL, project_title_snapshot TEXT NOT NULL,
    title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', score REAL,
    award_title TEXT, status TEXT NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted','reviewing','approved','rejected')),
    created_at TEXT NOT NULL
);
CREATE TABLE courses (id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', video_url TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'published');
CREATE TABLE templates (id TEXT PRIMARY KEY, title TEXT NOT NULL, source_project_id TEXT NOT NULL, version TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'published');
CREATE TABLE template_forks (id TEXT PRIMARY KEY, template_id TEXT NOT NULL REFERENCES templates(id), user_id TEXT NOT NULL REFERENCES platform_users(id), fork_project_id TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(template_id,user_id,fork_project_id));
CREATE TABLE certification_exams (id TEXT PRIMARY KEY, prompt TEXT NOT NULL, version TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE certification_applications (id TEXT PRIMARY KEY, exam_id TEXT NOT NULL REFERENCES certification_exams(id), user_id TEXT NOT NULL REFERENCES platform_users(id), submission_id TEXT NOT NULL REFERENCES platform_submissions(id), status TEXT NOT NULL DEFAULT 'reviewing' CHECK (status IN ('reviewing','certified','rejected')), score REAL, feedback TEXT, created_at TEXT NOT NULL);
CREATE TABLE creator_certifications (id TEXT PRIMARY KEY, user_id TEXT NOT NULL UNIQUE REFERENCES platform_users(id), status TEXT NOT NULL DEFAULT 'unapplied', level TEXT NOT NULL DEFAULT 'standard', source_type TEXT NOT NULL, submission_id TEXT, score REAL, feedback TEXT, certified_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE commercial_tasks (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL, brief_markdown TEXT NOT NULL,
    bounty_cents INTEGER NOT NULL CHECK (bounty_cents >= 0), required_level TEXT NOT NULL DEFAULT 'standard',
    claimed_by TEXT REFERENCES platform_users(id), claim_time TEXT, deadline TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','claimed','in_progress','delivered','revision_requested','resubmitted','accepted','settlement_pending','settled','closed','cancelled','expired','disputed','refunded')),
    version INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE task_deliveries (
    id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES commercial_tasks(id), creator_id TEXT NOT NULL REFERENCES platform_users(id),
    artifact_id TEXT NOT NULL, artifact_version TEXT NOT NULL, sha256 TEXT NOT NULL, media_type TEXT NOT NULL,
    project_id TEXT NOT NULL, project_title_snapshot TEXT NOT NULL, version_num INTEGER NOT NULL,
    delivery_note TEXT NOT NULL DEFAULT '', review_status TEXT NOT NULL DEFAULT 'pending', review_feedback TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
    UNIQUE(task_id, version_num)
);
CREATE TABLE task_history (id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES commercial_tasks(id), from_status TEXT, to_status TEXT NOT NULL, actor_id TEXT NOT NULL, reason TEXT, idempotency_key TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE idempotency_records (actor_id TEXT NOT NULL, key TEXT NOT NULL, response_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(actor_id,key));
CREATE TABLE ledger_accounts (id TEXT PRIMARY KEY, user_id TEXT NOT NULL UNIQUE REFERENCES platform_users(id), created_at TEXT NOT NULL);
CREATE TABLE ledger_entries (id TEXT PRIMARY KEY, transaction_id TEXT NOT NULL, account_id TEXT NOT NULL REFERENCES ledger_accounts(id), amount_cents INTEGER NOT NULL CHECK(amount_cents > 0), direction TEXT NOT NULL CHECK(direction IN ('debit','credit')), reference_type TEXT NOT NULL, reference_id TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
CREATE TABLE audit_events_ext (id TEXT PRIMARY KEY, actor_id TEXT, request_id TEXT NOT NULL, event_type TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT, payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX idx_tasks_status ON commercial_tasks(status, deadline);
CREATE INDEX idx_submissions_target ON platform_submissions(target_type,target_id);
