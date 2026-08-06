"""nesma_rules — BOM 质量门禁 G-01..G-10。

设计要点：门禁不是"通过/不通过"两态，而是**按目标状态分级**。
一条 draft 条目允许没有 rationale；但它想升到 reviewed 就必须补上。
每条发现因此带 `blocks`（阻断晋升到哪个状态），报告据此回答
"当前版本能升到哪一级、还差什么"，而不是笼统地报一堆红字。

依赖：仅标准库 —— 相似度用字符 3-gram Jaccard + 倒排索引，不引入 sklearn。
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

from bom_schema import (DUAL_METHOD_CLASSES, FP_COUNTED_CLASSES, STATUS_ORDER,
                        Bom, BomItem, Vocabulary, is_fp_counted, is_purchase)

# 单一功能点类型占比上限 —— 超过即判定为「批量打标」而非逐条识别
SINGLE_TYPE_MAX_RATIO = 0.80
# 描述最短字数
MIN_DESC_LEN = 15
# 跨 system 描述相似度告警线
SIMILARITY_THRESHOLD = 0.85
# 相对上一 released 版本的 FP 漂移告警线
FP_DRIFT_THRESHOLD = 0.15

#: 描述中应出现的动作词 —— 缺失说明该行只是名词短语，无法判定功能点类型
ACTION_WORDS = [
    "支持", "提供", "实现", "展示", "显示", "查询", "检索", "搜索", "筛选", "统计",
    "新增", "创建", "录入", "上报", "采集", "导入", "导出", "修改", "编辑", "删除",
    "配置", "设置", "管理", "维护", "审批", "提交", "发起", "推送", "通知", "告警",
    "生成", "计算", "分析", "预测", "识别", "推荐", "评估", "输出", "同步", "对接",
    "监控", "调度", "训练", "标注", "发布", "注册", "校验", "结算", "派单",
]


@dataclass
class Finding:
    gate: str
    severity: str                 # fail | warn
    blocks: str | None            # 阻断晋升到该状态；None = 仅提示
    scope: str                    # item | system | bom
    target: str                   # 条目 id 或 system 名
    message: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class GateReport:
    findings: list[Finding]
    stats: dict[str, Any]

    def by_gate(self) -> dict[str, list[Finding]]:
        out: dict[str, list[Finding]] = defaultdict(list)
        for f in self.findings:
            out[f.gate].append(f)
        return dict(out)

    def blocking(self, target_status: str) -> list[Finding]:
        """阻断晋升到 target_status 的发现。"""
        lvl = STATUS_ORDER[target_status]
        return [f for f in self.findings
                if f.blocks is not None and STATUS_ORDER[f.blocks] <= lvl]

    def highest_reachable_status(self) -> str:
        """在不修任何问题的前提下，本 BOM 最高能升到哪一级。"""
        for status in ("released", "reviewed", "draft"):
            if not self.blocking(status):
                return status
        return "draft"


# ---- 单条目门禁 --------------------------------------------------------


def g01_identity(bom: Bom) -> list[Finding]:
    """G-01 id 唯一、格式合法。结构性错误由 Bom.validate 抛出，这里只查重复。"""
    out = []
    seen = Counter(i.id for i in bom.items)
    for iid, n in seen.items():
        if n > 1:
            out.append(Finding("G-01", "fail", "draft", "item", iid,
                               f"id 重复 {n} 次"))
    return out


def g02_nesma_type(bom: Bom) -> list[Finding]:
    """G-02 功能点类型合法（a，硬性）+ 判定理由完整（b，升 reviewed 前必补）。"""
    out = []
    for it in bom.active():
        if it.cls not in FP_COUNTED_CLASSES or is_purchase(it):
            continue          # 采购口径的 KB/DATASET 条目本就不该有 nesma
        if it.nesma is None:
            out.append(Finding("G-02a", "fail", "draft", "item", it.id, "缺 nesma 段"))
            continue
        if not (it.nesma.rationale or "").strip():
            out.append(Finding("G-02b", "fail", "reviewed", "item", it.id,
                               "缺 nesma.rationale（类型判定理由）"))
        if not (it.nesma.reviewed_by or "").strip():
            out.append(Finding("G-02c", "fail", "released", "item", it.id,
                               "缺 nesma.reviewed_by（双人复核）"))
    return out


def g05_description(bom: Bom) -> list[Finding]:
    """G-05 描述可判定性 —— 太短或无动作词，无法据以判定功能点类型。"""
    out = []
    for it in bom.active():
        if not is_fp_counted(it):
            continue
        desc = (it.description or "").strip()
        if len(desc) < MIN_DESC_LEN:
            out.append(Finding("G-05", "warn", "released", "item", it.id,
                               f"描述仅 {len(desc)} 字（<{MIN_DESC_LEN}）",
                               {"description": desc}))
        elif not any(w in desc for w in ACTION_WORDS):
            out.append(Finding("G-05", "warn", "released", "item", it.id,
                               "描述无动作词，仅名词短语，无法判定功能点类型",
                               {"description": desc[:60]}))
    return out


def g07_class_consistency(bom: Bom) -> list[Finding]:
    """G-07 类别与计数方式一致 —— 硬件/成品不得走功能点法，反之亦然。"""
    out = []
    for it in bom.active():
        dual = it.cls in DUAL_METHOD_CLASSES
        needs = it.cls in FP_COUNTED_CLASSES
        if needs and it.nesma is None and not (dual and is_purchase(it)):
            out.append(Finding("G-07", "fail", "draft", "item", it.id,
                               f"class={it.cls} 应走功能点法但无 nesma 段，"
                               f"也没有 spec.subject —— 造价口径未定"))
        if not needs and it.nesma is not None:
            out.append(Finding("G-07", "fail", "draft", "item", it.id,
                               f"class={it.cls} 不走功能点法却带 nesma 段"))
        if dual and it.nesma is not None and is_purchase(it):
            out.append(Finding("G-07", "fail", "draft", "item", it.id,
                               f"class={it.cls} 同时有 nesma 与 spec.subject —— "
                               f"同一条目既按功能点计又按购置计，构成重复计列"))
        if it.cls == "HARDWARE" and not it.spec:
            out.append(Finding("G-07", "warn", "released", "item", it.id,
                               "硬件条目缺 spec（型号/参数/询价基线）"))
    return out


def g08_maturity_evidence(bom: Bom) -> list[Finding]:
    """G-08 成熟度举证 —— 声称已有必须说明已有什么。

    这是财评最高危的一条：既有功能按新开发报价，或反过来，
    有复用事实却不在复用度因子上体现，两边都会被挑战。
    """
    out = []
    for it in bom.active():
        if it.maturity in ("existing", "partial") and not (it.maturity_evidence or "").strip():
            out.append(Finding("G-08", "fail", "reviewed", "item", it.id,
                               f"maturity={it.maturity} 但缺 maturity_evidence"))
    return out


# ---- 系统级门禁 --------------------------------------------------------


def g03_type_distribution(bom: Bom) -> list[Finding]:
    """G-03 单一功能点类型占比 —— 超阈值即判定为批量打标。"""
    out = []
    for system, items in bom.by_system().items():
        types = Counter(i.nesma.type for i in items
                        if is_fp_counted(i))
        total = sum(types.values())
        if total < 5:
            continue
        top_type, top_n = types.most_common(1)[0]
        ratio = top_n / total
        if ratio > SINGLE_TYPE_MAX_RATIO:
            out.append(Finding("G-03", "fail", "reviewed", "system", system,
                               f"{top_type} 占 {ratio:.0%}（{top_n}/{total}），"
                               f"超过 {SINGLE_TYPE_MAX_RATIO:.0%} —— 疑为批量打标",
                               {"distribution": dict(types)}))
    return out


def g04_missing_ilf(bom: Bom) -> list[Finding]:
    """G-04 无任何逻辑文件的 system —— 系统性漏计。

    查的是 **ILF + ELF 都为零**，不是只查 ILF。一个子系统可以合理地不维护
    自己的数据（纯展示/纯推理），但它总得**引用**什么 —— EO/EQ 按定义要
    引用逻辑文件。两者都是 0 意味着这批功能点凭空产出数据，不成立。

    只查 ILF 会把「正确地全部记为 ELF」的子系统误报，那是在惩罚正确建模。
    """
    out = []
    for system, items in bom.by_system().items():
        fp_items = [i for i in items if is_fp_counted(i)]
        if len(fp_items) < 5:
            continue
        types = Counter(i.nesma.type for i in fp_items)
        if types.get("ILF", 0) + types.get("ELF", 0) == 0:
            out.append(Finding("G-04", "fail", "reviewed", "system", system,
                               f"{len(fp_items)} 个功能点中 ILF 与 ELF 均为 0 —— "
                               f"EO/EQ 按定义须引用逻辑文件，两者皆无不成立",
                               {"distribution": dict(types)}))
    return out


def g15_shared_logical_file(bom: Bom) -> list[Finding]:
    """G-15 同一逻辑数据组跨子系统重复记 ILF。

    规则：**一份逻辑数据组只有一个维护方**，维护方记 ILF（权重 10），
    其余引用方记 ELF（权重 7）。

    这条是 G-04 的镜像，也是它此前照不到的地方：G-04 只问「有没有 ILF」，
    答得上就放行 —— 于是「长者档案」在 5 个子系统各记一次 ILF、「订单」记 6 次，
    合计 25 条重复计列（250 UFP），全部通过门禁。

    真正的风险不是那点 UFP，是评审一眼看穿重复计列之后，**整份功能点计数
    都需要重新举证**。所以这条按 fail 报，且阻断 reviewed。

    重名而非同物的情况（如「文档」在行政域与智能体域各有一份）用
    `nesma.logical_file_note` 说明后即视为已裁定，不再报。
    """
    out = []
    groups: dict[str, list] = defaultdict(list)
    for i in bom.active():
        if not is_fp_counted(i) or i.nesma.type != "ILF":
            continue
        key = i.name.replace("-维护", "").replace("-台账", "").strip()
        groups[key].append(i)

    for name, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        # 已逐条裁定过（写了判定依据）的不再报 —— 包括「重名不同物」
        undecided = [i for i in members
                     if not (i.nesma.logical_file_note or "").strip()]
        if not undecided:
            continue
        systems = sorted({i.path.system for i in members})
        out.append(Finding(
            "G-15", "fail", "reviewed", "bom", name,
            f"逻辑数据组「{name}」在 {len(systems)} 个子系统各记一次 ILF —— "
            f"一份数据只能有一个维护方，其余应记 ELF（10→7）；"
            f"其中 {len(undecided)} 条尚未裁定",
            {"systems": systems,
             "undecided_ids": [i.id for i in undecided][:8],
             "excess_ufp": (len(members) - 1) * 10,
             "fix": "维护方填 nesma.logical_file_role=maintainer，"
                    "引用方改 type=ELF 且 logical_file_role=reference；"
                    "确属重名不同物的，写 logical_file_note 说明即可"}))
    return out


def g11_name_uniqueness(bom: Bom) -> list[Finding]:
    """G-11 名称在系统内不唯一 —— 无法逐条追溯，也说明拆分粒度未到基本处理级。

    源表约 45% 的「功能点名称」是描述的机器截断，导入时退回层级名兜底，
    因而同一层级下的多个功能点会重名。这些必须在 P2 重新命名。
    """
    out = []
    for system, items in bom.by_system().items():
        names = Counter(i.name for i in items if is_fp_counted(i))
        dups = {n: c for n, c in names.items() if c > 1}
        if dups:
            worst = sorted(dups.items(), key=lambda kv: -kv[1])[:5]
            out.append(Finding("G-11", "warn", "released", "system", system,
                               f"{len(dups)} 个名称重复（共 {sum(dups.values())} 条），"
                               f"最多的：{', '.join(f'{n}×{c}' for n, c in worst)}",
                               {"duplicates": dups}))
    return out


#: 占位条目的标记 —— 由 bom_apply 在无法从文本判定时生成，待共创环节确认
PLACEHOLDER_TAG = "placeholder"


def g12_placeholders(bom: Bom) -> list[Finding]:
    """G-12 占位条目未确认 —— 必须在共创环节裁定后才能进入 reviewed。

    占位是「已知的未知」：源表没写、无法从文本判定，但又不能当作不存在。
    生成占位条目把问题显式化，靠本门禁保证它不会被遗忘地混进正式版本。
    """
    ph = [i for i in bom.active() if PLACEHOLDER_TAG in i.tags]
    if not ph:
        return []
    by_system: dict[str, int] = defaultdict(int)
    for i in ph:
        by_system[i.path.system] += 1
    return [Finding("G-12", "fail", "reviewed", "bom", "-",
                    f"{len(ph)} 条占位条目待确认（{dict(by_system)}）—— "
                    f"须在飞书共创环节裁定后才能进入 reviewed",
                    {"ids": [i.id for i in ph[:20]], "total": len(ph)})]


#: 各科目的询价举证要求由标准包给出；这里只查「该填的字段填了没」。
#: 单价可以空（询价单是商务后补的），但科目和举证清单不能空 ——
#: 那说明这条根本没想清楚该报到哪一行。
_PURCHASE_REQUIRED = ("subject", "subject_code", "pricing_model",
                      "evidence_required", "pricing_basis")


def g13_purchase_subject(bom: Bom) -> list[Finding]:
    """G-13 采购条目的科目与举证完整性。

    分三档，因为这三件事的补齐时机完全不同：
      - 科目/举证字段缺失 → 阻断 draft，这是编制时就该定的
      - 单价缺失          → 阻断 released，等商务询价单
      - 与功能点条目双计   → 阻断 draft，重复计列是硬伤
    """
    out: list[Finding] = []
    no_price = []
    for it in bom.active():
        if not is_purchase(it):
            continue
        missing = [k for k in _PURCHASE_REQUIRED if not it.spec.get(k)]
        if missing:
            out.append(Finding("G-13a", "fail", "draft", "item", it.id,
                               f"采购条目缺 spec.{'/'.join(missing)}",
                               {"subject": it.spec.get("subject")}))
        if it.spec.get("reference_unit_price_yuan") is None:
            no_price.append(it)
        if str(it.spec.get("pricing_model", "")).startswith("TODO"):
            out.append(Finding("G-13c", "warn", "reviewed", "item", it.id,
                               f"授权/计费方式未定（{it.spec['pricing_model']}）—— "
                               f"它决定该条进建设期还是运营期，须在共创环节裁定"))
    if no_price:
        by_subject: dict[str, int] = defaultdict(int)
        for i in no_price:
            by_subject[i.spec["subject"]] += 1
        out.append(Finding("G-13b", "fail", "released", "bom", "-",
                           f"{len(no_price)} 条采购条目无单价（{dict(by_subject)}）—— "
                           f"引擎列为待询价不计入金额；出正式报价前须补齐盖章询价单",
                           {"ids": [i.id for i in no_price[:20]],
                            "total": len(no_price)}))
    return out


def g14_vocabulary(bom: Bom) -> list[Finding]:
    """G-14 受控词表 —— app_type / dev_category 合法且该填的填、该空的空。

    这两个字段此前**完全没有校验**，合法值实际借用山东包的 factor 键。
    填错要等到算价时 `PackError` 才暴露，一次一条；非功能点条目误填则更隐蔽 ——
    它不会报错，只会让人以为那一条参与了测算。
    """
    vocab = Vocabulary.load(bom.root) if bom.root else Vocabulary()
    if not vocab.fields:
        return []
    out = []
    for it in bom.active():
        for msg in vocab.check(it):
            out.append(Finding("G-14", "fail", "draft", "item", it.id, msg))
    return out


def g09_gpu_dependency(bom: Bom) -> list[Finding]:
    """G-09 声明需要推理算力，但 BOM 中无对应硬件条目。"""
    needy = [i for i in bom.active() if i.runtime.needs_inference_gpu]
    if not needy:
        return []
    has_gpu = any(i.cls == "HARDWARE" and
                  ("GPU" in str(i.spec).upper() or "GPU" in i.name.upper())
                  for i in bom.active())
    if has_gpu:
        return []
    return [Finding("G-09", "warn", "released", "bom", "-",
                    f"{len(needy)} 个条目声明 needs_inference_gpu，但 BOM 中无 GPU 硬件条目",
                    {"items": [i.id for i in needy[:10]]})]


# ---- 跨条目门禁 --------------------------------------------------------


def _norm(text: str) -> str:
    return re.sub(r"[\s，。、；：（）()【】\[\]0-9．.,;:]+", "", text or "")


def _trigrams(text: str) -> set[str]:
    t = _norm(text)
    return {t[i:i + 3] for i in range(len(t) - 2)} if len(t) >= 3 else set()


def g06_cross_system_duplicates(bom: Bom, threshold: float = SIMILARITY_THRESHOLD,
                                max_candidates: int = 40) -> list[Finding]:
    """G-06 跨 system 描述高度相似 —— 疑似重复计列。

    重复计列是各地标准的明令禁止项（柳州 三(一)2.7；山东同精神）。
    用字符 3-gram Jaccard + 倒排索引控制候选集，避免 O(n²) 全比对。
    """
    items = [i for i in bom.active() if is_fp_counted(i) and i.description]
    grams = {i.id: _trigrams(i.description) for i in items}
    by_id = {i.id: i for i in items}

    inverted: dict[str, list[str]] = defaultdict(list)
    for iid, gs in grams.items():
        for g in gs:
            inverted[g].append(iid)
    # 丢弃过于常见的 gram（出现在 >5% 条目中），它们不具区分度
    common = {g for g, ids in inverted.items() if len(ids) > max(20, len(items) * 0.05)}

    out: list[Finding] = []
    reported: set[tuple[str, str]] = set()
    for iid, gs in grams.items():
        if not gs:
            continue
        cand = Counter()
        for g in gs - common:
            for other in inverted[g]:
                if other != iid and by_id[other].path.system != by_id[iid].path.system:
                    cand[other] += 1
        for other, _ in cand.most_common(max_candidates):
            key = tuple(sorted((iid, other)))
            if key in reported:
                continue
            a, b = grams[iid], grams[other]
            union = len(a | b)
            if not union:
                continue
            jac = len(a & b) / union
            if jac >= threshold:
                reported.add(key)
                out.append(Finding("G-06", "warn", "released", "item", iid,
                                   f"与 {other} 描述相似度 {jac:.0%}（跨 system），疑似重复计列",
                                   {"other": other,
                                    "system_a": by_id[iid].path.system,
                                    "system_b": by_id[other].path.system,
                                    "text": by_id[iid].description[:80]}))
    return out


def g10_fp_drift(bom: Bom, previous: Bom | None, has_changelog: bool = False) -> list[Finding]:
    """G-10 相对上一 released 版本的 FP 总量漂移 —— 大幅变动必须有变更说明。"""
    if previous is None:
        return []
    from nesma_weights import ufp_total  # 延迟导入，避免循环
    old, new = ufp_total(previous), ufp_total(bom)
    if old == 0:
        return []
    drift = abs(new - old) / old
    if drift > FP_DRIFT_THRESHOLD and not has_changelog:
        return [Finding("G-10", "warn", "released", "bom", "-",
                        f"UFP 从 {old} 变为 {new}（{drift:+.1%}），超过 "
                        f"{FP_DRIFT_THRESHOLD:.0%} 且无 CHANGELOG 说明",
                        {"old": old, "new": new})]
    return []


# ---- 编排 --------------------------------------------------------------

GATES: list[Callable[[Bom], list[Finding]]] = [
    g01_identity, g02_nesma_type, g03_type_distribution, g04_missing_ilf,
    g05_description, g06_cross_system_duplicates, g07_class_consistency,
    g08_maturity_evidence, g09_gpu_dependency, g11_name_uniqueness,
    g12_placeholders, g13_purchase_subject, g14_vocabulary,
    g15_shared_logical_file,
]


def run(bom: Bom, previous: Bom | None = None, has_changelog: bool = False) -> GateReport:
    findings: list[Finding] = []
    for gate in GATES:
        findings.extend(gate(bom))
    findings.extend(g10_fp_drift(bom, previous, has_changelog))

    types = Counter(i.nesma.type for i in bom.active()
                    if is_fp_counted(i))
    stats = {
        "items_total": len(bom.items),
        "items_active": len(bom.active()),
        "by_class": dict(Counter(i.cls for i in bom.active())),
        "by_status": dict(Counter(i.status for i in bom.active())),
        "by_maturity": dict(Counter(i.maturity for i in bom.active())),
        "by_nesma_type": dict(types),
        "systems": len(bom.by_system()),
    }
    return GateReport(findings, stats)


def format_report(report: GateReport, bom_version: str) -> str:
    """人读的门禁报告（markdown）。"""
    lines = [f"# BOM 质量门禁报告 — v{bom_version}", ""]
    s = report.stats
    lines += [
        f"- 条目：{s['items_active']} 有效 / {s['items_total']} 总计，"
        f"覆盖 {s['systems']} 个系统",
        f"- 类别分布：{s['by_class']}",
        f"- 功能点类型分布：{s['by_nesma_type']}",
        f"- 成熟度分布：{s['by_maturity']}",
        f"- **不修任何问题，当前最高可晋升到：`{report.highest_reachable_status()}`**",
        "",
    ]

    for status in ("draft", "reviewed", "released"):
        blocking = [f for f in report.findings if f.blocks == status]
        if not blocking:
            continue
        lines += [f"## 阻断晋升到 `{status}`（{len(blocking)} 条）", ""]
        by_gate: dict[str, list[Finding]] = defaultdict(list)
        for f in blocking:
            by_gate[f.gate].append(f)
        for gate in sorted(by_gate):
            fs = by_gate[gate]
            lines.append(f"### {gate} — {len(fs)} 条（{fs[0].severity}）")
            for f in fs[:12]:
                lines.append(f"- `{f.target}` {f.message}")
            if len(fs) > 12:
                lines.append(f"- …其余 {len(fs) - 12} 条")
            lines.append("")
    return "\n".join(lines)
