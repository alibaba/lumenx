# 万相-图生视频-基于首帧

> 作者: 参与者： 米昵、闻奕 等
> 发布时间: 2025-01-17
> 原文链接: https://help.aliyun.com/zh/model-studio/image-to-video-api-reference/

---
万相-图生视频模型根据**首帧图像**和**文本提示词**，生成一段流畅的视频。

**相关文档**：[使用指南](https://help.aliyun.com/zh/model-studio/image-to-video-guide)

## 适用范围

为确保调用成功，请务必保证模型、endpoint URL 和 API Key 均属于**同一地域**。跨地域调用将会失败。

：确认模型所属的地域。**选择模型****选择 URL**：选择对应的地域 Endpoint URL，支持HTTP URL或 DashScope SDK URL。**配置 API Key**：获取该地域的[API Key](https://help.aliyun.com/zh/model-studio/get-api-key)，再[配置API Key到环境变量](https://help.aliyun.com/zh/model-studio/configure-api-key-through-environment-variables)。**安装 SDK**：如需通过SDK进行调用，请[安装DashScope SDK](https://help.aliyun.com/zh/model-studio/install-sdk)。

本文的示例代码适用于**北京地域**。

## HTTP调用

图生视频任务耗时较长（通常为1-5分钟），API采用异步调用的方式。整个流程包含 **“创建任务 -> 轮询获取”** 两个核心步骤，具体如下：

### 步骤1：创建任务获取任务ID

`POST https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis`


`POST https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis`


`POST https://dashscope-us.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis`


创建成功后，使用接口返回的

`task_id`

查询结果，task_id 有效期为 24 小时。**请勿重复创建任务**，轮询获取即可。新手指引请参见

[Postman](https://help.aliyun.com/zh/model-studio/first-call-to-image-and-video-api)。

## 请求参数 | 多镜头叙事 自动配音 传入音频文件 生成无声视频 使用Base64 使用视频特效 使用反向提示词 仅wan2.6系列模型支持此功能。 可通过设置
仅wan2.6和wan2.5系列模型支持此功能。 若不提供
仅wan2.6和wan2.5系列模型支持此功能。 如需为视频指定背景音乐或配音，可通过
仅以下模型支持生成无声视频：
通过 关于 Base64 字符串的格式要求，请参见 示例：下载
通过 negative_prompt 指定生成的视频避免出现“花朵”元素。
|
## 请求头（Headers） | |
请求内容类型。此参数必须设置为 | |
请求身份认证。接口使用阿里云百炼API-Key进行身份认证。示例值：Bearer sk-xxxx。 | |
异步处理配置参数。HTTP请求只支持异步， 缺少此请求头将报错：“current user api does not support synchronous calls”。 | |
## 请求体（Request Body） | |
模型名称。模型列表与价格详见 示例值：wan2.6-i2v-flash。 | |
输入的基本信息，如提示词等。 | |
视频处理参数，如设置视频分辨率、设置视频时长、开启prompt智能改写、添加水印等。 |

## 响应参数 | 成功响应 异常响应 请保存 task_id，用于查询任务状态与结果。
创建任务失败，请参见
|
任务输出信息。 | |
请求唯一标识。可用于请求明细溯源和问题排查。 | |
请求失败的错误码。请求成功时不会返回此参数，详情请参见 | |
请求失败的详细信息。请求成功时不会返回此参数，详情请参见 |

### 步骤2：根据任务ID查询结果

`GET https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}`


`GET https://dashscope-intl.aliyuncs.com/api/v1/tasks/{task_id}`


`GET https://dashscope-us.aliyuncs.com/api/v1/tasks/{task_id}`


## 请求参数 | 查询任务结果 将
|
| |
请求身份认证。接口使用阿里云百炼API-Key进行身份认证。示例值：Bearer sk-xxxx。 | |
| |
任务ID。 |

| 任务执行成功 任务执行失败 任务查询过期 视频URL仅保留24小时，超时后会被自动清除，请及时保存生成的视频。
若任务执行失败，task_status将置为 FAILED，并提供错误码和信息。请参见
task_id查询有效期为 24 小时，超时后将无法查询，返回以下报错信息。
|
任务输出信息。 | |
输出信息统计，只对成功的结果计数。 | |
请求唯一标识。可用于请求明细溯源和问题排查。 |

## DashScope SDK调用

SDK 的参数命名与[HTTP接口](#42703589880ts)基本一致，参数结构根据语言特性进行封装。

由于图生视频任务耗时较长（通常为1-5分钟），SDK 在底层封装了 HTTP 异步调用流程，支持同步、异步两种调用方式。

具体耗时受限于排队任务数和服务执行情况，请在获取结果时耐心等待。

### Python SDK调用

请确保 DashScope Python SDK 版本**不低于 **

，再运行以下代码。**1.25.8**

若版本过低，可能会触发 “url error, please check url!” 等错误。请参考[安装SDK](https://help.aliyun.com/zh/model-studio/install-sdk)进行更新。

根据模型所在地域设置

:**base_http_api_url**

`dashscope.base_http_api_url = 'https://dashscope.aliyuncs.com/api/v1'`


`dashscope.base_http_api_url = 'https://dashscope-intl.aliyuncs.com/api/v1'`


`dashscope.base_http_api_url = 'https://dashscope-us.aliyuncs.com/api/v1'`


**示例代码**

同步调用会阻塞等待，直到视频生成完成并返回结果。本示例展示三种图像输入方式：公网URL、Base64编码、本地文件路径。

##### 请求示例

```
import base64
import os
from http import HTTPStatus
from dashscope import VideoSynthesis
import mimetypes
import dashscope
# 以下为北京地域url，获取url：https://help.aliyun.com/zh/model-studio/image-to-video-api-reference
dashscope.base_http_api_url = 'https://dashscope.aliyuncs.com/api/v1'
# 若没有配置环境变量，请用百炼API Key将下行替换为：api_key="sk-xxx"
# 获取API Key：https://help.aliyun.com/zh/model-studio/get-api-key
api_key = os.getenv("DASHSCOPE_API_KEY")
# --- 辅助函数：用于 Base64 编码 ---
# 格式为 data:{MIME_type};base64,{base64_data}
def encode_file(file_path):
mime_type, _ = mimetypes.guess_type(file_path)
if not mime_type or not mime_type.startswith("image/"):
raise ValueError("不支持或无法识别的图像格式")
with open(file_path, "rb") as image_file:
encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
return f"data:{mime_type};base64,{encoded_string}"
"""
图像输入方式说明：
以下提供了三种图片输入方式，三选一即可
1. 使用公网URL - 适合已有公开可访问的图片
2. 使用本地文件 - 适合本地开发测试
3. 使用Base64编码 - 适合私有图片或需要加密传输的场景
"""
# 【方式一】使用公网可访问的图片URL
# 示例：使用一个公开的图片URL
img_url = "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250925/wpimhv/rap.png"
# 【方式二】使用本地文件（支持绝对路径和相对路径）
# 格式要求：file:// + 文件路径
# 示例（绝对路径）：
# img_url = "file://" + "/path/to/your/img.png" # Linux/macOS
# img_url = "file://" + "/C:/path/to/your/img.png" # Windows
# 示例（相对路径）：
# img_url = "file://" + "./img.png" # 相对当前执行文件的路径
# 【方式三】使用Base64编码的图片
# img_url = encode_file("./img.png")
# 设置音频audio url
audio_url = "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250925/ozwpvi/rap.mp3"
def sample_call_i2v():
# 同步调用，直接返回结果
print('please wait...')
rsp = VideoSynthesis.call(api_key=api_key,
model='wan2.6-i2v-flash',
prompt='一幅都市奇幻艺术的场景。一个充满动感的涂鸦艺术角色。一个由喷漆所画成的少年，正从一面混凝土墙上活过来。他一边用极快的语速演唱一首英文rap，一边摆着一个经典的、充满活力的说唱歌手姿势。场景设定在夜晚一个充满都市感的铁路桥下。灯光来自一盏孤零零的街灯，营造出电影般的氛围，充满高能量和惊人的细节。视频的音频部分完全由他的rap构成，没有其他对话或杂音。',
img_url=img_url,
audio_url=audio_url,
resolution="720P",
duration=10,
prompt_extend=True,
watermark=False,
negative_prompt="",
seed=12345)
print(rsp)
if rsp.status_code == HTTPStatus.OK:
print("video_url:", rsp.output.video_url)
else:
print('Failed, status_code: %s, code: %s, message: %s' %
(rsp.status_code, rsp.code, rsp.message))
if __name__ == '__main__':
sample_call_i2v()
```


##### 响应示例

video_url 有效期24小时，请及时下载视频。

```
{
"status_code": 200,
"request_id": "2794c7a3-fe8c-4dd4-a1b7-xxxxxx",
"code": null,
"message": "",
"output": {
"task_id": "c15d5b14-07c4-4af5-b862-xxxxxx",
"task_status": "SUCCEEDED",
"video_url": "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/xxx.mp4?Expires=xxx",
"submit_time": "2026-01-22 23:24:46.527",
"scheduled_time": "2026-01-22 23:24:46.565",
"end_time": "2026-01-22 23:25:59.978",
"orig_prompt": "一幅都市奇幻艺术的场景。一个充满动感的涂鸦艺术角色。一个由喷漆所画成的少年，正从一面混凝土墙上活过来。他一边用极快的语速演唱一首英文rap，一边摆着一个经典的、充满活力的说唱歌手姿势。场景设定在夜晚一个充满都市感的铁路桥下。灯光来自一盏孤零零的街灯，营造出电影般的氛围，充满高能量和惊人的细节。视频的音频部分完全由他的rap构成，没有其他对话或杂音。"
},
"usage": {
"video_count": 1,
"video_duration": 0,
"video_ratio": "",
"duration": 10,
"input_video_duration": 0,
"output_video_duration": 10,
"audio": true,
"SR": 720
}
}
```


本示例展示异步调用方式。该方式会立即返回任务ID，需要自行轮询或等待任务完成。

##### 请求示例

```
import os
from http import HTTPStatus
from dashscope import VideoSynthesis
import dashscope
# 以下为北京地域url，获取url：https://help.aliyun.com/zh/model-studio/image-to-video-api-reference
dashscope.base_http_api_url = 'https://dashscope.aliyuncs.com/api/v1'
# 若没有配置环境变量，请用百炼API Key将下行替换为：api_key="sk-xxx"
# 获取API Key：https://help.aliyun.com/zh/model-studio/get-api-key
api_key = os.getenv("DASHSCOPE_API_KEY")
# 使用公网可访问的图片URL
img_url = "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250925/wpimhv/rap.png"
# 设置音频audio url
audio_url = "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250925/ozwpvi/rap.mp3"
def sample_async_call_i2v():
# 异步调用，返回一个task_id
rsp = VideoSynthesis.async_call(api_key=api_key,
model='wan2.6-i2v-flash',
prompt='一幅都市奇幻艺术的场景。一个充满动感的涂鸦艺术角色。一个由喷漆所画成的少年，正从一面混凝土墙上活过来。他一边用极快的语速演唱一首英文rap，一边摆着一个经典的、充满活力的说唱歌手姿势。场景设定在夜晚一个充满都市感的铁路桥下。灯光来自一盏孤零零的街灯，营造出电影般的氛围，充满高能量和惊人的细节。视频的音频部分完全由他的rap构成，没有其他对话或杂音。',
img_url=img_url,
audio_url=audio_url,
resolution="720P",
duration=10,
prompt_extend=True,
watermark=False,
negative_prompt="",
seed=12345)
print(rsp)
if rsp.status_code == HTTPStatus.OK:
print("task_id: %s" % rsp.output.task_id)
else:
print('Failed, status_code: %s, code: %s, message: %s' %
(rsp.status_code, rsp.code, rsp.message))
# 获取异步任务信息
status = VideoSynthesis.fetch(task=rsp, api_key=api_key)
if status.status_code == HTTPStatus.OK:
print(status.output.task_status)
else:
print('Failed, status_code: %s, code: %s, message: %s' %
(status.status_code, status.code, status.message))
# 等待异步任务结束
rsp = VideoSynthesis.wait(task=rsp, api_key=api_key)
print(rsp)
if rsp.status_code == HTTPStatus.OK:
print(rsp.output.video_url)
else:
print('Failed, status_code: %s, code: %s, message: %s' %
(rsp.status_code, rsp.code, rsp.message))
if __name__ == '__main__':
sample_async_call_i2v()
```


##### 响应示例

1、创建任务的响应示例

```
{
"status_code": 200,
"request_id": "6dc3bf6c-be18-9268-9c27-xxxxxx",
"code": "",
"message": "",
"output": {
"task_id": "686391d9-7ecf-4290-a8e9-xxxxxx",
"task_status": "PENDING",
"video_url": ""
},
"usage": null
}
```


2、查询任务结果的响应示例

video_url 有效期24小时，请及时下载视频。

```
{
"status_code": 200,
"request_id": "2794c7a3-fe8c-4dd4-a1b7-xxxxxx",
"code": null,
"message": "",
"output": {
"task_id": "c15d5b14-07c4-4af5-b862-xxxxxx",
"task_status": "SUCCEEDED",
"video_url": "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/xxx.mp4?Expires=xxx",
"submit_time": "2026-01-22 23:24:46.527",
"scheduled_time": "2026-01-22 23:24:46.565",
"end_time": "2026-01-22 23:25:59.978",
"orig_prompt": "一幅都市奇幻艺术的场景。一个充满动感的涂鸦艺术角色。一个由喷漆所画成的少年，正从一面混凝土墙上活过来。他一边用极快的语速演唱一首英文rap，一边摆着一个经典的、充满活力的说唱歌手姿势。场景设定在夜晚一个充满都市感的铁路桥下。灯光来自一盏孤零零的街灯，营造出电影般的氛围，充满高能量和惊人的细节。视频的音频部分完全由他的rap构成，没有其他对话或杂音。"
},
"usage": {
"video_count": 1,
"video_duration": 0,
"video_ratio": "",
"duration": 10,
"input_video_duration": 0,
"output_video_duration": 10,
"audio": true,
"SR": 720
}
}
```


### Java SDK调用

请确保 DashScope Java SDK 版本**不低于 **

，再运行以下代码。**2.22.6**

若版本过低，可能会触发 “url error, please check url!” 等错误。请参考[安装SDK](https://help.aliyun.com/zh/model-studio/install-sdk)进行更新。

根据模型所在地域设置

:**baseHttpApiUrl**

`Constants.baseHttpApiUrl = "https://dashscope.aliyuncs.com/api/v1";`


`Constants.baseHttpApiUrl = "https://dashscope-intl.aliyuncs.com/api/v1";`


`Constants.baseHttpApiUrl = "https://dashscope-us.aliyuncs.com/api/v1";`


**示例代码**

同步调用会阻塞等待，直到视频生成完成并返回结果。本示例展示三种图像输入方式：公网URL、Base64编码、本地文件路径。

##### 请求示例

```
// Copyright (c) Alibaba, Inc. and its affiliates.
import com.alibaba.dashscope.aigc.videosynthesis.VideoSynthesis;
import com.alibaba.dashscope.aigc.videosynthesis.VideoSynthesisParam;
import com.alibaba.dashscope.aigc.videosynthesis.VideoSynthesisResult;
import com.alibaba.dashscope.exception.ApiException;
import com.alibaba.dashscope.exception.InputRequiredException;
import com.alibaba.dashscope.exception.NoApiKeyException;
import com.alibaba.dashscope.utils.JsonUtils;
import com.alibaba.dashscope.utils.Constants;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.Base64;
import java.util.HashMap;
import java.util.Map;
public class Image2Video {
static {
// 以下为北京地域url，获取url：https://help.aliyun.com/zh/model-studio/image-to-video-api-reference
Constants.baseHttpApiUrl = "https://dashscope.aliyuncs.com/api/v1";
}
// 若没有配置环境变量，请用百炼API Key将下行替换为：apiKey="sk-xxx"
// 获取API Key：https://help.aliyun.com/zh/model-studio/get-api-key
static String apiKey = System.getenv("DASHSCOPE_API_KEY");
/**
* 图像输入方式说明：三选一即可
*
* 1. 使用公网URL - 适合已有公开可访问的图片
* 2. 使用本地文件 - 适合本地开发测试
* 3. 使用Base64编码 - 适合私有图片或需要加密传输的场景
*/
//【方式一】公网URL
static String imgUrl = "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250925/wpimhv/rap.png";
//【方式二】本地文件路径（file://+绝对路径）
// static String imgUrl = "file://" + "/your/path/to/img.png"; // Linux/macOS
// static String imgUrl = "file://" + "/C:/your/path/to/img.png"; // Windows
//【方式三】Base64编码
// static String imgUrl = Image2Video.encodeFile("/your/path/to/img.png");
// 设置音频audio url
static String audioUrl = "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250925/ozwpvi/rap.mp3";
public static void image2video() throws ApiException, NoApiKeyException, InputRequiredException {
// 设置parameters参数
Map<String, Object> parameters = new HashMap<>();
parameters.put("prompt_extend", true);
parameters.put("watermark", false);
parameters.put("seed", 12345);
VideoSynthesis vs = new VideoSynthesis();
VideoSynthesisParam param =
VideoSynthesisParam.builder()
.apiKey(apiKey)
.model("wan2.6-i2v-flash")
.prompt("一幅都市奇幻艺术的场景。一个充满动感的涂鸦艺术角色。一个由喷漆所画成的少年，正从一面混凝土墙上活过来。他一边用极快的语速演唱一首英文rap，一边摆着一个经典的、充满活力的说唱歌手姿势。场景设定在夜晚一个充满都市感的铁路桥下。灯光来自一盏孤零零的街灯，营造出电影般的氛围，充满高能量和惊人的细节。视频的音频部分完全由他的rap构成，没有其他对话或杂音。")
.imgUrl(imgUrl)
.audioUrl(audioUrl)
.duration(10)
.parameters(parameters)
.resolution("720P")
.negativePrompt("")
.build();
System.out.println("please wait...");
VideoSynthesisResult result = vs.call(param);
System.out.println(JsonUtils.toJson(result));
}
/**
* 将文件编码为Base64字符串
* @param filePath 文件路径
* @return Base64字符串，格式为 data:{MIME_type};base64,{base64_data}
*/
public static String encodeFile(String filePath) {
Path path = Paths.get(filePath);
if (!Files.exists(path)) {
throw new IllegalArgumentException("文件不存在: " + filePath);
}
// 检测MIME类型
String mimeType = null;
try {
mimeType = Files.probeContentType(path);
} catch (IOException e) {
throw new IllegalArgumentException("无法检测文件类型: " + filePath);
}
if (mimeType == null || !mimeType.startsWith("image/")) {
throw new IllegalArgumentException("不支持或无法识别的图像格式");
}
// 读取文件内容并编码
byte[] fileBytes = null;
try{
fileBytes = Files.readAllBytes(path);
} catch (IOException e) {
throw new IllegalArgumentException("无法读取文件内容: " + filePath);
}
String encodedString = Base64.getEncoder().encodeToString(fileBytes);
return "data:" + mimeType + ";base64," + encodedString;
}
public static void main(String[] args) {
try {
image2video();
} catch (ApiException | NoApiKeyException | InputRequiredException e) {
System.out.println(e.getMessage());
}
System.exit(0);
}
}
```


##### 响应示例

video_url 有效期24小时，请及时下载视频。

```
{
"request_id": "87c091bb-7a3c-4904-8501-xxxxxx",
"output": {
"task_id": "413ed6e4-5f3a-4f57-8d58-xxxxxx",
"task_status": "SUCCEEDED",
"video_url": "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/xxx.mp4?Expires=xxx",
"orig_prompt": "一幅都市奇幻艺术的场景。一个充满动感的涂鸦艺术角色。一个由喷漆所画成的少年，正从一面混凝土墙上活过来。他一边用极快的语速演唱一首英文rap，一边摆着一个经典的、充满活力的说唱歌手姿势。场景设定在夜晚一个充满都市感的铁路桥下。灯光来自一盏孤零零的街灯，营造出电影般的氛围，充满高能量和惊人的细节。视频的音频部分完全由他的rap构成，没有其他对话或杂音。",
"submit_time": "2026-01-22 23:25:45.729",
"scheduled_time": "2026-01-22 23:25:45.771",
"end_time": "2026-01-22 23:26:44.942"
},
"usage": {
"video_count": 1,
"duration": 10.0,
"input_video_duration": 0.0,
"output_video_duration": 10.0,
"SR": "720"
},
"status_code": 200,
"code": "",
"message": ""
}
```


本示例展示异步调用方式。该方式会立即返回任务ID，需要自行轮询或等待任务完成。

##### 请求示例

```
// Copyright (c) Alibaba, Inc. and its affiliates.
import com.alibaba.dashscope.aigc.videosynthesis.VideoSynthesis;
import com.alibaba.dashscope.aigc.videosynthesis.VideoSynthesisListResult;
import com.alibaba.dashscope.aigc.videosynthesis.VideoSynthesisParam;
import com.alibaba.dashscope.aigc.videosynthesis.VideoSynthesisResult;
import com.alibaba.dashscope.exception.ApiException;
import com.alibaba.dashscope.exception.InputRequiredException;
import com.alibaba.dashscope.exception.NoApiKeyException;
import com.alibaba.dashscope.task.AsyncTaskListParam;
import com.alibaba.dashscope.utils.JsonUtils;
import com.alibaba.dashscope.utils.Constants;
import java.util.HashMap;
import java.util.Map;
public class Image2Video {
static {
// 以下为北京地域url，获取url：https://help.aliyun.com/zh/model-studio/image-to-video-api-reference
Constants.baseHttpApiUrl = "https://dashscope.aliyuncs.com/api/v1";
}
// 若没有配置环境变量，请用百炼API Key将下行替换为：apiKey="sk-xxx"
// 获取API Key：https://help.aliyun.com/zh/model-studio/get-api-key
static String apiKey = System.getenv("DASHSCOPE_API_KEY");
//设置输入图像url
static String imgUrl = "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250925/wpimhv/rap.png";
// 设置音频audio url
static String audioUrl = "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250925/ozwpvi/rap.mp3";
public static void image2video() throws ApiException, NoApiKeyException, InputRequiredException {
// 设置parameters参数
Map<String, Object> parameters = new HashMap<>();
parameters.put("prompt_extend", true);
parameters.put("watermark", false);
parameters.put("seed", 12345);
VideoSynthesis vs = new VideoSynthesis();
VideoSynthesisParam param =
VideoSynthesisParam.builder()
.apiKey(apiKey)
.model("wan2.6-i2v-flash")
.prompt("一幅都市奇幻艺术的场景。一个充满动感的涂鸦艺术角色。一个由喷漆所画成的少年，正从一面混凝土墙上活过来。他一边用极快的语速演唱一首英文rap，一边摆着一个经典的、充满活力的说唱歌手姿势。场景设定在夜晚一个充满都市感的铁路桥下。灯光来自一盏孤零零的街灯，营造出电影般的氛围，充满高能量和惊人的细节。视频的音频部分完全由他的rap构成，没有其他对话或杂音。")
.imgUrl(imgUrl)
.audioUrl(audioUrl)
.duration(10)
.parameters(parameters)
.resolution("720P")
.negativePrompt("")
.build();
// 异步调用
VideoSynthesisResult task = vs.asyncCall(param);
System.out.println(JsonUtils.toJson(task));
System.out.println("please wait...");
//获取结果
VideoSynthesisResult result = vs.wait(task, apiKey);
System.out.println(JsonUtils.toJson(result));
}
// 获取任务列表
public static void listTask() throws ApiException, NoApiKeyException {
VideoSynthesis is = new VideoSynthesis();
AsyncTaskListParam param = AsyncTaskListParam.builder().build();
param.setApiKey(apiKey);
VideoSynthesisListResult result = is.list(param);
System.out.println(result);
}
// 获取单个任务结果
public static void fetchTask(String taskId) throws ApiException, NoApiKeyException {
VideoSynthesis is = new VideoSynthesis();
// 如果已设置 DASHSCOPE_API_KEY 为环境变量，apiKey 可为空
VideoSynthesisResult result = is.fetch(taskId, apiKey);
System.out.println(result.getOutput());
System.out.println(result.getUsage());
}
public static void main(String[] args) {
try {
image2video();
} catch (ApiException | NoApiKeyException | InputRequiredException e) {
System.out.println(e.getMessage());
}
System.exit(0);
}
}
```


##### 响应示例

1、创建任务的响应示例

```
{
"request_id": "5dbf9dc5-4f4c-9605-85ea-xxxxxxxx",
"output": {
"task_id": "7277e20e-aa01-4709-xxxxxxxx",
"task_status": "PENDING"
}
}
```


2、查询任务结果的响应示例

video_url 有效期24小时，请及时下载视频。

```
{
"request_id": "87c091bb-7a3c-4904-8501-xxxxxx",
"output": {
"task_id": "413ed6e4-5f3a-4f57-8d58-xxxxxx",
"task_status": "SUCCEEDED",
"video_url": "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/xxx.mp4?Expires=xxx",
"orig_prompt": "一幅都市奇幻艺术的场景。一个充满动感的涂鸦艺术角色。一个由喷漆所画成的少年，正从一面混凝土墙上活过来。他一边用极快的语速演唱一首英文rap，一边摆着一个经典的、充满活力的说唱歌手姿势。场景设定在夜晚一个充满都市感的铁路桥下。灯光来自一盏孤零零的街灯，营造出电影般的氛围，充满高能量和惊人的细节。视频的音频部分完全由他的rap构成，没有其他对话或杂音。",
"submit_time": "2026-01-22 23:25:45.729",
"scheduled_time": "2026-01-22 23:25:45.771",
"end_time": "2026-01-22 23:26:44.942"
},
"usage": {
"video_count": 1,
"duration": 10.0,
"input_video_duration": 0.0,
"output_video_duration": 10.0,
"SR": "720"
},
"status_code": 200,
"code": "",
"message": ""
}
```


**使用限制**

**数据时效**：任务task_id和 视频url均只保留 24 小时，过期后将无法查询或下载。**内容审核**：输入的内容（如prompt、图像）、输出视频均会经过内容安全审核，含违规内容将返回 “IPInfringementSuspect”或“DataInspectionFailed”错误，详见参见[错误信息](https://help.aliyun.com/zh/model-studio/error-code)。

**错误码**

如果模型调用失败并返回报错信息，请参见[错误信息](https://help.aliyun.com/zh/model-studio/error-code)进行解决。

**常见问题**

**Q：如何生成特定宽高比（如3:4）的视频？**

**A：** 输出视频的宽高比由**输入首帧图像（img_url）**决定，但**无法保证精确比例**（如严格3:4），会存在一定偏差。

**为什么会有偏差？**模型会以输入图像的比例为基准，结合设置的分辨率档位（resolution）总像素，自动计算出最接近的合法分辨率。由于要求视频的长和宽必须是 16 的倍数，模型会对最终分辨率做微调，因此无法保证输出比例严格等于 3:4，但会非常接近。

例如：输入图像750×1000（宽高比 3:4 = 0.75），并设置 resolution = "720P"（目标总像素约 92 万），实际输出816×1104（宽高比 ≈ 0.739，总像素约90万）。


**实践建议**：**输入控制**：尽量使用与目标比例一致的图片作为首帧输入。**后期处理**：如果您对比例有严格要求，建议在视频生成后，使用编辑工具进行简单的裁剪或黑边填充。


**Q：如何获取视频存储的访问域名白名单？**

A： 模型生成的视频存储于阿里云OSS，API将返回一个临时的公网URL。**若需要对该下载地址进行防火墙白名单配置**，请注意：由于底层存储会根据业务情况进行动态变更，为避免过期信息影响访问，文档不提供固定的OSS域名白名单。如有安全管控需求，请联系客户经理获取最新OSS域名列表。

- 本页导读 （1）
- 适用范围
- HTTP调用
- 步骤1：创建任务获取任务ID
- 步骤2：根据任务ID查询结果
- DashScope SDK调用
- Python SDK调用
- Java SDK调用
- 使用限制
- 错误码
- 常见问题