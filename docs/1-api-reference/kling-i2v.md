# 可灵 API — 图生视频 (Image to Video)

> 来源: 可灵 AI 开发者文档 (青雀文档)
> 原文链接: https://docs.qingque.cn/d/home/eZQAyImcbaS0fz-8ANjXvU5ed?identityId=2Cn18n4EIHT

---

## 3-7 【图生视频】创建任务

| 项目 | 值 |
|---|---|
| 网络协议 | https |
| 请求地址 | /v1/videos/image2video |
| 请求方法 | POST |
| 请求格式 | application/json |
| 响应格式 | application/json |

### 请求头

| 字段 | 值 | 描述 |
|---|---|---|
| Content-Type | application/json | 数据交换格式 |
| Authorization | 鉴权信息，参考接口鉴权 | 鉴权信息，参考接口鉴权 |

### 请求体

> 请您注意，为了保持命名统一，原 `model` 字段变更为 `model_name` 字段，未来请您使用该字段来指定需要调用的模型版本。
> 同时，我们保持了行为上的向前兼容，如您继续使用原 `model` 字段，不会对接口调用有任何影响、不会有任何异常，等价于 `model_name` 为空时的默认行为（即调用V1模型）

| 字段 | 类型 | 必填 | 默认值 | 描述 |
|---|---|---|---|---|
| model_name | string | 可选 | kling-v1 | 模型名称。枚举值：`kling-v1`, `kling-v1-5`, `kling-v1-6`, `kling-v2-master`, `kling-v2-1`, `kling-v2-1-master`, `kling-v2-5-turbo`, `kling-v2-6`, `kling-v3` |
| image | string | 可选 | 空 | 参考图像。支持传入图片 Base64 编码或图片 URL（确保可访问）。请不要在 Base64 编码字符串前添加任何前缀（如 `data:image/png;base64,`）。图片格式支持 `.jpg / .jpeg / .png`，文件大小不超过 10MB，宽高尺寸不小于 300px，宽高比介于 1:2.5 ~ 2.5:1 之间。`image` 参数与 `image_tail` 参数至少二选一，二者不能同时为空。 |
| image_tail | string | 可选 | 空 | 参考图像 - 尾帧控制。格式要求同 `image` 字段。`image_tail` 参数、`dynamic_masks`/`static_mask` 参数、`camera_control` 参数三选一，不能同时使用。 |
| multi_shot | boolean | 可选 | false | 是否生成多镜头视频。为 `true` 时 `prompt` 参数无效；为 `false` 时 `shot_type` 及 `multi_prompt` 参数无效。 |
| shot_type | string | 可选 | 空 | 分镜方式。枚举值：`customize`，`intelligence`。当 `multi_shot` 为 `true` 时必填。 |
| prompt | string | 可选 | 空 | 文本提示词，可包含正向描述和负向描述。不能超过 2500 个字符。Omni 模型可通过 `<<<element_1>>>`、`<<<image_1>>>`、`<<<video_1>>>` 指定主体/图片/视频。用 `<<<voice_1>>>` 指定音色（序号同 `voice_list` 排列顺序）。当 `multi_shot` 为 `false` 或 `shot_type` 为 `intelligence` 时必填。 |
| multi_prompt | array | 可选 | 空 | 各分镜信息（提示词、时长等）。最多 6 个分镜，最小 1 个分镜。每个分镜内容最大长度不超过 512。每个分镜时长不大于总时长且不小于 1。所有分镜时长之和等于总时长。格式：`[{"index": int, "prompt": "string", "duration": "5"}, ...]`。当 `multi_shot` 为 `true` 且 `shot_type` 为 `customize` 时不得为空。 |
| negative_prompt | string | 可选 | 空 | 负向文本提示词。不能超过 2500 个字符。 |
| element_list | array | 可选 | 空 | 参考主体列表。格式：`[{"element_id": long}, ...]`。最多 3 个参考主体。与 `voice_list` 互斥，不能共存。 |
| voice_list | array | 可选 | 无 | 引用的音色列表。至多 2 个音色。格式：`[{"voice_id": "voice_id_1"}, ...]`。与 `element_list` 互斥，不能共存。 |
| sound | string | 可选 | off | 是否同时生成声音。枚举值：`on`，`off`。仅 V2.6 及后续版本模型支持。 |
| cfg_scale | float | 可选 | 0.5 | 生成视频的自由度。值越大模型自由度越小，与提示词相关性越强。取值范围：[0, 1]。kling-v2.x 模型不支持此参数。 |
| mode | string | 可选 | std | 生成视频的模式。枚举值：`std`（标准模式，性价比高），`pro`（专家模式，质量更佳）。 |
| static_mask | string | 可选 | 无 | 静态笔刷涂抹区域 mask 图片。图片长宽比必须与输入图片相同。 |
| dynamic_masks | array | 可选 | 无 | 动态笔刷配置列表，最多 6 组。每组包含 `mask`（涂抹区域）和 `trajectories`（运动轨迹坐标序列，最多 77 个点，坐标原点为图片左下角）。格式：`[{"mask": "string", "trajectories": [{"x": int, "y": int}, ...]}, ...]` |
| camera_control | object | 可选 | 空 | 控制摄像机运动。未指定时模型智能匹配。 |
| camera_control.type | string | 可选 | 无 | 预定义运镜类型。枚举值：`simple`（简单运镜，需配合 config 六选一），`down_back`（下移拉远），`forward_up`（推进上移），`right_turn_forward`（右旋推进），`left_turn_forward`（左旋推进）。 |
| camera_control.config | object | 可选 | 无 | 运镜类型为 `simple` 时必填。以下 6 个参数只能有一个不为 0：`horizontal`（水平平移 [-10,10]），`vertical`（垂直平移 [-10,10]），`pan`（水平摇镜 [-10,10]），`tilt`（垂直摇镜 [-10,10]），`roll`（旋转 [-10,10]），`zoom`（变焦 [-10,10]）。 |
| duration | string | 可选 | 5 | 生成视频时长，单位秒。**枚举值：3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15** |
| watermark_info | object | 可选 | 空 | 是否生成含水印结果。格式：`{"enabled": boolean}`。 |
| callback_url | string | 可选 | 无 | 任务结果回调通知地址。 |
| external_task_id | string | 可选 | 无 | 自定义任务 ID，单用户下需保证唯一性。 |

### 响应体

```json
{
    "code": 0,
    "message": "string",
    "request_id": "string",
    "data": {
        "task_id": "string",
        "task_info": {
            "external_task_id": "string"
        },
        "task_status": "string",  // submitted | processing | succeed | failed
        "created_at": 1722769557708,
        "updated_at": 1722769557708
    }
}
```

### 调用示例

#### 多镜头效果的图生视频

```bash
curl --location 'https://xxx/v1/videos/image2video' \
--header 'Authorization: Bearer xxx' \
--header 'Content-Type: application/json' \
--data '{
    "model_name": "kling-v3",
    "image": "xxx",
    "prompt": "",
    "multi_shot": "true",
    "shot_type": "customize",
    "multi_prompt": [
        {"index": 1, "prompt": "Two friends talking under a streetlight at night.", "duration": "2"},
        {"index": 2, "prompt": "A runner sprinting through a forest, leaves flying.", "duration": "3"},
        {"index": 3, "prompt": "A woman hugging a cat, smiling.", "duration": "3"},
        {"index": 4, "prompt": "A door creaking open, shadowy hallway.", "duration": "3"},
        {"index": 5, "prompt": "A man slipping on a banana peel, shocked expression.", "duration": "3"},
        {"index": 6, "prompt": "A sunset over mountains, small figure walking away.", "duration": "1"}
    ],
    "negative_prompt": "",
    "duration": "15",
    "mode": "pro",
    "sound": "on"
}'
```

#### 指定音色生成视频

```bash
curl --location 'https://api-beijing.klingai.com/v1/videos/image2video/' \
--header 'Authorization: Bearer {token}' \
--header 'Content-Type: application/json; charset=utf-8' \
--data '{
    "model_name": "kling-v2-6",
    "image": "图片链接",
    "prompt": "<<<voice_1>>>让图中人物说出以下文字：热烈欢迎大家",
    "voice_list": [{"voice_id": "音色id"}],
    "duration": "5",
    "mode": "pro",
    "sound": "on"
}'
```

---

## 3-8 【图生视频】查询任务（单个）

| 项目 | 值 |
|---|---|
| 请求地址 | /v1/videos/image2video/{id} |
| 请求方法 | GET |

### 请求路径参数

| 字段 | 类型 | 必填 | 默认值 | 描述 |
|---|---|---|---|---|
| task_id | string | 可选 | 无 | 图生视频的任务 ID（路径参数），与 `external_task_id` 二选一 |
| external_task_id | string | 可选 | 无 | 图生视频的自定义任务 ID，与 `task_id` 二选一 |

### 响应体

```json
{
    "code": 0,
    "message": "string",
    "request_id": "string",
    "data": {
        "task_id": "string",
        "task_status": "string",  // submitted | processing | succeed | failed
        "task_status_msg": "string",
        "task_info": {
            "external_task_id": "string"
        },
        "task_result": {
            "videos": [
                {
                    "id": "string",
                    "url": "string",
                    "watermark_url": "string",
                    "duration": "string"
                }
            ]
        },
        "watermark_info": {"enabled": false},
        "final_unit_deduction": "string",
        "created_at": 1722769557708,
        "updated_at": 1722769557708
    }
}
```

> 注意：生成的视频会在 30 天后被清理，请及时转存。

---

## 3-9 【图生视频】查询任务（列表）

| 项目 | 值 |
|---|---|
| 请求地址 | /v1/videos/image2video?pageNum=1&pageSize=30 |
| 请求方法 | GET |

### 查询参数

| 字段 | 类型 | 必填 | 默认值 | 描述 |
|---|---|---|---|---|
| pageNum | int | 可选 | 1 | 页码，取值范围 [1, 1000] |
| pageSize | int | 可选 | 30 | 每页数据量，取值范围 [1, 500] |

### 响应体

与单个查询相同结构，`data` 为数组形式。
