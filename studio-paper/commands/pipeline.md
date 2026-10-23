---
description: Run the whole paper pipeline for one paper in four stages — positioning, proposal, experiments, review — pausing after each stage
argument-hint: [paper-id] [--from positioning|proposal|experiments|review]
---

# /studio-paper:pipeline {paper-id}

按四段串联技能，每段结束暂停给用户确认；回流（偏离、fatal、拒稿改投）由人决定后从对应段重入。

1. **定位** — `position-paper`（闸门 D2 论点句）
2. **开题** — `propose-experiments`（闸门 D3 判据冻结）
3. **实验** — `record-run` ×N（可 `--import studio-evolve`）→ `freeze-results`（闸门 D7）→ 交 studio-docs 写初稿
4. **评审** — `review-internally --pass 1` → 改稿 → `review-internally --pass 2`（闸门 D8）→ `check-submission`（闸门 D9）→ 人提交 → 外审后 `respond-reviews`（闸门 D11）→ `check-submission --camera-ready`

开始前检查 `.studio-paper/{paper-id}/` 存在，否则先跑 `init-workspace`。`--from` 从指定段重入。
