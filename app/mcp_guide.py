"""Usage guide shared by the MCP tool, resource and prompt.

The same text is shipped as ``skills/novel-rag-writing/SKILL.md`` so agents that
support file-based skills can install it directly.
"""

USAGE_GUIDE = """\
# Novel-RAG 写作参考 MCP · 使用说明

## 这个服务给你什么

你只需要说明**想写什么样的文章/场景**。服务会自己把写作意图拆成检索查询，
从书库中找到可参考的场景，并按预算拼成提示词或结构化素材。

它**不写正文**，只提供写作范本与可用的上下文。

## 最重要的一条

不要自己拼标签、猜检索词。直接把“想写什么”交给 `search_writing_references`。

## 推荐调用：search_writing_references

参数：

- `writing_goal`（必填）：一句话到一段话，描述要写的内容、情绪、结构或叙事功能。
- `outline_context`：该场景在细纲中的上下文（前置事件、人物关系、要达成的剧情作用）。
- `constraints`：结构化要求，例如
  `{"情绪":"压抑","句式":"短句","视角":"第三人称限知","避免":"大段心理描写"}`。
- `book_ids`：可选，默认使用全部有 ready 版本的书籍。
- `output`：`prompt`（默认，直接可用）/ `fragments`（自己拼）/ `both`。
- `budget_chars`、`max_scenes`、`book_quota`：上下文预算与书籍配额。
- `use_rerank`：默认 `false`。重排是分钟级操作，只有做离线精排时才打开。

示例：

```json
{
  "writing_goal": "写一段雨夜追杀的紧张场景，冷峻短句，结尾留一个反转",
  "outline_context": "主角刚发现盟友背叛，追兵已经封锁街道",
  "constraints": {"情绪": "紧张", "句式": "短句", "避免": "大段心理描写"}
}
```

## 结果怎么用

- `prompt.text`：直接作为写作模型的上下文或 user 消息的一部分，让它续写。
- `prompt.budget`：预算使用情况；`dropped` 列出因预算被舍弃的候选。
- `references`：每条标明 `book_id` / `version` / `scene_id`，正文里不要提编号；
  需要回查原文时调用 `get_scene`。
- `fragments`：当你有自己的提示词体系时使用。

## 其他工具

- `plan_writing_query`：只把写作目标转成检索查询，不检索；用于调试或预览。
- `build_reference_prompt`：进阶用法，直接给结构化 query，取拼装结果。
- `get_scene`：按 `book_id`、`version`、`scene_id` 读取原文与标注。
- `list_books`：查看可用书籍与当前版本。
- `get_usage_guide`：返回这份说明（MCP 资源 `novel-rag://usage-guide` 同内容）。

## 什么时候会没有结果

- 写作目标太抽象，或与书库内容无关：补充人物关系、冲突、场景功能，写具体。
- 目标书籍没有 ready 版本：先在服务端完成索引。

## 不要做的事

- 不要只丢几个标签（如“紧张、短句”）；给一句完整的写作意图。
- 不要指望它写正文、改剧情或自动续写。
- 不要要求整本书；超长 Scene 会被有界采样。
- 默认不要开启重排；它耗时分钟级，不属于实时链路。
"""

INSTRUCTIONS = (
    "Novel-RAG exposes a writing-reference library. Call "
    "`search_writing_references` with the scene you want to write: the tool "
    "builds the retrieval query, searches the book set and assembles a prompt. "
    "Read `get_usage_guide` first if you are unsure how to phrase the request."
)
