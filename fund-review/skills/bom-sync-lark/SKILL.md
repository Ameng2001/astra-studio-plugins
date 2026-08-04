---
name: bom-sync-lark
description: '把造价 BOM 推送到飞书多维表格供业务侧共创评审，并把裁决结果拉回生成 diff 报告。用于 ILF 实体核对、占位确认、类型裁决等需要人在环判断的环节。触发词："同步BOM到飞书", "推送共创表", "拉取飞书裁决", "/fund-review:bom-sync-lark"。'
allowed-tools: Read, Write, Bash, Glob
user-invocable: true
---

# bom-sync-lark

**git 是发布层，飞书是评审层。** 两个方向语义不对称，不要当成普通双向同步。

| 方向 | 行为 |
|---|---|
| `push` | git 版本 → 覆盖飞书主表。主表是**只读快照**，结构性变更走「变更提案」表 |
| `pull` | 飞书裁决 → **只生成 diff 报告，不改 BOM**。落实须经 `bom_apply` 并记 CHANGELOG |

为什么 pull 不直接落库：飞书上的一次误点击不应该直接改变报价基线。
裁决结果要经人工确认后，由 `bom_apply` 作为一次有版本号、有 CHANGELOG 的变更落实。

## 前置

- `lark-cli` 已认证（`lark-cli auth status`），需要 `base:*` scope
- `bom/lark-sync.json` 记录 `base_token` 与各表 `table_id`

> `+title-resolve` 需要 `search:docs:read` scope。缺失时无法按标题查重，
> 此时不要盲目新建 —— 先向用户确认目标 Base 是否已存在。

## 表结构

| 表 | 内容 | 谁填 |
|---|---|---|
| **功能点清单** | BOM 全量快照 | 只读；复核人/复核意见两列可填 |
| **占位待确认** | `tags: [placeholder]` 的条目 + 待确认问题 | 业务侧填「确认结论」 |
| **C-ILF实体核对** | 建议的逻辑文件实体清单 | 业务侧填「核对结论」 |
| **F-类型裁决** | 竞争类型无文本依据可拆的条目 | 业务侧填「裁决结论」 |
| **变更提案** | 空表，业务侧提结构性变更 | 业务侧新建行 |

每张裁决表都有一个 `★待…` 视图，筛掉已处理项，业务侧直接从视图开工。

## 用法

```bash
PLUG=<plugin>/scripts
PYTHONPATH=$PLUG python3 $PLUG/lark_bom_sync.py push --bom bom --config bom/lark-sync.json
PYTHONPATH=$PLUG python3 $PLUG/lark_bom_sync.py pull --bom bom --config bom/lark-sync.json --out bom/lark-pull
```

首次搭建（建 Base + 建表 + 建视图）目前是手工编排 `lark-cli base +base-create /
+table-create / +record-batch-create / +view-create / +view-set-filter`，
完成后把返回的 token 与各 id 写进 `lark-sync.json`。脚本负责此后的例行同步。

## lark-cli 的三个坑

踩过并已在脚本中处理，改动脚本时不要退回去：

1. **默认输出是 markdown，不是 JSON** —— 所有调用必须显式加 `--json`
2. **`+record-list` 返回位置数组** —— `data.data` 是每行一个数组，列顺序由
   `data.fields` 给出，不是字段名→值的字典。必须 zip 成字典再按名取值
3. **没有 `total` 字段** —— 分页靠 `has_more` 判断；批量写入的返回
   `record_id_list` 长度是精确的写入条数，用它对账

## 冲突策略

- pull **永不自动覆盖** `status: released` 的条目 —— 触及即写入 `conflicts.md`，人工裁定
- 主表被业务侧误改不影响 git；下次 `push` 会覆盖回去
- 结构性变更不接受直接改主表，只认「变更提案」表

## 注意

- push 会**先清空再重写**主表。增量合并对只读快照没有意义，且容易留下幽灵行
- 批量写入单次上限 200 条，脚本已分批；连续写同一表要串行
- `select` 字段只能写入字段中已存在的选项，新增取值要先改字段
