# Wan 2.7 I2I 窄事实卡

查阅：2026-10-28；供应商 DashScope，既有 `wan2.7-image` / `wan2.7-image-pro`。来源：[官方 API](https://help.aliyun.com/zh/model-studio/wan-image-generation-and-editing-api-reference)，具体为 HTTP 异步调用的请求字段；页面更新时间未可靠取得，不补写发布日期。只保存实施摘要，不声称完成外部 raw archive 或 Context Hub 同步。

原异步 image-generation 端点支持图像编辑，按 content 数组顺序传入 image，支持 URL 或 Base64。最多 9 张，每张最多 20 MB；宽高 240–8000、比例 1:8–8:1；JPEG/PNG/BMP/WEBP，PNG 不支持透明通道。Pro 的 I2I 最高 2K，与其 T2I 4K 区别；明确像素尺寸的面积范围为 768²–2048²。n 固定为1，seed/watermark保持原已支持语义。

本片选已有 Base64 能力承载接纳时冻结字节，沿原 classic scope/单次 POST/task ID/query/fetch。既有 `provider-wan27-t2i-bound-http-v1` 继续只有 T2I，新 I2I 版本不改写历史或扩大 catalog、认证、模型默认和 UI。现有 `probe_frozen_image` 已返回尺寸/透明信息，无需新媒体解析依赖。
