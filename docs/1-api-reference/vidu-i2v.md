# 图生视频

> 发布时间: 2025-01-01
> 原文链接: https://platform.vidu.cn/docs/image-to-video

---
# 图生视频

POST https://api.vidu.cn/ent/v2/img2video

## 请求头

| 字段 | 值 | 描述 |
|---|---|---|
| Content-Type | application/json | 数据交换格式 |
| Authorization | Token {your api key} | 将 {your api key} 替换为您的 token |

## 请求体

| 参数名称 | 类型 | 必填 | 参数描述 |
|---|---|---|---|
| model | String | 是 | 模型名称 可选值：viduq3-turbo、viduq3-pro、viduq2-pro-fast、viduq2-pro-fast、viduq2-pro、viduq2-turbo、viduq1 、viduq1-classic 、vidu2.0 - viduq3-turbo：对比viduq3-pro，生成速度更快 - viduq3-pro：高效生成优质音视频内容，让视频内容更生动、更形象、更立体，效果更好 - viduq2-pro-fast：价格触底、效果稳定，生成速度较viduq2-turbo提高2-3倍 - viduq2-pro：新模型，效果好，细节丰富 - viduq2-turbo：新模型，效果好，生成快 - viduq1：画面清晰，平滑转场，运镜稳定 - viduq1-classic：画面清晰，转场、运镜更丰富 - vidu2.0：生成速度快 |
| images | Array[String] | 是 | 首帧图像 模型将以此参数中传入的图片为首帧画面来生成视频。 注1：支持传入图片 Base64 编码或图片URL（确保可访问）； 注2：只支持输入 1 张图； 注3：图片支持 png、jpeg、jpg、webp格式； 注4：图片比例需要小于 1:4 或者 4:1 ； 注5：图片大小不超过 50 MB； 注6：请注意，http请求的post body不超过 20MB，且编码必须包含适当的内容类型字符串，例如：
|
| prompt | String | 可选 | 文本提示词 生成视频的文本描述。 注1：字符长度不能超过 5000 个字符 注2：若使用is_rec推荐提示词参数，模型将不考虑此参数所输入的提示词 |
| audio | Bool | 可选 | 是否使用音视频直出能力，默认为false，枚举值为： - false：不需要音视频直出，输出静音视频 - true：需要音视频直出，输出带台词以及背景音的视频 注1：该参数为true时，voice_id参数才生效 注2：该参数为true时，仅q3模型支持错峰 注3：当model 为q3-pro或q3-turbo 时，该参数默认值为true |
| audio_type | String | 可选 | 音频类型，audio为true时必填，默认为all - all：音效+人声 - speech _only：仅人声- sound_effect_only：仅音效注：该参数目前仅支持q2、q1、2.0系列模型的音频拆分 |
| voice_id | String | 可选 | 音色id，q3系列模型不生效 用来决定视频中的声音音色，为空时系统会自动推荐，可选枚举值参考列表：
同时您可以使用
|
| is_rec | Bool | 可选 | 是否使用推荐提示词 - true：是，由系统自动推荐提示词，并使用提示词内容生成视频，推荐提示词数量=1 - false：否，根据输入的prompt生成视频 注意：启用推荐提示词后，每个任务多消耗10积分 |
| bgm | Bool | 可选 | 是否为生成的视频添加背景音乐。 默认：false，可选值 true 、false - 传 true 时系统将从预设 BGM 库中自动挑选合适的音乐并添加；不传或为 false 则不添加 BGM。 - BGM不限制时长，系统根据视频时长自动适配 - BGM参数在q2模型的duration为 9秒 或 10秒 时不生效；该参数在q3模型中不生效 |
| duration | Int | 可选 | 视频时长 viduq3-pro、viduq3-turbo 默认为 5，可选：1 - 16 viduq2-pro-fast、viduq2-pro、viduq2-turbo 默认为 5，可选：1 - 10 viduq1、viduq1-classic 默认为 5，可选：5 vidu2.0 默认为 4，可选：4、8 |
| seed | Int | 可选 | 随机种子 当默认不传或者传0时，会使用随机数替代 手动设置则使用设置的种子 |
| resolution | String | 可选 | 分辨率参数，默认值依据模型和视频时长而定： - viduq3-pro 、viduq3-turbo 1-16秒：默认 720p，可选：540p、720p、1080p - viduq2-pro-fast、 viduq2-pro、viduq2-turbo 1-10秒：默认 720p，可选：720p、1080p - viduq1 、viduq1-classic 5秒：默认 1080p，可选：1080p - vidu2.0 4秒：默认 360p，可选：360p、720p、1080p - vidu2.0 8秒：默认 720p，可选：720p |
| movement_amplitude | String | 可选 | 运动幅度 默认 auto，可选值：auto、small、medium、large 注：q2、q3系列模型改参数不生效 |
| payload | String | 可选 | 透传参数 不做任何处理，仅数据传输 注：最多 1048576个字符 |
| off_peak | Bool | 可选 | 错峰模式，默认为：false，可选值： - true：错峰生成视频； - false：即时生成视频； 注1：错峰模式消耗的积分更低，具体请查看
注2：错峰模式下提交的任务，会在48小时内生成，未能完成的任务会被自动取消，并返还该任务的积分； 注3：您也可以
注4：除q3 外的其他音视频直出功能，都不支持错峰模式 |
| watermark | Bool | 可选 | 是否添加水印 - true：添加水印； - false：不添加水印； 注1：目前水印内容为固定，内容由AI生成，默认不加 注2：您可以通过watermarked_url参数查询获取带水印的视频内容，详情见
|
| wm_position | Int | 可选 | 水印位置，表示水印出现在图片的位置，可选项为： 1：左上角 2：右上角 3：右下角 4：左下角 默认为：3 |
| wm_url | String | 可选 | 水印内容，此处为图片URL 不传时，使用默认水印：内容由AI生成 |
| meta_data | String | 可选 | 元数据标识，json格式字符串，透传字段，您可以 自定义格式 或使用 示例格式 ，示例如下： { "Label": "your _label","ContentProducer": "yourcontentproducer","ContentPropagator": "your_content_propagator","ProduceID": "yourproductid", "PropagateID": "your_propagate_id","ReservedCode1": "yourreservedcode1", "ReservedCode2": "your_reserved_code2"} 该参数为空时，默认使用vidu生成的元数据标识 |
| callback_url | String | 可选 | Callback 协议 需要您在创建任务时主动设置 callback_url，请求方法为 POST，当视频生成任务有状态变化时，Vidu 将向此地址发送包含任务最新状态的回调请求。回调请求内容结构与查询任务API的返回体一致 回调返回的"status"包括以下状态： - processing 任务处理中 - success 任务完成（如发送失败，回调三次） - failed 任务失败（如发送失败，回调三次） Vidu采用回调签名算法进行认证，详情见：
|

curl -X POST -H "Authorization: Token {your_api_key}" -H "Content-Type: application/json" -d '{"model": "viduq3-pro","images": ["https://prod-ss-images.s3.cn-northwest-1.amazonaws.com.cn/vidu-maas/template/image2video.png"],"prompt": "The astronaut waved and the camera moved up.","audio": true,"voice_id": "professional_host","duration": 5,"seed": 0,"resolution": "1080p","movement_amplitude": "auto","off_peak": false}' https://api.vidu.cn/ent/v2/img2video

## 响应体

| 字段 | 类型 | 描述 |
|---|---|---|
| task_id | String | Vidu 生成的任务ID |
| state | String | 处理状态 可选值： created 创建成功 queueing 任务排队中 processing 任务处理中 success 任务成功 failed 任务失败 |
| model | String | 本次调用的模型名称 |
| prompt | String | 本次调用的提示词参数 |
| images | Array[String] | 本次调用的图像参数 |
| duration | Int | 本次调用的视频时长参数 |
| audio | Bool | 本次调用是否使用音画同出 |
| audio_type | String | 本次调用输出的音频类型 |
| seed | Int | 本次调用的随机种子参数 |
| resolution | String | 本次调用的分辨率参数 |
| movement_amplitude | String | 本次调用的镜头动态幅度参数 |
| payload | String | 本次调用时传入的透传参数 |
| off_peak | Bool | 本次调用时是否使用错峰模式 |
| credits | Int | 本次调用使用的积分数 |
| watermark | Bool | 本次提交任务是否使用水印 |
| created_at | String | 任务创建时间 |

{"task_id": "your_task_id_here","state": "created","model": "viduq3-pro","images": ["https://prod-ss-images.s3.cn-northwest-1.amazonaws.com.cn/vidu-maas/template/image2video.png"],"prompt": "The astronaut waved and the camera moved up.","duration": 5,"seed": random_number,"resolution": "1080p","movement_amplitude": "auto","payload":"","off_peak": false,"credits":credits_number,"created_at": "2025-01-01T15:41:31.968916Z"}

当前页面

目录

- 请求头
- 请求体
- 响应体