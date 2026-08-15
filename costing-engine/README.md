# costing-engine · 统一造价引擎 v2

面向多行业（幼教 / 康养 / 农业…）、多地区、多商机的信息化项目造价测算引擎。

数据仓：`Fund-engineering/digital-costing/`
架构文档：`digital-costing/docs/ARCHITECTURE.md` ← **改代码前先读，尤其是「四条规矩」**

---

## 与 fund-review 的关系

本目录 **fork 自 `astra-studio-plugins/fund-review/`**（2026-08-15，v1 25,765 行 / 68 脚本）。

| | 服务对象 | 数据仓 | 状态 |
|---|---|---|---|
| `fund-review/` | 柳州幼教项目 | `clife-earlychild-care/` | **冻结在 v1**，服务至交付，只做必要维护 |
| `costing-engine/`（本目录） | 统一引擎 | `digital-costing/` | v2 开发中，康养为第一个真实商机 |

**为什么 fork 而不是共用**：为多行业做的改造会改变加载与裁剪行为，
而柳州项目正在 v1 上每日出数（本文写作当日仍在调整功能点归并与设备配置）。
两者演进节奏不同，强行共用等于让柳州承担 v2 的全部风险。

**不要把 v2 的改动回流 v1**。等柳州交付完成后再决定合并或归档。

---

## 待做的核心改造

v1 的计价逻辑（NESMA 计数、柳州标准取值、五阶段工作量、硬件六环链路、
守卫与交叉校验）全部有效，**不要重写**。要改的是数据加载与商机裁剪这一层。

### 1. BOM 多源加载

```
v1   Bom.load(bom_dir)                      单一目录
v2   Bom.compose(shared_dirs, vertical_dir, deal.compose)
```

- 按 `deal.compose` 的 `include` / `exclude_modules` 裁剪
- 各层独立 `VERSION`，组合结果写入 `deal.lock.json`
- **裁剪发生在加载期，不改任何产品级文件**（规矩二）

### 2. 条目 ID 命名空间校验

加载后校验：共享层 `FP.<SYS>.<seq>`、行业层 `FP.<VERT>.<SYS>.<seq>`。
**发现重复 ID 直接硬失败** —— v1 曾有 110 个重复 ID 导致 472 UFP 重复计数
与共创记录错配，跨行业后规模会放大。

### 3. 复用度 / 软件类别的 deal 覆盖

产品级 `taxonomy.yaml` 只记 `maturity` 缺省，deal 的 `overrides` 可逐子系统覆盖。
**覆盖必须带 basis**，非「低」复用度无依据时拒绝出表（沿用 v1 守卫）。

### 4. 飞书多 base

`bom/shared/lark.json` 与 `bom/verticals/*/lark.json` 各自配置 base token 与表映射。
拉取 / 推送按层进行，跨 base 天然隔离。

### 5. 基线命名加行业维度

```
v1   baselines/<pack>@bom-<ver>/
v2   baselines/<pack>@<vertical>@<ver>/
```

### 6. 新增：共享层变更影响面检查

改一条 `shared/` 的功能点，列出受影响的商机及各自金额变动。
v1 中改 BOM 直接重出即可；多行业下，共享层一次改动会同时影响多个在跑的商机。

### 7. 新增：跨行业一致性对账

同一子系统在不同商机的 UFP 应当相等（除非 `include` 范围不同）。
不等即说明有人在商机层改了不该改的东西。

---

## 验收标准

**唯一标准是金额。** 任何改造完成后，用同一份数据、同一个标准包重出套表，
金额必须一分不差。

不是单元测试通过，不是没报错 —— 是金额相等。
计价链路的缺陷不以异常形式出现，只会以「一个看起来挺合理的数」出现。
典型形状见 `ARCHITECTURE.md` 附录。

---

## 迁移路径

```
阶段 1   引擎支持多源加载，数据仍指向单一目录，验证行为不变
阶段 2   康养 BOM 入库，shared/ 抽取，ID 加命名空间
阶段 3   飞书建 base 并推送，读回验证
阶段 4   康养第一个真实商机跑通全链路
```

柳州数据**不迁**。等 v2 稳定、柳州交付完成后再决定归档或迁入做历史对账。
