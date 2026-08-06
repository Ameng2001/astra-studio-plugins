---
name: bom-sync-lark
description: '把造价 BOM 同步到飞书多维表格并把评审结果拉回。三条通道：BOM 共创（地域无关的产品事实，方案人员按子系统分表编辑）、功能点计算书（BOM × 某省标准包渲染的只读送审材料，一省一个 Base）、裁决表（实体核对/占位确认/类型裁决等人在环判断）。触发词："同步BOM到飞书", "推送共创表", "推送计算书", "生成某省计算书", "拉取飞书裁决", "/fund-review:bom-sync-lark"。'
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

## 飞书上只维护一样东西：BOM

| 东西 | 谁产出 | 要不要维护 |
|---|---|---|
| **BOM Base** | `lark_worksheet.py` push/pull | **是** —— 方案人员在这编辑产品事实 |
| 01–04 各表（sheet） | `quote_generate --publish-lark` | 否 —— 命令的快照产物，重跑覆盖 |
| 裁决表 Base | `lark_bom_sync.py` | 半 —— 评审人只填结论列 |

**计算书不做成 Base，它是命令。** 换省就是换 `--pack` 一个参数：

```bash
quote_generate --bom bom --pack standard-packs/<region> --out deals/<id> \
    --publish-lark <folder_token>
```

早先做过一版「计算书 Base」，把 1763 行明细复制到飞书维护 —— 那是错的。
飞书的关联字段只能在**同一个 Base 内**用（`link_table` 吃的是 Base 内作用域的
table_id），跨 Base 引用 BOM 做不到，于是只能复制。一份数据两处维护，
改了名字两边就静默分叉。而 `02-功能点测算表.xlsx` 本来就比那个 Base 更全：
它有逐项因子分列和「复算公式」列，Base 只有一个合成后的 US。

### BOM Base：地域无关，只放产品事实

一个 Base，**一张 `0 参数表` + 每个子系统一张表**。不做成单张大表，也不拆成
多个 Base：单表 1700+ 行方案人员翻不动；拆 Base 会让"全项目多少 UFP"变成
跨文件手工汇总，而那恰恰是最容易出错的地方。

```
子系统 → 一~四级模块 → 功能点计数项 → 功能描述 → 类别 → UFP
       → 应用类型 → 成熟度 → 成熟度依据 → 备注 → 条目ID
```

两张 `0 *` 表都随 BOM 一起 push，事实源在 git：

| 表 | 放什么 | 事实源 |
|---|---|---|
| `0 参数表` | NESMA 估算功能点法权重（GB/T 42588，三省一致）+ 元信息 | 人维护，push 不动 |
| `0 词表` | 受控词表：每个字段的合法值与含义，**一个数字都没有** | `bom/vocabulary.yaml` |

分两张是刻意的 —— 参数表里的东西都带取值，词表只回答「有哪些合法值、
各是什么意思」。混在一起会让人以为「智能信息」也有个取值藏在里面。

UFP 是引用参数表的公式列，push 不写（写了会被公式覆盖）。

**字典有两种，方向相反，别混：**

| | 是什么 | 在哪 |
|---|---|---|
| 受控词表 | BOM 条目能说自己是什么 | `bom/vocabulary.yaml` → `0 词表`，**地域无关** |
| 映射字典 | 某个省怎么读这套词表 | `pack.factors.*.aliases` / `unmapped`，**地域专属** |

「智能信息在广东叫人工智能」进 BOM 就是把地域知识塞回来。反过来，
词表进标准包会让每接一个省都要重新声明一遍合法值。

有了规范词表，`pack.vocabulary_coverage(vocab)` 能在**选定标准包时**就报出
缺口，而不是等生成到第 800 行报价才 `PackError`：

```
shandong-2024      app_type ok        覆盖 7 项
guangdong-2018-sw  app_type ok        覆盖 4 项，已声明无对应 3 项
                   dev_category n/a   本包无此维度
```

`missing`（既无取值也无声明）是真遗漏，接新省份时最该先补这个；
`unmapped` 是已知缺口，用到时须业务侧裁定归入哪一类。

**不在这里的**：US、复用度、修改类型、任何地域系数。

### 复用度与修改类型是算出来的，不是填出来的

这两个曾经是计算书上让人逐行填的列，错在两处：

1. **复用度的事实 BOM 里早就有** —— `maturity`（new/partial/existing）。
   让人再填一遍等于同一事实录两次。
2. **档位词表各省不同** —— 山东是「新建 / 升级改造_*」，广东是「高/中/低」，
   取值也不一样。照抄样例表的「低」写进山东计算书，参数表里查不到，
   FILTER 落空得 0，**1763 行 US 全塌成 0 而且不报错**。

现在：`maturity` 存在 BOM（产品事实），映射表在标准包的
`factors.reuse.aliases_by_project_type`（各省认领自己的档位），
引擎 `CostingEngine.reuse_level(item)` 逐条解析。

修改类型同理由 `project_type` 解析：新建项目一律「新增」，恒为 1.0。

新建项目下山东三个 maturity 都映射到「新建」= 1.0 —— 标准明文规定与供应商的
产品成熟度无关（产品成熟度是内部成本口径，不是甲方的既有系统）。
**等于忽略这一维，但忽略是算出来的**，不是靠人填 1763 个「低」。

配置 `lark-worksheet.json`：

```json
{ "base_token": "...", "region_neutral": true, "param_table": "tbl...",
  "tables": { "<BOM 里的 system 名>": "tbl...", ... },
  "table_names": { "<system 名>": "1.1 平台能力", ... } }
```

```bash
PYTHONPATH=$PLUG python3 $PLUG/lark_worksheet.py push --bom bom --config bom/lark-worksheet.json
PYTHONPATH=$PLUG python3 $PLUG/lark_worksheet.py pull --bom bom --config bom/lark-worksheet.json --out bom/lark-worksheet-pull
```

只有 `is_fp_counted()` 为真的条目进表。HARDWARE、采购科目条目不在表里是设计
如此，pull 比对时按同一集合过滤，否则会报一片假的"疑删除"。

## 通道三：裁决表

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
10. **`+table-list` 返回 `data.tables[]`，键是 `id` / `name`** —— 不是
    `items[].table_id`。猜键名会让「表已存在」被误判成「建表失败」然后重复建
11. **列文件夹是 `drive files list`（原生资源），没有 `drive +list`** ——
    用错会让「查重」静默返回空，于是重复建 Base。查重命令失败时必须硬报错，
    不能当成「没找到」
12. **`+table-create` 用 `--name` + `--fields`；`drive +delete` 用 `--file-token`**
    （不是 `--token`）。这一族命令的 `--json` 都是**输出格式简写**，不是载荷
13. **建资源的命令会在 JSON 前先打一行散文** —— `+create-folder`、`+table-create`
   都是。解析崩了**不代表操作失败**。永远先看 `ok`，再用 `+table-list` /
   `+folder-list` 回查 id，不要靠猜返回结构的键名。当初就是解析崩了以为失败、
   重跑一遍，建出了两份文件夹和两个 Base

## 整表覆盖：先写后删，写前校验

三条通道的写入都走 `lark_table.replace_all()`。这个模块存在的原因是一次
真实事故：原本是「先清空再写入」，脚本还在写一个已被删掉的列 ——
清空成功、写入失败，**15 张表全空**。飞书不报错，只是什么都没有了。

```
preflight  → 记下旧 id → 写新行 → 删旧行 → 轮询断言行数
```

**`preflight` 必须在删任何东西之前跑。** 它静态查三类会让整批写入失败的问题：

| 查什么 | 实测拦住 |
|---|---|
| 写表里不存在的列 | `['修改类型', '重用程度']`（本次事故形态） |
| 往公式/lookup 等派生列写值 | `['UFP']` |
| 单选列写不存在的选项 | `类别` 写 `XYZ` |

**顺序是先写后删，不是先删后写。** 任何时刻表里都不是空的。中途失败留下的是
重复行，而重复行**下次 push 会自动清掉**（删的是本次开始前就存在的那批），
是自愈的；空表不会自愈。代价是中途表里短暂有 2× 行。

**收尾断言要轮询，不能一读定论。** 删除返回 ok 之后列记录未必立刻反映 ——
实测删完立刻读还是 76 旧 + 76 新，等几秒才对。一读定论会把正常的延迟报成
「删除失败」，比不检查更糟。

**不做临时表切换。** 看着最干净，但跨表公式里嵌的是 `table_id`，换表要重建
所有公式列和配置；而建表/删表本身有频控（实测撞过 `OpenAPIDeleteField
limited`）。风险比它要解决的问题还大。

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
