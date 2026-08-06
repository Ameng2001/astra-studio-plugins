---
name: delivery-plan
description: '设定 BOM 各条目的交付形态（私有化/SaaS/买断/订阅），展开运营期费用清单与 TCO 对比，并跑一致性规则防重复计列。触发词："交付方式", "私有化还是SaaS", "运营期费用", "TCO对比", "/fund-review:delivery-plan"。'
allowed-tools: Read, Write, Bash, Glob
user-invocable: true
---

# delivery-plan

## 核心判断：不枚举场景

**部署形态是 BOM 逐条目的属性**，客户对 20 个模块做 20 个不同选择都能算。

常被当作五种独立场景的需求，其实是一张 2×2 矩阵（平台/模型 × 私有化/SaaS）：

| | 平台/应用 | 行业大模型 |
|---|---|---|
| **私有化** | 场景4 → `D1`/`D2` | 场景2 → `D4` |
| **SaaS** | 场景3 → `D3` | 场景1 → `D5` |

「应用私有化 + 模型 SaaS」不是第五种模式，是左下 ∪ 右上这一**组合格**。
枚举法表达不了任意组合，逐项指派天然可以。

## 交付形态

| 形态 | 建设期 | 运营期 |
|---|---|---|
| `D1` 平台-定制开发私有化 | CE-DEV + CE-HW + CE-IMPL + 设计/测试 | 运维（另立项目） |
| `D2` 平台-成品私有化买断 | CE-LIC + CE-HW + CE-IMPL | 质保期后运维 |
| `D3` 平台-SaaS订阅 | CE-IMPL（接入配置） | **CE-SUB → C-life** |
| `D4` 模型-私有化部署 | CE-LIC + CE-HW(GPU) + CE-IMPL | 外部大模型 API（**客户直付厂商**）+ 自有算力 |
| `D5` 模型-SaaS订阅 | CE-IMPL | **CE-SUB → C-life**（含算力/外部API/运维） |
| `D6` 知识库/数据集-定制建设 | CE-DEV + CE-IMPL | 更新迭代 |
| `D7` 硬件购置 | CE-HW（含≥3年质保） | 质保后运维 |

## 用法

交付方案用**规则匹配**而非逐条指定 —— 1500+ 条目手工指派不现实：

```yaml
defaults:                        # 先匹配到的先生效
  - match: {cls: HARDWARE}
    mode: D7
  - match: {system: 智能能力中枢-行业专业模型}
    mode: D5
  - match: {cls: SOFTWARE_FP}
    mode: D1
overrides:                       # 按 id 精确覆盖
  - {id: FP.PLAT.0001, mode: D3}
```

```bash
PYTHONPATH=$PLUG python3 $PLUG/quote_generate.py \
  --bom bom --pack standard-packs/<region> --out deals/<id> \
  --modes delivery-modes/modes.yaml \
  --internal-cost delivery-modes/internal-cost-model.yaml \
  --delivery-plan deals/<id>/delivery-plan.yaml \
  --scenarios delivery-modes/scenarios/*.yaml
```

产出 `…_Z03_运营期费用清单_….xlsx` 与 `…_Z04_交付方式TCO对比_….xlsx`。

## 三条容易做错的地方

**建设期必须依赖交付方案。** 不接交付方案，三个场景会算出同一个建设期总额，
TCO 对比毫无意义 —— SaaS 下不该有定制开发费（订阅就不用建）。
引擎按形态的 `construction` 列表过滤：不含 `CE-DEV` 的条目不走功能点法。

**运营期成本有聚合粒度。** 基础设施运维、推理算力、外部 API 都是**部署级**的，
不随系统数增加；订阅是**产品级**的。按系统累加会把 ¥25.8 万的运维放大到 ¥670 万。
粒度由 `cost_elements[].aggregation` 声明（`item` / `deployment` / `subscription`）。

**`in_scope=false` 的行必须列出但不计入。**
漏列 → 财评认为方案不完整（"运维怎么办"答不上来）；
计入 → 超出本标准科目范围（山东无运维科目）会被划掉。
列而不计并说明另行立项，才是正确做法。

## 一致性规则

| 规则 | 检查 | 级别 |
|---|---|---|
| R-1 | 同一系统内 SaaS 与私有化不得混用（跨系统混用合法，即场景5） | fail |
| R-2 | `D4` 必须配套 GPU；`D5` 不应出现 GPU | fail |
| R-3 | 订阅期内不得再单列同系统运维费 | fail |
| R-6 | 运营期每行必须有 payee 与 in_scope | fail |
| R-7 | `maturity=existing` 走 `D1` 告警（新建项目下降为 warn） | warn |
| R-8 | 形态须在区域标准包 `delivery_mode_support` 中被支持 | fail |

R-8 会读区域标准包。山东下 `定制软件租赁` 是 `not_in_scope`、`云资源租赁` 属
负面清单 —— 选了会直接 fail 并给出条文依据。

## 内部成本严格不外发

`internal-cost-model.yaml` 是商业敏感文件。对外只出 `list_price`（订阅价），
成本构成仅用于：① 毛利校验（低于阈值引擎报警）；② 被问「订阅价依据」时出
**脱敏后**的构成说明（只列构成项，不出成本数）；③ TCO 对比与谈判底线。

## 缺口不静默

走 `CE-LIC` 但 BOM 无许可价的条目，列为**待核价**而非静默计 0 ——
否则私有化方案会显得虚低，是自欺。TCO 表的「待核价与缺口」页逐条列出。
