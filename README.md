# Video Translater

`/Volumes/8TR0/codex/video_translater` 是当前在用的视频转写、校译、翻译合成工作台。

当前代码已经落地的主链路是：

1. `导入视频`
2. `转写分段`
3. `Codex` 生成 source 纠错建议
4. 人工确认 source 文本
5. `翻译合成`
6. 全局时序对齐
7. 生成 target 音轨与 `dubbed.v1.mp4`

其中 `target` 侧当前已经进入 V2 的第一阶段：

- 已落地：边界分类、全局停顿预算分配、统一语速放宽
- 尚未开始：`utterance group` 语流组建模与组级 TTS

同时保留一键入口：

- `转写分段`：只跑 source 链路，纠错建议生成后停在人工确认
- `翻译合成`：要求 source 纠错已处理完成
- `转写合成`：从头重跑 source 和 target；source review 若还有待确认项，会自动全部接受后继续进入 `翻译合成`

## 当前实现状态

当前代码不是 demo 壳子，已经接入真实的本地/CLI 运行链路：

- 后端：`FastAPI`
- 前端：`frontend/index.html` + `frontend/app.js` + `frontend/styles.css`
- ASR：`mlx-community/Qwen3-ASR-*`
- 文本校译 / target 翻译：`codex gpt-5.4`
- TTS：`mlx-community/Qwen3-TTS-*`
- 媒体处理：`ffmpeg`

当前工作台同时支持：

- 局域网访问
- 项目列表和项目右键菜单
- source / target 双字幕工作台
- 逐段播放
- source / target 文本编辑与保存
- target 文本保存后 `重新合成`
- 截取片段并新建项目
- 导出项目 zip

## 模型配置

### 语音转写

- `mlx-community/Qwen3-ASR-0.6B-8bit`
- `mlx-community/Qwen3-ASR-1.7B-8bit`

默认值：

- `mlx-community/Qwen3-ASR-1.7B-8bit`

### 语音合成

- `mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit`
- `mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit`

默认值：

- `mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit`

### 文本校译

- `Codex / gpt-5.4 / none`
- `Codex / gpt-5.4 / low`
- `Codex / gpt-5.4 / medium`
- `Codex / gpt-5.4 / high`
- `Codex / gpt-5.4 / xhigh`

默认值：

- `Codex / gpt-5.4 / medium`

兼容说明：

- 旧配置里的 `codex-minimal` 会在运行时自动迁移为 `codex-none`

## 当前运行规则

### Source 链路

`转写分段` 会执行：

1. 从 `source/original.mp4` 提取 `source/original.wav`
2. 按语音间隙优先切 ASR 块
3. 逐块转写并回收时间戳
4. 生成 `segments/source.v1.json`
5. 生成 `subtitles/source.v1.srt`
6. 切出 `voices/source-segments/*.wav`
7. 切出 `voices/source-reference-segments/*.wav`
8. 运行 source review，产出 `jobs/source_correction_review.json`

补充说明：

- `source-reference-segments` 不是原始硬切音频，而是经过外部静音修剪和短淡入淡出处理后的参考音频
- source 条目内可直接点击 `试听` 播放这份参考音频

这条链路不会自动改正文。

### Target 链路

`翻译合成` 会执行：

1. 读取当前 source 最终文本
2. 生成 `segments/source.snapshot.for-target.v1.json`
3. 逐段翻译
4. 逐段合成 `voices/target-draft/*.wav`
5. 写 `segments/target.draft.v1.json`
6. 做全局时序对齐，生成 `voices/target-aligned/*.wav`
7. 生成 `voices/target-track.v1.wav`
8. 写 `segments/target.aligned.v1.json`
9. 生成 `subtitles/target.draft.v1.srt`
10. 生成 `subtitles/target.v1.srt`
11. 生成 `target/dubbed.v1.mp4`

补充说明：

- target TTS 当前读取的是 `source snapshot text + reference_audio_path`
- `reference_audio_path` 默认指向 `voices/source-reference-segments/<segment_id>.wav`
- 时序对齐当前已经不是“逐段硬塞回原段时长”，而是“统一时长放宽 + 边界分类 + 全局停顿预算分配”

### 一键全流程

`转写合成` 会先清空当前产物，再执行：

1. `run_source_pipeline(project_id)`
2. 若 source review 还有待确认项，则自动接受全部修订
3. `run_target_pipeline(project_id)`

这是一条“从头重跑”的流水线。

## 项目结构

每个项目目录位于：

- `projects/<project_id>/`

当前关键文件如下：

- `project.json`
- `source/original.mp4`
- `source/original.wav`
- `segments/source.v1.json`
- `segments/source.snapshot.for-target.v1.json`
- `segments/target.draft.v1.json`
- `segments/target.aligned.v1.json`
- `subtitles/source.v1.srt`
- `subtitles/target.draft.v1.srt`
- `subtitles/target.v1.srt`
- `voices/source-segments/*.wav`
- `voices/source-reference-segments/*.wav`
- `voices/target-draft/*.wav`
- `voices/target-aligned/*.wav`
- `voices/target-track.v1.wav`
- `target/dubbed.v1.mp4`
- `jobs/state.json`
- `jobs/runtime.json`
- `jobs/source_correction_review.json`
- `logs/events.jsonl`

## 运行

### 本机

```bash
/opt/anaconda3/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8010
```

### 局域网

```bash
/opt/anaconda3/bin/python -m uvicorn server:app --host 0.0.0.0 --port 8010
```

或使用：

```bash
./scripts/run_lan.sh
```

## 文档

当前设计文档入口在：

- [docs/设计文档/README.md](/Volumes/8TR0/codex/video_translater/docs/设计文档/README.md)

当前最重要的 3 份标准文档是：

- [frontend-v1-完整复原规范-2026-03-14.md](/Volumes/8TR0/codex/video_translater/docs/设计文档/frontend-v1-完整复原规范-2026-03-14.md)
- [source-v1-转写分段设计-2026-03-14.md](/Volumes/8TR0/codex/video_translater/docs/设计文档/source-v1-转写分段设计-2026-03-14.md)
- [target-v1-翻译合成设计-2026-03-14.md](/Volumes/8TR0/codex/video_translater/docs/设计文档/target-v1-翻译合成设计-2026-03-14.md)

阶段升级设计文档在：

- [target-v2-语流组与全局停顿分配设计-2026-03-14.md](/Volumes/8TR0/codex/video_translater/docs/设计文档/target-v2-语流组与全局停顿分配设计-2026-03-14.md)

## 当前已知边界

以下问题当前仍属于已知边界，而不是已彻底解决：

1. 逐段独立翻译仍会带来少量跨段语义边界不自然
2. 逐段独立 TTS 仍可能把参考音频边界特征重复带入段首
3. 当前已通过参考音频淡入淡出显著缓解段间杂音，但这还不是最终的原理级方案
4. 当前只完成了 V2 的第一阶段；语流组与组级 TTS 仍待实现

也就是说，当前代码已经可用，但 target 音质层面仍有继续优化空间。
