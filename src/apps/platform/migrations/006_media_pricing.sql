CREATE TABLE media_pricing (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    media_kind TEXT NOT NULL CHECK (media_kind IN ('image','video','audio')),
    pricing_version TEXT NOT NULL,
    credits_per_unit INTEGER NOT NULL CHECK (credits_per_unit > 0),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    created_at TEXT NOT NULL,
    UNIQUE(provider, model, media_kind, pricing_version)
);

CREATE INDEX idx_media_pricing_lookup ON media_pricing(provider, model, media_kind, active);

INSERT INTO media_pricing VALUES
('pricing-dashscope-image-default-v1','dashscope','*','image','2026-10-31.1',4,1,'2026-10-31T00:00:00+00:00'),
('pricing-kling-video-default-v1','kling','*','video','2026-10-31.1',20,1,'2026-10-31T00:00:00+00:00'),
('pricing-cosyvoice-audio-default-v1','dashscope','*','audio','2026-10-31.1',2,1,'2026-10-31T00:00:00+00:00');
