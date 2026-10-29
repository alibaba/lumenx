# Aliyun Wan 3.0 frozen-byte temporary upload notes

Date: 2026-10-12
Scope: read-only official documentation research for future LumenX Wan 3.0 frozen-byte upload integration. This note records public documentation evidence only. No real DashScope API, OSS upload, or paid generation call was executed.

## Direct facts from official documentation

### Temporary upload policy

DashScope temporary uploads are obtained with a policy request using the model that will later consume the file:

- `GET /api/v1/uploads?action=getPolicy&model={model}`
- Headers include `Authorization: Bearer ...` and `Content-Type: application/json`.
- The file is bound to the model named in the policy request, so generation should use the same model id.

The policy response `data` includes at least these fields:

- `policy`
- `signature`
- `upload_dir`
- `upload_host`
- `expire_in_seconds`
- `max_file_size_mb`
- `capacity_limit_mb`
- `oss_access_key_id`
- `x_oss_object_acl`
- `x_oss_forbid_overwrite`

Source: <https://help.aliyun.com/en/model-studio/get-temporary-file-url> and <https://help.aliyun.com/zh/model-studio/get-temporary-file-url/>

### Form upload contract

The binary upload is a form POST to the returned `data.upload_host`, not to the video generation endpoint. Official examples include these form fields:

- `OSSAccessKeyId = data.oss_access_key_id`
- `policy = data.policy`
- `Signature = data.signature`
- `key = data.upload_dir + "/" + filename`
- `x-oss-object-acl = data.x_oss_object_acl`
- `x-oss-forbid-overwrite = data.x_oss_forbid_overwrite`
- `file`

`file` must be the last form field. `success_action_status=200` is optional in the official example. A successful upload returns HTTP 200 with no response body.

The example policy uses private ACL and overwrite protection values returned by the policy response, such as `x_oss_object_acl: private` and `x_oss_forbid_overwrite: true`. Implementations should use the returned values rather than hard-code them.

Source: <https://help.aliyun.com/en/model-studio/get-temporary-file-url>

### Temporary `oss://` URL

After upload succeeds, the temporary file reference is built as:

```text
oss://{uploaded form key}
```

The URL is valid for 48 hours. Uploaded files cannot be queried, modified, or downloaded through the temporary-upload API; they are intended only as model-call inputs.

Source: <https://help.aliyun.com/en/model-studio/get-temporary-file-url> and <https://help.aliyun.com/zh/model-studio/get-temporary-file-url/>

### `X-DashScope-OssResourceResolve` header

When using a temporary `oss://` URL in an HTTP generation request, the request must include:

```text
X-DashScope-OssResourceResolve: enable
```

The official documentation says SDKs add this header automatically for temporary URLs, but OpenAI SDK calls do not support this automatic behavior. A direct HTTP provider should add the header explicitly whenever it sends temporary `oss://` media references.

Source: <https://help.aliyun.com/zh/model-studio/get-temporary-file-url/> and <https://help.aliyun.com/en/model-studio/get-temporary-file-url>

### Wan 3.0 generation endpoint

Wan 3.0 video generation uses a workspace gateway endpoint, for example:

```text
https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis
```

Other regions use the same workspace-id subdomain pattern for that region. HTTP calls are asynchronous and require `X-DashScope-Async: enable`.

Source: <https://help.aliyun.com/en/model-studio/wan3-video-generation-api-reference>

### Media limits and data URI support

Wan 3.0 image inputs (`first_frame`, `last_frame`, `reference_image`) support public HTTP/HTTPS URLs, temporary `oss://` URLs, and Base64 data URI. Constraints include:

- formats: JPEG/JPG/PNG/BMP/WEBP;
- PNG transparent channels are not supported;
- each side between 240 and 8000 pixels;
- aspect ratio not greater than 8:1;
- file size up to 20 MB.

Wan 3.0 video inputs (`reference_video`) list public URL and temporary `oss://` URL inputs, not data URI. Constraints include:

- formats: mp4 and mov;
- each clip duration 1 to 15 seconds;
- total reference-video duration up to 15 seconds;
- frame rate at least 16 fps;
- each side between 240 and 4096 pixels;
- aspect ratio not greater than 8:1;
- file size up to 100 MB.

Wan 3.0 audio inputs (`reference_audio`) list public URL and temporary `oss://` URL inputs, not data URI. Constraints include:

- formats: wav and mp3;
- each clip duration 1 to 15 seconds;
- total reference-audio duration up to 15 seconds;
- file size up to 15 MB.

Wan 3.0 file inputs list office/text document formats and a 100 MB size limit, with page-count limits for some formats.

Source: <https://help.aliyun.com/en/model-studio/wan3-video-generation-api-reference>

### Documented upload and reference errors

The temporary upload documentation lists these relevant failures:

- policy expired: HTTP 403 `AccessDenied`; request a new credential;
- policy request throttled: HTTP 429 `Throttling.RateQuota`; 100 QPS per account per model;
- temporary OSS URL malformed or not resolved by header: HTTP 400 errors such as `invalid_parameter_error` or `InvalidParameter.DataInspection`.

Source: <https://help.aliyun.com/en/model-studio/get-temporary-file-url>

## Inferences for LumenX design

- LumenX should upload frozen bytes, not re-read mutable local paths during provider execution.
- The upload policy `model` should match the exact Wan 3.0 model used for generation, such as `wan3.0-video` or `wan3.0-video-prime`.
- The provider should preserve the returned upload policy fields and should not hard-code ACL or overwrite policy values.
- Any request that includes temporary `oss://` media should set `X-DashScope-OssResourceResolve: enable` explicitly.
- Video and audio data URI support should not be implemented for Wan 3.0 unless a later official source says it is supported. The current official Wan 3.0 page only lists data URI for images.
- Legacy helpers that create DashScope temporary URLs for older flows are risky to copy directly if they assume global endpoints, mutable file paths, global OSS configuration, or implicit header behavior.

## Unknowns and unresolved points

- The manual HTTP temporary-upload example still shows `https://dashscope.aliyuncs.com/api/v1/uploads`, while the CLI example on the same temporary-upload page can be configured with `DASHSCOPE_HTTP_BASE_URL="https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1"` before running `dashscope oss upload`. Wan 3.0 generation itself clearly uses the workspace gateway. The documentation does not provide a direct manual curl example for `GET https://{WorkspaceId}.{region}.maas.aliyuncs.com/api/v1/uploads?action=getPolicy&model=...`.
- Because of that gap, LumenX should treat the upload-policy base URL as a provider runtime seam and test both identity and region binding. It should not silently fall back from an invalid workspace endpoint to the classic global endpoint.
- This note does not prove runtime behavior, regional availability, or account-level policy support. It only records official documentation as of 2026-10-12.
