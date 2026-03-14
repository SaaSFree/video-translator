# Transcript Postprocess Prompt

This prompt is designed as a shared post-processing spec for ASR transcripts across languages.
Use the shared core rules for all languages, and add a short language-specific note only when needed.

## Recommended Usage

Replace `<TARGET_LANGUAGE>` with the language of the transcript you are cleaning.
Append the raw transcript after the prompt.

## Shared Prompt

```text
请整理我提供的 <TARGET_LANGUAGE> 转写文本，输出一份适合阅读和校对的修正版正文。

要求：

1. 只修正明显的语音识别错误。
包括：同音误识别、专有名词误识别、英文术语误识别、标点误判、断句错误。
不要改作者观点、事实判断、论证逻辑和表达立场。
如果内容可能事实有误，但不像 ASR 错误，不要改。

2. 先去掉原文所有换行，拼接成连续正文，再重新加标点和分句。
不要沿用原始换行。

3. 按目标语言的常用书面表达习惯修正标点。
让句子自然、通顺、可读。

4. 分句原则是“一个完整语义单元一行”。
长句可以保留，不要机械拆短。
如果上下句其实属于同一完整表达，就必须合并，不要硬拆。

5. 以下情况要特别注意合并：
- 举例被拆开时要合并。
- 反问、设问、自问自答被拆开时要合并。
- 递进追问被拆开时要合并。
- 铺垫句和紧接的承接句被拆开时要合并。
- 语气上明显连在一起的短句要合并。

例如下面这些都应合并为一行：
“你可能会问，这怎么说呢？没这么严重吧？”
“但即便如此，开发者们依然觉得这是划算的。为什么呢？”
“他坐在电脑前，搓了搓手，准备开始感受前沿科技的魅力了。然后呢？”
“因为大厂本来是以相对固定的包月订阅价格给那些人类用户准备的，人类打字能有多快？一天能问多少个问题？”
“比如你是一个乐于助人的助手，你的名字叫什么？你不能回答什么问题？等等一大长串隐藏指令。”

6. 专有名词如果能高置信判断为 ASR 错误，就修正；不能高置信判断时宁可保留，不要乱改。

7. 最终只输出整理后的正文，每个完整语义单元一行。
不要加解释、标题、序号或说明。
```

## Language-Specific Notes

Use this section only if a language needs extra punctuation or segmentation guidance.

- Chinese:
  - Prefer natural Chinese punctuation and phrasing.
  - Long sentences are acceptable if the semantic unit is complete.
  - Do not over-split rhetorical pairs, example chains, or continuation phrases.
- Future languages:
  - Add a short note here instead of duplicating the whole prompt.

## Why This Layout

- Shared core:
  - Keeps one stable rule set for all languages.
- Small language notes:
  - Easier to maintain when later adding English, Japanese, or other transcript styles.
- Root-level docs placement:
  - Easy to find and reuse from scripts, prompts, or manual workflows.
