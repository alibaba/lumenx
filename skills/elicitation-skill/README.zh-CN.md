# Elicitation Skill — 面向 AI Agent 的意图编译器

[English](README.md) | 简体中文

**Elicitation Skill** 是一个 AgentSkill，用来帮助 AI 助手把人类尚未成形的模糊意图，转译成可以执行、可以路由、可以改变系统的定义。

它不是会议纪要机器人，不是咨询问卷，也不是通用头脑风暴 Prompt。它教会 Agent 在急着执行之前，先问几个锋利的问题，挖出人类判断背后的隐性张力，再把回答编译成一份 **Intent Brief（意图简报）**。

这份 Intent Brief 可以继续变成：产品定义、工作流调整、Skill 改造、Memory 规则、Agent 行为变化，或具体任务。

## 为什么需要它

大多数 AI Agent 在任务已经清楚时，执行力都很强。真正困难的地方往往发生在执行之前：

- 人类有一个很强但还模糊的直觉；
- 产品方向还没有被说清楚；
- 团队感觉到某种张力，但还无法命名；
- Agent 太急着行动，于是产出一个看似合理、实则均值化的方案。

Elicitation Skill 为这个“执行前”的阶段提供了一个轻量协议。

> 先问。再综合。最后反哺系统。

## 它能做什么

当你给 Agent 一个模糊输入，比如：

- “帮我把这个产品想法问清楚。”
- “我们做一次 elicitation。”
- “我们是不是缺一个 agent / workflow？”
- “这个方向我感觉是对的，但还说不清为什么。”
- “把这个模糊判断变成可执行定义。”

Agent 会：

1. 判断本次 elicitation 的类型；
2. 先问 3–5 个简短但高杠杆的问题；
3. 等待人类回答；
4. 生成一份 Intent Brief；
5. 给出 1–3 个具体的系统反哺动作。

## 适合什么时候用

当任务还没完全定义清楚，但方向判断很重要时使用：

- 产品策略
- Agent / Skill / Workflow 设计
- 创意方向
- 品牌与品味校准
- 周回顾 / 下周重点
- 复盘与事后分析
- 战略张力澄清

不要把它用于普通问答，或已经非常明确的简单任务。

## 仓库结构

```text
.
├── README.md
├── README.zh-CN.md
├── skill/
│   └── elicitation-agent/
│       ├── SKILL.md
│       └── references/
│           └── question-patterns.md
├── templates/
│   └── intent-brief.md
├── examples/
│   └── website-product-intent-brief.md
├── docs/
│   └── product-note.md
└── AGENTS.md
```

## 安装方式

把 skill 文件夹复制到你的 OpenClaw / Codex / Agent Skills 目录：

```bash
cp -R skill/elicitation-agent ~/.openclaw/workspace/skills/
```

然后根据你的运行环境，重启或重新加载 Agent。

## 快速开始

你可以这样对 Agent 说：

```text
我们为这个模糊产品想法做一次 elicitation：
我觉得我们的网站不应该像普通公司官网，而应该像一个可以进入的世界。
```

Agent 不应该立刻开始写 sitemap。它应该先问几个问题，把真正的判断问出来。

## 核心产物：Intent Brief

最终产物需要清楚区分：

- 显性目标
- 隐性张力
- Agent 判断
- 可执行转译
- 反哺对象
- 验证方式
- 不做什么

模板见：[`templates/intent-brief.md`](templates/intent-brief.md)。

## 设计原则

- 少而锋利的问题，胜过冗长问卷。
- 不要太早总结。
- 不要把人类变成项目经理。
- 不要把人类洞察归功给 Agent。
- 每次 elicitation 都必须导向至少一个具体变化。
- 如果没有任何系统、产品、工作流或行为改变，这次 elicitation 就还没完成。

## 一个简单例子

模糊输入：

> “我觉得这个网站应该有一种世界观，而不是普通公司官网。”

Agent 应该先问：

1. 它最不该被误解成什么？
2. 如果它是一个世界，用户进入后第一眼应该记住什么？
3. 这个判断最终应该改变页面结构、视觉系统、内容组织，还是转化路径？

人类回答后，Agent 再把它编译成 Intent Brief，并转化为 PRD、设计原则或执行清单。

## 许可证

MIT. 见 [`LICENSE`](LICENSE)。
