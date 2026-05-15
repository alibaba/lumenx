🔍 无限画布实现方案调研报告
三大核心技术路线
① DOM + CSS Transform（React Flow 路线）⭐ 推荐
原理：用 DOM 元素渲染节点，CSS transform: translate() scale() 做平移缩放
代表：xyflow/xyflow (36K⭐)、react-diagrams (9.4K⭐)
架构：Viewport → CSS transform(matrix) → NodeRenderer + EdgeRenderer
优势：节点可用 HTML 任意渲染（视频、图片、表单），自定义极灵活
② Canvas 2D 纯绘制（ComfyUI 路线）
原理：<canvas> 2D Context 直接绘制一切
代表：litegraph.js (8K⭐)、ComfyUI_frontend
性能优化：ComfyUI 自实现了 QuadTree 空间索引 做 viewport culling
优势：性能极佳（数千节点无压力），但视频嵌入需 DOM overlay
③ Canvas 2D + DOM 混合（tldraw 路线）
原理：Canvas 绘制形状 + DOM 覆盖 UI 控件
代表：tldraw/tldraw (47K⭐)、excalidraw (122K⭐)
核心抽象：Vec(x,y,z) 向量 + Box(x,y,w,h) 边界 + Mat 变换矩阵
优势：SDK 成熟，协作内置，但更适合白板而非节点编辑器
🎯 针对 AI 视频生成的推荐
推荐技术栈
React + TypeScript
├── @xyflow/react        # 无限画布核心
├── zustand              # 状态管理
├── @dnd-kit             # 侧边栏组件拖拽
└── react-zoom-pan-pinch # 额外缩放控制（可选）
⚠️ AI 视频画布的关键挑战
视频预览嵌入 — DOM 方案直接 <video>，Canvas 方案需逐帧绘制
模型调用状态 — pending→running→done 的实时反馈（WebSocket/SSE）
大量缩略图渲染 — viewport culling + 懒加载（参考 ComfyUI 的 QuadTree）
DAG 执行引擎 — 节点间数据传递和依赖图执行（参考 ComfyUI 后端）