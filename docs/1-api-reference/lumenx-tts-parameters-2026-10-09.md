# 当前 TTS 参数边界核对

核对日期：2026-10-09。范围：既有 CosyVoice `dashscope.audio.tts_v2.SpeechSynthesizer` 和 Qwen3 非实时 `MultiModalConversation`；本次仅修既有适配器字段，不接入新模型、传输或修改 catalog。仓库内留证模式，未同步外部原始资料库或 Context Hub。

## 官方契约

- [CosyVoice Python SDK](https://help.aliyun.com/zh/model-studio/cosyvoice-python-sdk)：指令字段为单数 `instruction`；语速、音调、音量分别使用 `speech_rate`、`pitch_rate`、`volume`。官方数值范围分别为 0.5–2、0.5–2、0–100。
- [Qwen-TTS 非实时 API](https://help.aliyun.com/zh/model-studio/qwen-tts-api)：`instructions` 适用于 Instruct-Flash 系列，不能据此推断所有声音模型均支持。该请求表未列出数字语速、音调、音量。
- [Qwen 实时客户端事件](https://www.alibabacloud.com/help/en/model-studio/qwen-tts-realtime-client-events)：其数字参数表属于实时协议，不能直接移植到本项目使用的非实时接口。

## 本地证据与修复范围

本机 DashScope 1.27.1 的 SDK 构造器显式接收 `instruction`，既有适配器却传 `instructions`。新测试使用真实 SDK 构造器及请求序列化，仅替换 `call` 的网络合成；带指令的 CosyVoice 场景先因未知 keyword 失败，空指令与不支持指令的旧音色三个对照通过。修正只改 kwargs 键名，保持已有音色支持判断、模型选择与数值行为。

测试：`tests/test_tts_sdk_parameters.py`，通过指定 `scripts/run_tests_isolated.py` 执行，输入、API key 和输出字节均为合成数据。它证明本机 SDK 参数和序列化兼容，不证明实际供应商响应或声音质量。最终通过记录跟随执行台账，不能把预期测试写成真实合成验收。

## 下一音频参数片约束

当前适配器对 CosyVoice 数值做截断；Qwen 非实时路径未发送这些数字，且指令可能使默认模型切换为 Instruct 变体。A2 的请求继承、生成快照和过期判定必须区分用户意图与实际调用条件，不能只增加输入 hash 就宣称所有参数生效。自定义音色的模型覆盖与支持指令判断也需按实际解析来源测试。本次不升级 SDK、不静默改用实时接口、不自动重试真实生成。

后续实施 `9763d152` 已将上述适配器参数准备与执行分开，并确保显式模型覆盖不会被指令再次替换；不增加模型或传输能力。对应[固定源码验收](../agents/deliverables/lumenx-refactor/P2M/2026-10-09-image-tts/README.md)。请求选项、对白成功快照与完整过期判定仍是后续集成，不由本页事实核对代替。
