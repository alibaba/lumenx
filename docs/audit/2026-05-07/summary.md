# LumenX Studio 项目审计摘要

日期：2026-05-07

## 结论
这个项目已经不是“空壳 demo”了，核心流程基本能跑，但还没到稳定交付版。最大问题不是单点代码坏掉，而是端口、部署、安全边界、导出/音频真实性、以及状态同步都还没有收口。

## 最关键的风险
- 启动入口和端口没有单一真相，开发、Docker、桌面壳、README 各说各话。
- 后端对外暴露面过大：开放 CORS、无鉴权、`/files` 直接挂整个 `output/`。
- 导出和音频链路存在大量占位实现，UI 看到的能力和后端真实能力不一致。
- 前端本地状态和后端数据会漂移，删除失败还会被静默吞掉。
- 生产构建绕过了类型和 lint 检查，回归门槛偏低。

## 已验证
- Python 测试：`pytest -q` 通过。
- 前端测试：`npm -C frontend run test -- --runInBand` 通过。
- 前端构建：`npm -C frontend run build` 通过，但有 rewrites/export 警告，且跳过类型和 lint 校验。
- 复制/文案审计：`npm -C frontend run audit:copy:strict` 仍会失败，说明 copy 纪律还没收口。

## 详细问题
- [问题清单](./findings.md)
- [验证记录](./verification.md)
