# Wan 3.0 shared execution migration evidence

Captured / rechecked: 2026-10-12, Asia/Shanghai. Scope: Alibaba Cloud Wan `wan3.0-video` and `wan3.0-video-prime`; repo-only model workflow (Mode B). This records official contract facts for migration of the existing S/A branches, not a new model launch or production acceptance. The external raw-doc archive and Context Hub have not been synchronized.

## Sources and version boundary

- [Wan 3.0 API](https://help.aliyun.com/zh/model-studio/wan3-video-generation-api-reference): model IDs, submission fields, parameter ranges/defaults and query results.
- [Wan 3.0 guide](https://help.aliyun.com/zh/model-studio/wan3-video-generation-guide): input roles, combinations and limits.
- [Async tasks](https://help.aliyun.com/zh/model-studio/manage-asynchronous-tasks): query scope, task lifetime and cancellation boundary.
- [Regions](https://help.aliyun.com/zh/model-studio/regions) and [API keys](https://help.aliyun.com/zh/model-studio/get-api-key): host, workspace and credential scope.

The API, guide, task and region pages expose HTML `dateModified` around 2026-10-10 11:45–11:46 +08:00. This is page metadata, not a model release date. The independently refreshed facts agree with the earlier [P5 evidence](../agents/raw/lumenx-p5-provider-facts.md), which retains detailed limits and unresolved contract questions.

## Confirmed migration constraints

The All-in-One request uses ordered `input.media` items with explicit `type` and `url`. First/last frames cannot be mixed with references; reference images, videos and audio can mix. File and link are mutually exclusive. User-selected media must never be silently dropped to rescue an incompatible request. Existing S support must be inventoried separately from the larger public API.

Defaults include duration 5, resolution 1080P and ratio adaptive. Duration -1 requests automatic length; seed -1 requests randomness and seed 0 is valid. Prompt extension defaults true and watermark false. Omission, false, zero and -1 must remain distinct. Output audio false means no output audio track.

The same-day implementation recheck confirms resolution options 480P/720P/1080P; ratios adaptive/21:9/16:9/4:3/1:1/3:4/9:16; duration 2..30 without video inputs, or combined input/output duration at most 30 with video. Output audio defaults true. Prompt accepts at most 20000 characters before provider truncation. These current facts supersede any narrower historical S catalog values; the application validates before submission instead of silently truncating or changing defaults.

Submit returns a provider task ID; query and media fetch are separate operations. The original provider/model/region/host/workspace/credential binding must survive restart. Task IDs and result URLs have expiry constraints; a failed query or download does not authorize another paid submission.

## Unresolved, not implicit product rules

- Official docs do not define the combined behavior of `audio=false` and `reference_audio`. Preserve both; do not force sound on or delete the reference.
- JSON null behavior is unspecified. The application must validate it explicitly, not coerce it to absence.
- No documented client submission idempotency key was found. A timeout without a usable task ID remains an unknown outcome.
- Public endpoint documentation does not prove account-specific model availability or permission. No account or provider calls were made.

Implementation, verification status and rollout gates remain in the [execution ledger](../agents/context/lumenx-first-principles/EXECUTION-STATUS.md); this document does not close P3/P5/P6.
