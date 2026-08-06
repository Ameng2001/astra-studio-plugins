---
name: bom-sync-lark
description: '把造价 BOM 推送到飞书多维表格供业务侧共创评审，并把裁决结果拉回生成 diff 报告。两条通道：功能点计算书（方案人员按子系统分表直接编辑）与裁决表（ILF 实体核对、占位确认、类型裁决等人在环判断）。触发词："同步BOM到飞书", "推送共创表", "推送计算书", "拉取飞书裁决", "/fund-review:bom-sync-lark"。'
allowed-tools: Read, Write, Bash, Glob
user-invocable: true
---

# bom-sync-lark

飞书上有**两条互不干扰的通道**，服务两拨人：

| 通道 | 脚本 | 配置 | 给谁用 |
|---|---|---|---|
| **功能点计算书** | `lark_worksheet.py` | `lark-worksheet.json` | 方案人员按子系统直接编辑功能点清单 |
| **裁决表** | `lark_bom_sync.py` | `lark-sync.json` | 评审人处理占位、实体核对、类型裁决 |

计算书是主线 —— 它的形态对齐财评《功能点计算书》，方案人员打开就能改。
裁决表是质检的人在环补充，不要拿它当共创入口。

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

## 通道一：功能点计算书

一个 Base，**一张 `0 参数表` + 每个子系统一张表**。不做成单张大表，也不拆成
多个 Base：单表 1700+ 行方案人员翻不动；拆 Base 会让"全项目多少 UFP"变成
跨文件手工汇总，而那恰恰是最容易出错的地方。

### 一个 Base 对应一个标准包

BOM 本体是区域中立的 —— 它存 `app_type: 智能信息` 这样的**分类名**，不存 1.5。
但计算书要算出 US 就必须落到某个省，所以**计算书 Base 天然是区域专属的**，
名字必须说清楚（`康养功能点计算书（山东 2024）`，不要叫「…BOM」）。

换省不是改参数表数字的事：

| | 山东 2024 | 广东 2018 软件分册 |
|---|---|---|
| 规模变更因子 | 1.21 | **没有这个环节** |
| 应用类型词表 | 7 类（智能信息 1.5） | 4 类（**人工智能** 1.5） |
| 开发类别系数 | 6 类 0.8–1.1 | **没有** |

US 是**公式结构**不同。所以：

- `0 参数表` 头三行是元信息（标准包 / 计数方法 / 换省提示），取值列留空 ——
  FILTER 按参数类别匹配不到它们，不进任何公式
- 配置带 `pack_id`，`push` 前调 `check_pack()` 与元信息行比对，不一致直接拒写。
  拿山东的 Base 推广东的活**不会报任何错，只会静默算错**
- 做另一个省时另建 Base，用该省的公式档案生成 US

列顺序对齐财评计算书：`子系统 → 一~四级模块 → 功能点计数项 → 类别 →
UFP → 应用类型 → 重用程度 → 修改类型 → US → 备注`。

**UFP 与 US 是公式列，不是数据列。** 它们跨表引用 `0 参数表`（20 行：
功能点权重 5 + 重用系数 3 + 修改类型系数 3 + 应用类型系数 7 + 规模变更因子 2）：

```
UFP = SUM([0 参数表].FILTER(AND(参数类别="功能点权重", 参数名=[类别])).[取值])
US  = ROUND(UFP × 规模变更因子 × 重用系数 × 修改类型系数 × 应用类型系数, 2)
```

好处是系数口径只有一处可改：改 `0 参数表` 里 EO=5，15 张表一起变
（飞书公式重算是**异步的，有数秒延迟**，刚改完立刻回读可能拿到旧值，
不要据此判断联动失效）。坏处是 push 不能写 UFP/US —— 写了会被公式覆盖，
脚本只写数据列。

**应用类型不能省。** AI 类子系统因子 1.5、业务处理 1.0，漏掉这一项会把
5 个 AI 子系统整体少算三分之一。但它**不是靠给每个子系统各配一张参数表**
解决的 —— 系数取值是标准规定的（全省统一，L1，进共享参数表），
某子系统属于哪一类是产品事实（L0，BOM 的 `app_type`，进子系统表的
「应用类型」列）。拆参数表等于把同一个 1.5 抄 5 遍，标准改版要改 5 处，
而且谁改了自己那张表都没人察觉。

**逐条 ROUND 到 2 位**，与引擎 `xlround(afp, 2)` 对齐。不加这层，AI 类
子系统每个 EO 差 0.005（5×1.21×1.5 = 9.075 vs 9.08），227 行的平台能力
就差 0.24 —— 财评拿计算书对测算表时对不上，得当场解释。

配置 `lark-worksheet.json`：

```json
{ "base_token": "...", "pack_id": "shandong-2024", "param_table": "tbl...",
  "tables": { "<BOM 里的 system 名>": "tbl...", ... },
  "table_names": { "<system 名>": "1.1 平台能力", ... } }
```

`tables` 的键必须是 BOM `path.system` 的原值，`table_names` 是飞书上的短表名
（表名有长度限制，写全称会被截断）。

```bash
PYTHONPATH=$PLUG python3 $PLUG/lark_worksheet.py push --bom bom --config bom/lark-worksheet.json
PYTHONPATH=$PLUG python3 $PLUG/lark_worksheet.py pull --bom bom --config bom/lark-worksheet.json --out bom/lark-worksheet-pull
```

push 只覆盖子系统表，**不动 `0 参数表`** —— 那是人维护的。
pull 逐表读回，用表名反查 `子系统` 补齐（飞书上这列可以留空）。

只有 `FP_COUNTED_CLASSES` 且带 `nesma` 的条目进计算书。HARDWARE 等类别不在
表里是设计如此，pull 比对时按同一集合过滤，否则会报一片假的"疑删除"。

## 通道二：裁决表

| 表 | 内容 | 谁填 |
|---|---|---|
| **功能点清单** | BOM 全量快照 | 只读；复核人/复核意见两列可填 |
| **占位待确认** | `tags: [placeholder]` 的条目 + 待确认问题 | 业务侧填「确认结论」 |
| **C-ILF实体核对** | 建议的逻辑文件实体清单 | 业务侧填「核对结论」 |
| **F-类型裁决** | 竞争类型无文本依据可拆的条目 | 业务侧填「裁决结论」 |
| **变更提案** | 空表，业务侧提结构性变更 | 业务侧新建行 |

每张裁决表都有一个 `★待…` 视图，筛掉已处理项，业务侧直接从视图开工。

### 用法

```bash
PLUG=<plugin>/scripts
PYTHONPATH=$PLUG python3 $PLUG/lark_bom_sync.py push --bom bom --config bom/lark-sync.json
PYTHONPATH=$PLUG python3 $PLUG/lark_bom_sync.py pull --bom bom --config bom/lark-sync.json --out bom/lark-pull
```

首次搭建（建 Base + 建表 + 建视图）目前是手工编排 `lark-cli base +base-create /
+table-create / +record-batch-create / +view-create / +view-set-filter`，
完成后把返回的 token 与各 id 写进 `lark-sync.json`。脚本负责此后的例行同步。

## lark-cli 的坑

全部踩过并已在两个脚本中处理。改动脚本时不要退回去：

1. **指定输出格式用 `--format json`，不是 `--json`** —— `--json` 在
   `+record-batch-create` / `+record-delete` 上是**载荷参数**。两者混用会拼成
   `--json <载荷> --json`，末尾那个没有参数，直接报 `flag needs an argument`
2. **`+record-delete` 的载荷是 `--json {"record_id_list":[...]}`**，没有
   `--record-id` 这个形式
3. **清空必须反复取页直到表空** —— 单次 `+record-list` 只回一页（200 条）。
   取一页删完就退出，会留下剩余旧记录与新数据叠加。实测 1764 条只删了 200，
   push 后表里 3327 行。**它不报错，只是静默留下一半旧数据** —— 用
   `push` 后行数是否等于 BOM 条数来对账
4. **`+record-list` 返回位置数组** —— `data.data` 是每行一个数组，列顺序由
   `data.fields` 给出，不是字段名→值的字典。必须 zip 成字典再按名取值
5. **没有 `total` 字段** —— 分页靠 `has_more` 判断；批量写入的返回
   `record_id_list` 长度是精确的写入条数，用它对账
6. **`--json @file` 只吃相对于 cwd 的相对路径；`--fields` 完全不接受 `@file`**，
   必须内联
7. **改公式字段要先读指南再加 `--i-have-read-guide`** —— `+field-update`
   在 `--json.type` 为 `formula` 时会硬性拒绝，让你先读
   `skills/lark-base/references/formula-field-guide.md`。表达式写**名称**语法
   （`[0 参数表].FILTER(...)`），飞书存储时自己归一化成 `$table[id].$field[id]`。
   硬约束：FILTER / SUMIF / COUNTIF / MAP **不得互相嵌套**（并列相乘可以）
8. **`+field-create` 用的是 `field-list` 的返回形状**（`name` / `type` 字符串 /
   `options` 平铺），不是 OpenAPI 的 `field_name` / 数字 type / `property` 嵌套。
   混用报 `Invalid discriminator value`
9. **建列返回 ok 后 `field-list` 未必立刻可见** —— 最终一致。不重试就会在
   下一步取 field id 时 KeyError，而列其实已经建好了
10. **建资源的命令会在 JSON 前先打一行散文** —— `+create-folder`、`+table-create`
   都是。解析崩了**不代表操作失败**。永远先看 `ok`，再用 `+table-list` /
   `+folder-list` 回查 id，不要靠猜返回结构的键名。当初就是解析崩了以为失败、
   重跑一遍，建出了两份文件夹和两个 Base

## 冲突策略

- pull **永不自动覆盖** `status: released` 的条目 —— 触及即写入 `conflicts.md`，人工裁定
- 主表被业务侧误改不影响 git；下次 `push` 会覆盖回去
- 结构性变更不接受直接改主表，只认「变更提案」表

## 注意

- push 会**先清空再重写**。增量合并对只读快照没有意义，且容易留下幽灵行
- 批量写入单次上限 200 条，脚本已分批；连续写同一表要串行
- `select` 字段只能写入字段中已存在的选项，新增取值要先改字段
- 计算书 push 一轮 15 张表要两分钟量级（每表一清一写、逐页删）。别设短超时，
  中途掐断会留下清空了但没写回的表
