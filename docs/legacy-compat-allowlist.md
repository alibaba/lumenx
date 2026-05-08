# Legacy Compatibility Allowlist

This file lists legacy names that are allowed to remain in tests or compatibility
paths. They must not be used as product defaults, fixture templates, README
setup instructions, Docker defaults, or startup scripts unless the entry below
explicitly says so.

## Image Model Alias

- `gpt-image-2`
  - Allowed only in tests marked with `@pytest.mark.legacy_compat`.
  - Purpose: prove historical OpenAI image-model aliases do not regress request
    routing, fallback keys, or moderation/429 behavior.
  - Not allowed in `.env`, Docker files, frontend defaults, fixture scripts, or
    story-project manifests. The product default is `gpt-image2`.

## Object Storage Aliases

- `OSS_BUCKET_NAME`
- `OSS_ENDPOINT`
- `OSS_BASE_PATH`

These names are allowed as backward-compatible aliases for older OSS runtime
configuration. New UI and API payloads should prefer:

- `OBJECT_STORAGE_BUCKET_NAME`
- `OBJECT_STORAGE_ENDPOINT`
- `OBJECT_STORAGE_BASE_PATH`

## Project Data Compatibility

- Model fields tagged as `[LEGACY]` in `src/apps/comic_gen/models.py`.
  - Purpose: load older projects without data loss while newer asset/unit
    structures take priority.
- Pipeline comments or metadata that mention `legacy` for old project imports,
  variant migration, or first-path compatibility.
  - Purpose: preserve old project behavior during import/export and rendering.

## Business-Semantic Legacy Labels

- `character_legacy`
  - Purpose: front-end upload/source semantics for historical character video
    references.
  - This is not a model default or image-generation alias.

## Provider Compatibility Paths

- DashScope legacy image-model paths.
  - Purpose: keep old Wan/DashScope adapters callable for projects already
    configured to use them.
  - New Image2 fixture workflows should continue to use `gpt-image2` through
    the OpenAI-compatible image generation and image edit paths.
