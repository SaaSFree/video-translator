# Official Notes

更新日期：`2026-03-14`

本文不是泛泛的技术选型笔记，而是当前 `/Volumes/8TR0/codex/video_translater` 代码里仍然生效的“官方依据摘要”。

只保留目前真实影响实现的几条：

1. OpenAI `gpt-5.4` 的推理档位
2. Qwen3-TTS `Base` 模型的 voice clone 输入约束
3. `mlx-audio` 对 Qwen3-ASR / ForcedAligner / Qwen3-TTS 的本地运行支持
4. FastAPI 与 FFmpeg 在当前工程中的职责边界

---

## 1. OpenAI / GPT-5.4

### 1.1 当前代码对应的官方事实

OpenAI 当前官方模型目录显示：

- `gpt-5.4` 是复杂推理和编码的旗舰模型
- `gpt-5.4` 支持的 reasoning effort 是：
  - `none`
  - `low`
  - `medium`
  - `high`
  - `xhigh`

对应官方来源：

- [Models | OpenAI API](https://developers.openai.com/api/docs/models)

依据页面中的 `gpt-5.4` 条目：

- `Model ID gpt-5.4`
- `Reasoning: none low medium high xhigh`

### 1.2 当前代码中的映射

这直接对应当前代码里的：

- [config.py](/Volumes/8TR0/codex/video_translater/backend/app/config.py)
- [providers.py](/Volumes/8TR0/codex/video_translater/backend/app/providers.py)

当前前端/后端共享的 `review_backend` 选项是：

- `codex-none`
- `codex-low`
- `codex-medium`
- `codex-high`
- `codex-xhigh`

### 1.3 兼容策略

旧配置里曾出现过 `codex-minimal`。

当前代码的原则是：

- 不再把 `minimal` 当成当前有效配置展示
- 但仍兼容旧数据
- 读到旧值时迁移为 `codex-none`

这属于“兼容历史配置”，不是当前产品能力本身。

---

## 2. Qwen3-TTS

### 2.1 当前代码对应的官方事实

Qwen 官方 README 明确说明：

- `Qwen3-TTS-12Hz-1.7B-Base`
- `Qwen3-TTS-12Hz-0.6B-Base`

都属于 `Base` voice clone 模型。

官方说明还明确写到：

- voice clone 时需要提供 `ref_audio`
- 同时提供该参考音频的转写 `ref_text`
- 如果使用 `x_vector_only_mode=True`，则 `ref_text` 可以省略
- 但克隆质量可能下降

官方来源：

- [QwenLM/Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS)

关键点见 README 的 `Voice Clone` 段落。

### 2.2 对当前工程的直接约束

这条官方约束直接决定了当前代码不能再这样做：

- 参考音频只截半段
- 参考文本却仍传整句全文

因为 `ref_audio` 和 `ref_text` 在模型语义上是一对，`ref_text` 必须对应参考音频里实际说出的内容。

这条原则当前已经映射到：

- [pipeline.py](/Volumes/8TR0/codex/video_translater/backend/app/pipeline.py)
- [providers.py](/Volumes/8TR0/codex/video_translater/backend/app/providers.py)

当前 target TTS 的参考输入是：

- `ref_audio = voices/source-segments/<segment_id>.wav`
- `ref_text = source snapshot 中对应段的 text`

### 2.3 关于 reusable prompt

Qwen 官方 README 还提供了 `create_voice_clone_prompt` 的复用方式：

- 先用 `ref_audio + ref_text` 构建 clone prompt
- 后续多次生成时复用 `voice_clone_prompt`

这对当前项目有一个重要启发：

- 如果以后要继续优化性能和稳定性
- 可以考虑把每段或每组参考音频先做成可复用 prompt
- 而不是每次合成都重新抽特征

当前代码还没有走到这一步，但这是后续优化的官方允许方向。

---

## 3. MLX Audio

### 3.1 当前代码对应的官方事实

`mlx-audio` 官方 README 明确列出了当前工程正在用到的模型族：

- `Qwen3-ASR`
- `Qwen3-ForcedAligner`
- `Qwen3-TTS`

官方来源：

- [Blaizzy/mlx-audio](https://github.com/Blaizzy/mlx-audio)

README 中明确展示了：

- `mlx-community/Qwen3-ASR-0.6B-8bit`
- `mlx-community/Qwen3-ForcedAligner-0.6B-8bit`
- `mlx-community/Qwen3-TTS-*`

### 3.2 对当前工程的直接影响

这直接支撑了当前后端的本地运行结构：

- ASR 服务脚本：`scripts/mlx_audio_asr_service.py`
- TTS 服务脚本：`scripts/mlx_audio_tts_service.py`

也决定了当前 provider 层的结构是合理的：

- 端口健康检查
- 按模型名拉起本地服务
- 当端口上挂着错误模型时自动切换

对应当前代码：

- [providers.py](/Volumes/8TR0/codex/video_translater/backend/app/providers.py)

### 3.3 当前实现边界

`mlx-audio` 官方支持这些模型本地运行，并不等于：

- 当前项目已经把参考音频质量控制做到了最优
- 当前项目已经解决所有段间杂音问题

也就是说：

- `mlx-audio` 解决的是“本地可运行”
- 不自动解决“参考音频边界噪声”和“逐段独立 TTS 的自然度”

---

## 4. FastAPI

### 4.1 当前代码中的角色

FastAPI 在当前项目里承担两类职责：

1. 提供 API
2. 直接托管前端静态页面

对应当前代码：

- [main.py](/Volumes/8TR0/codex/video_translater/backend/app/main.py)

其中：

- `/static` 直接挂静态资源
- 运行任务通过 worker 子进程执行
- 长任务不在请求线程里直接跑完

### 4.2 当前工程层面的原则

当前代码沿用的原则是：

- API 只负责启动、查询、更新、停止任务
- 长任务交给 worker / pipeline
- 前端通过轮询获取项目详情和任务状态

这条边界当前仍然成立，没有变化。

---

## 5. FFmpeg

### 5.1 当前代码中的角色

FFmpeg 在当前工程里负责：

1. 提取原音频
2. 切 source 段音频
3. 切参考音频
4. 拼接 target 音轨
5. 最终把原视频画面与 target 音轨合成 `dubbed.v1.mp4`

对应代码集中在：

- [media.py](/Volumes/8TR0/codex/video_translater/backend/app/media.py)

### 5.2 当前工程层面的原则

FFmpeg 负责的是“媒体变换”，不是“语言智能”。

所以：

- 文本纠错不依赖 FFmpeg
- 翻译不依赖 FFmpeg
- 但音频提取、切段、拼接、mux 都依赖 FFmpeg

---

## 6. 当前代码与官方依据的对应关系

为了便于后续维护，当前可以直接记住这 4 条：

1. `gpt-5.4` 当前支持 `none / low / medium / high / xhigh`
   这决定了前端和后端的 `review_backend` 选项集合。

2. `Qwen3-TTS Base` voice clone 的正确输入是 `ref_audio + ref_text`
   这决定了 target TTS 的参考输入构造方式。

3. `mlx-audio` 官方支持当前使用的 Qwen3 ASR / ForcedAligner / TTS 模型
   这决定了当前 provider + service 脚本结构是正确的。

4. FastAPI + FFmpeg 仍然是当前工程的边界基础
   这决定了“API + worker + media pipeline”这套架构不需要重写。

---

## 7. 参考链接

当前仍然有效、且已经在代码层面发生作用的官方来源：

- [Models | OpenAI API](https://developers.openai.com/api/docs/models)
- [Using GPT-5.4 / latest-model guide](https://developers.openai.com/api/docs/guides/latest-model)
- [QwenLM/Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS)
- [Blaizzy/mlx-audio](https://github.com/Blaizzy/mlx-audio)

如果后续再调整：

- `review_backend`
- target TTS 参考策略
- 本地 ASR/TTS 服务切换策略

应优先重新核对以上来源，再改代码。
