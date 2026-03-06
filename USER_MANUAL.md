# Lumen-X 用户手册

> 作者：星莲(StarLotus，张钧贺)

## 📋 目录

1. [快速开始](#-快速开始)
2. [API 密钥配置](#-api-密钥配置)
3. [OSS 存储配置（可选）](#-oss-存储配置可选)
4. [日志查看](#-日志查看)
5. [常见问题](#-常见问题)

---

## 🚀 快速开始

### 首次启动（仅限应用打包方式）

1. **双击应用图标**启动 Lumen-X
2. 应用会自动打开**设置页面**
3. 按照提示完成 **API 密钥配置**

### 应用数据目录

所有用户数据存储在以下位置：

| 系统 | 路径 |
|------|------|
| macOS / Linux | `~/.lumen-x/` |
| Windows | `C:\Users\<用户名>\.lumen-x\` |

---

## 🔑 API 密钥配置

LumenX 使用阿里云灵积平台(DashScope)提供 AI 能力。

### 获取 API Key

1. 访问 [阿里云灵积平台](https://dashscope.aliyun.com/)
2. 登录您的阿里云账号（没有账号请先注册）
3. 进入 **控制台** → **API-KEY 管理**
4. 点击 **创建新的 API-KEY**
5. 复制生成的 API Key

### 在应用中配置

1. 启动 LumenX
2. 点击左上角 **设置图标** ⚙️
3. 找到 **DASHSCOPE_API_KEY** 输入框
4. 粘贴您的 API Key
5. 点击 **保存**

> ⚠️ **重要**：请妥善保管您的 API Key，不要泄露给他人。

---

## ☁️ OSS 存储配置（可选）

OSS 配置用于云端存储生成的资产，适合团队协作或跨设备使用。

### 获取 OSS 配置信息

1. 访问 [阿里云 OSS 控制台](https://oss.console.aliyun.com/)
2. 创建或选择一个 **Bucket**
3. 记录以下信息：
   - **Bucket 名称**
   - **Endpoint**（如 `oss-cn-beijing.aliyuncs.com`）
4. 在 **RAM 访问控制** 中创建 AccessKey

### 在应用中配置

在设置页面填写以下字段：

| 字段 | 说明 | 示例 |
|------|------|------|
| ALIBABA_CLOUD_ACCESS_KEY_ID | AccessKey ID | `LTAI5t...` |
| ALIBABA_CLOUD_ACCESS_KEY_SECRET | AccessKey Secret | `xxxxxx...` |
| OSS_BUCKET_NAME | Bucket 名称 | `my-lumenx-bucket` |
| OSS_ENDPOINT | OSS 地域节点 | `oss-cn-beijing.aliyuncs.com` |
| OSS_BASE_PATH | 存储路径前缀 | `lumenx` |

---

## 📋 日志查看

当遇到问题时，日志文件可帮助排查原因。

### 日志文件位置

| 系统 | 路径 |
|------|------|
| macOS / Linux | `~/.lumen-x/logs/app.log` |
| Windows | `C:\Users\<用户名>\.lumen-x\logs\app.log` |

### 打开日志目录

**macOS**：
1. 打开 Finder
2. 按 `Cmd + Shift + G`
3. 输入 `~/.lumen-x/logs` 并回车

**Windows**：
1. 打开资源管理器
2. 在地址栏输入 `%USERPROFILE%\.lumen-x\logs`
3. 按回车

### 如何提交问题报告

如需技术支持，请提供：
1. **app.log** 文件（或其中的错误部分）
2. 操作步骤描述

---

## ❓ 常见问题

### Q: 为什么需要配置 API Key？

A: LumenX 使用阿里云灵积平台的 AI 模型进行图像和视频生成。API Key 用于验证您的身份并计费。

### Q: OSS 配置失败怎么办？

请检查：
1. AccessKey ID 和 Secret 是否正确
2. Bucket 名称是否存在
3. Endpoint 格式是否正确（不需要 `https://` 前缀）
4. RAM 权限是否包含 OSS 读写权限

### Q: 生成失败如何排查？

1. 查看日志文件中的错误信息
2. 检查 API Key 是否过期或余额不足
3. 确认网络连接正常

### Q: 如何清理缓存？

删除 `~/.lumen-x/` 目录下的 `webview_storage` 文件夹，然后重启应用。

---

## 📞 获取帮助

如有问题，请联系本项目开发者 星莲（StarLotus，张钧贺） 或查看项目 README 文档。
