# 设计文档归档

本目录保存 `/Volumes/8TR0/codex/video_translater` 的当前设计文档与历史归档。

当前代码对应的产品范围已经固定为：

`导入视频 -> 转写分段 -> source 纠错确认 -> 翻译合成 -> 时序对齐 -> 目标视频预览`

同时保留一键入口：

`转写合成 = 转写分段 + 自动接受所有 source 修订 + 翻译合成`

## 当前标准文档

以下 3 份是当前主标准，文档内容必须与当前代码保持一致：

1. [frontend-v1-完整复原规范-2026-03-14.md](/Volumes/8TR0/codex/video_translater/docs/设计文档/frontend-v1-完整复原规范-2026-03-14.md)
2. [source-v1-转写分段设计-2026-03-14.md](/Volumes/8TR0/codex/video_translater/docs/设计文档/source-v1-转写分段设计-2026-03-14.md)
3. [target-v1-翻译合成设计-2026-03-14.md](/Volumes/8TR0/codex/video_translater/docs/设计文档/target-v1-翻译合成设计-2026-03-14.md)

这 3 份文档当前覆盖：

- 页面与交互复原
- source 实现与文件产物
- target 实现、重合成、全局对齐和视频输出

## 阶段升级设计

以下文档描述的是 `target` 链路的结构性升级方案。

当前状态：

- 第一步“全局停顿预算分配”已落地到当前代码
- 第二步“语流组”尚未开始
- 第三步“组级 TTS”尚未开始

1. [target-v2-语流组与全局停顿分配设计-2026-03-14.md](/Volumes/8TR0/codex/video_translater/docs/设计文档/target-v2-语流组与全局停顿分配设计-2026-03-14.md)

这份文档覆盖：

- 语流组建模
- 全局停顿预算分配
- 组级 TTS 的升级方向
- 如何从根上减少段间长静音和连贯性断裂

## 当前代码状态说明

当前版本已经落地，而不是“设计先行待实现”：

- `转写分段` 已完成
- source 纠错建议与人工确认已完成
- `翻译合成` 已完成
- `转写合成` 自动接受修订并继续 target 已完成
- target 右侧编辑与 `重新合成` 已完成
- target 视频输出已完成
- target V2 第一步“边界分类 + 全局停顿预算分配”已完成

## 历史归档

以下 `2026-03-13` 文档保留为历史版本，仅用于回看旧页面和旧文案，不再作为当前实现标准：

1. [frontend-v1-界面与交互设计-2026-03-13.md](/Volumes/8TR0/codex/video_translater/docs/设计文档/frontend-v1-界面与交互设计-2026-03-13.md)
2. [frontend-v1-页面结构图与组件清单-2026-03-13.md](/Volumes/8TR0/codex/video_translater/docs/设计文档/frontend-v1-页面结构图与组件清单-2026-03-13.md)
3. [frontend-v1-交互流程与状态流转-2026-03-13.md](/Volumes/8TR0/codex/video_translater/docs/设计文档/frontend-v1-交互流程与状态流转-2026-03-13.md)
4. [frontend-v1-文案规范表-2026-03-13.md](/Volumes/8TR0/codex/video_translater/docs/设计文档/frontend-v1-文案规范表-2026-03-13.md)
