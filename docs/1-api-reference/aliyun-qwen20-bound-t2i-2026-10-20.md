# Qwen Image 2.0 strict T2I integration evidence

Captured: 2026-10-20. Provider: Alibaba Cloud DashScope. Scope: `qwen-image-2.0` and `qwen-image-2.0-pro`, text-to-image only.

Source: [Alibaba Cloud Qwen text-to-image API](https://help.aliyun.com/zh/model-studio/qwen-image-api).

The synchronous API uses `/api/v1/services/aigc/multimodal-generation/generation`, with a user message containing one text item. Image URLs are returned in assistant message content under `output.choices`. This slice uses one image per request.

The documented negative prompt limit is 500 characters. Image area is between 512² and 2048² pixels; dimensions are not individually capped at 2048. Prompt rewriting defaults to true, watermark to false, and seed is optional in [0, 2147483647]. Classic Beijing/Singapore domains remain usable. The page currently describes the Pro alias as matching the 2026-04-22 variant; no alias/version changes are made here.

Implementation: new explicit `provider-qwen20-t2i-bound-http-v1` profile, same Core schema v2. Effective flags are frozen. Negative prompts exceeding the documented limit are rejected instead of truncated. The 16 KiB positive-prompt ceiling is a local memory bound, **not** validation of the provider's 1300-token limit. Existing unversioned Qwen and strict Wan records retain their original paths. Catalog only adds four missing existing Studio aspect sizes; defaults and visible models are unchanged.

Evidence grade: official documentation plus fake-HTTP regression tests; no live paid-provider acceptance. Mode B repo-only staging mirror: promotion to the canonical raw archive / Context Hub remains outstanding.
