# 媒体响应：显示地址与原始引用

接口核对：2026-10-10。适用于 Core foundation 分支。实施与运行证据只见[执行台账](../agents/context/lumenx-first-principles/EXECUTION-STATUS.md)；本契约只定义响应边界，与[对白/配音契约](lumenx-dialogue-audio-contract.md)配合。

## 响应规则

`api.signed_response` 只转换已声明的媒体展示字段及媒体列表。通用 `url` 只有媒体变体对象或调用方显式声明的根字段才可转换；上传和导出使用显式声明。对白、提示词、指令、ID 不能因为看起来像对象路径就被签名。

转换展示字段时，同一对象返回可选 `media_refs`，保存该字段转换前的原始引用。例如 `video_url` 可用于播放，`media_refs.video_url` 用于和原始来源记录比较。媒体列表的引用按位置对应；未转换的项为 null。没有 OSS 配置时维持本地展示引用，不需要人工 sidecar。

`media_refs` 是响应附带字段，不写入项目/系列/资产库，不计入内容 hash、revision 或生成参数。响应媒体对象上已有的同名附带字段不可信，序列化时丢弃，再按当前原始字段重建。序列化的输入必须是正式原始数据；不能将已签名的响应再次当作原始数据来猜测其来源。

`dub_preview_source`、`dub_applied_source`、`dialogue_audio_snapshot` 和 `options/prepared/resolved_request/settings/sources` 是原始快照/参数，不递归签名。它们是不可按响应字段解释的不透明内容，包括未知版本和内部恰好同名的 `media_refs`，均逐字段保留。

## 客户端消费

展示继续使用可播放 URL；来源比对使用同对象的原始引用。没有附带引用时只比较原字段的完整值，不剥离查询参数、不猜测签名规则。存在但非法的附带引用不能当作有效来源证明，也不能通过回退 URL 获得更宽松的判断。

`media_refs` 不是内容 hash、固定媒体版本、文件存在性证明或访问授权。签名更新不应改变原引用关系；相同路径下字节变化的识别、签名过期刷新、远程媒体固定版本仍需对应 P3/P7 验收。

后端回归入口：`tests/test_media_response_contract.py`。真实 GET、配音 raw ACK、audio/state、上传/导出及 fake signer 覆盖响应形状；不代表真实 OSS 鉴权通过。前端来源与实际组件消费由 `dub-source.test.ts`、配音/候选/Assembly 相关组件回归验证，固定运行结果由台账链接。
