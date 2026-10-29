# Qwen Image 2.0 I2I 事实卡

核对日期：2026-10-28。既有 `qwen-image-2.0` / `qwen-image-2.0-pro`；[官方图像编辑 API](https://help.aliyun.com/zh/model-studio/qwen-image-edit-api)。本文是 repo-only 实施摘要，未执行外部 raw archive 或 Context Hub 同步；页面发布日期未可靠取得。

HTTP 同步返回图像结果，单条 user content 包含 1–3 张图片和一条文字指令；支持 Base64，单图不超过 10MB。384–3072 是推荐输入尺寸，不是硬性拒绝范围。输出指定宽*高，面积为 512²–2048²；支持负向词（最多 500 字符）、prompt_extend、watermark、seed。

本切片只接一张已冻结本地参考、n=1，复用现有图片解码格式和原同步执行记录，不增加 GIF/TIFF 等格式读取。保留明确 false/seed=0、完整负向词及指定尺寸。没有供应商任务 handle；丢响应不得据此自动重新提交。现有域名仍可使用，本片不更换 scope、域名或鉴权配置。
