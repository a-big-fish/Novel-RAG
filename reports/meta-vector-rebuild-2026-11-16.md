# Meta 向量重建 · 2026-11-16

## 背景

2026-11-13 的风格与技法路由诊断显示：`meta-dense` 命中率 9/28，书内两两余弦 0.81–0.84，
查询标签与标注标签的平均 Jaccard 重合度只有 0.29；仅标签查询在 miss 探针上全部进不了 Top-5。
查询侧已改为 `raw_intent` 锚点 + 标签，但向量空间本身的同质化仍在。

## 方法

1. 新增版本化提示词 `app/prompts/style_signature/v1.py`：对单个 Scene 生成
   - `style_signature`：60–200 字，必须引用具体物件、句式、对话节奏或信息释放方式；
   - `distinctive_devices`：3–5 条本场景独有写法；
   - `retrieval_hooks`：3–6 个可检索的区分性短语。
2. 结果写入 `scenes.meta_json.style_v1`，不改动原有标注字段，也不需要新迁移。
3. `build_meta_text` 优先使用风格签名，回退到旧版风格摘要/标签拼接。
4. `scripts/rebuild_meta_vectors.py` 只重建 `meta-dense` 向量：保留 `text-dense`、
   `summary-dense`、`text-sparse` 与 payload，按 point id 覆盖写回；脚本幂等，重复运行跳过已有签名。
5. 全量执行：4 本书、260 个入选且已标注 Scene，0 失败；耗时约 11 分钟（LLM 并发 10）。

复现命令：

```bash
uv run python -B -m scripts.rebuild_meta_vectors --book-ids 10 43 44 86 --workers 10
# 小样本验证
uv run python -B -m scripts.rebuild_meta_vectors --limit 3 --dry-run
# 已有签名时只重嵌入
uv run python -B -m scripts.rebuild_meta_vectors --skip-sign
```

## 重建前后

| 书籍 | Scene | meta 重建前 | meta 重建后 | text-dense |
| --- | ---: | ---: | ---: | ---: |
| 马之途 | 8 | 0.789 | 0.734 | 0.734 |
| 备用联系人 | 54 | 0.799 | 0.726 | 0.773 |
| 切勿操之过急 | 89 | 0.835 | 0.706 | 0.740 |
| 百年孤独 | 109 | 0.814 | 0.684 | 0.701 |

（数值为书内两两余弦均值；完整 JSON：`data/converted/meta-rebuild-2026-11-16.json`。）

## 检索影响

- 单书探针 `meta_dense` Hit@5：9/28（初始）→ 24/28（查询锚点修复）→ **25/28（向量重建后）**。
- RRF Hit@5：27/28 → **28/28**。
- 原文语义、剧情摘要、原文关键词路由未受重建影响。
- 完整基线见 [`retrieval-baseline-multi-book-2026-11-13.md`](retrieval-baseline-multi-book-2026-11-13.md)，
  查询侧诊断见 [`meta-route-diagnosis-2026-11-13.md`](meta-route-diagnosis-2026-11-13.md)。

## 限制与后续

- 语料仍只有四本书、每本 8–109 个可检索 Scene，Hit@5 天花板效应明显；25/28 不能外推到大规模书库。
- 重建后 `meta-dense` 的余弦仍高于 `summary-dense`，标签词表收敛（Jaccard 0.29）尚未处理。
- 重建只改向量，不改 `style_summary` 等展示字段；若需要展示，可从 `meta_json.style_v1` 读取。
- 回滚：重新运行重建脚本即可；公开数据快照 `sql/fixtures/public-books/` 不受影响。
