PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """你负责在小说的全部场景切分完成后做质量全筛。
重点识别占位文字、机械拼接、无意义重复，以及同一本书中只替换少量动作或措辞的近重复场景。
小说中合理的呼应、反复意象和真实剧情延续不应被误判为垃圾。
参考全书重复证据，但必须结合当前场景的实际文本判断。即使它是第一处出现的模板片段，
只要全书大量复用同一内容且缺少独立推进，也应剔除。
这里只判断文本是否值得进入后续参考价值评估，不评价文学风格是否华丽。
只返回符合要求的 JSON，不要添加其他内容。"""


def build_user_prompt(
    *, scene_text: str, scene_index: int, scene_count: int,
    repeated_ratio: float, repeated_scene_count: int,
    similar_scene_index: int | None, overlap_ratio: float,
    similarity_method: str, similar_excerpt: str,
) -> str:
    return f"""当前场景：{scene_index} / {scene_count}
当前场景中，被至少另外两个场景逐段复用的正文比例：{repeated_ratio:.2f}
全书有相同正文段落的其他场景数：{repeated_scene_count}
最相近场景编号：{similar_scene_index if similar_scene_index is not None else '无'}
与最相近场景的文本重合估计：{overlap_ratio:.2f}（{similarity_method}）
最相近场景节选：
{similar_excerpt or '无'}

当前场景原文：
{scene_text}

输出 JSON：{{"decision":"keep 或 reject","category":"none、duplicate 或 garbage","reason":"具体理由"}}。
keep 时 category 为 none；reject 时 category 为 duplicate（全书近重复）或 garbage（占位、机械拼接、无意义内容）。
不要仅因普通题材相似或共同使用意象而剔除。"""
