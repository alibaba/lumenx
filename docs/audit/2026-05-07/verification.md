# 验证记录

## 代码与测试

### Python
```bash
pytest -q
```
- 结果：`222 passed`
- 说明：核心后端测试当前是绿的。

### Frontend 单测
```bash
npm -C frontend run test -- --runInBand
```
- 结果：`142 passed`

### Frontend 构建
```bash
npm -C frontend run build
```
- 结果：构建成功。
- 额外提示：
  - `rewrites` 在 `output: export` 下不会生效。
  - `typescript.ignoreBuildErrors` 和 `eslint.ignoreDuringBuilds` 会让构建跳过类型与 lint 校验。

### 文案审计
```bash
npm -C frontend run audit:copy:strict
```
- 结果：失败，报告了 460 条疑似裸文案。
- 说明：这不一定全是坏事，因为 prompt 文本和正常 copy 会混在一起，但它至少说明 copy / i18n 边界还没收口。

## 额外观察
- 这套仓库对“能跑”已经很努力了，测试覆盖也不错。
- 真正拖后腿的是入口统一、边界校验、安全暴露面、以及部分功能仍停留在 mock。
