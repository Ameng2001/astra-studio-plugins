"""quote_selfreview — 对自己生成的报价做财评预审（红队预演）。

与 quote-review 的区别：那个评审**外部**报价，这个评审**我们自己刚生成的**。
立场是替财评专家提问，然后检查我方能不能答上来。

每条发现三段式：
  reviewer_question  财评专家会怎么问 —— 用他们的语气和关注点
  our_answer         我方准备的回答 —— 答不上来的直接标 ⚠️，那才是要补的工作
  citation           条款锚点，按 pack_id 动态取，不硬编码任何地方标准

**这是预审，不是监管审计。** 它模拟外部评审可能的挑战，本身不构成合规结论。

用法：
    python3 quote_selfreview.py --deal <deal_dir> --bom <dir> --pack <dir> \
        [--modes <yaml> --delivery-plan <yaml>] [--gate-report <json>]
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

SEVERITY_WEIGHT = {"fail": 12, "warn": 4, "info": 0}


@dataclass
class Finding:
    id: str
    category: str
    severity: str                   # fail | warn | info
    title: str
    reviewer_question: str
    our_answer: str
    answerable: bool                # False = 现在答不上来，送审前必须补
    citation: str = ""
    amount_at_risk: float = 0.0
    action: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


class SelfReview:
    def __init__(self, deal: Path, bom_dir: Path, pack_dir: Path,
                 modes: Path | None = None, plan: Path | None = None) -> None:
        from bom_schema import Bom
        from standard_pack import StandardPack

        self.deal_dir = deal
        self.bom = Bom.load(bom_dir)
        self.pack = StandardPack.load(pack_dir)
        self.result = json.loads(
            (deal / "out" / "costing-result.json").read_text(encoding="utf-8"))
        self.modes_path, self.plan_path = modes, plan
        self.findings: list[Finding] = []

    def add(self, **kw: Any) -> None:
        self.findings.append(Finding(**kw))

    # ---- 检查族 ----

    def c1_counting_quality(self) -> None:
        """计数规范 —— 财评最先问的就是「功能点怎么数出来的」。"""
        import nesma_rules

        report = nesma_rules.run(self.bom)
        by_gate: dict[str, int] = {}
        for f in report.findings:
            by_gate[f.gate] = by_gate.get(f.gate, 0) + 1

        status = report.highest_reachable_status()
        if status != "released":
            self.add(
                id="C1-01", category="计数规范", severity="fail",
                title=f"BOM 处于 {self.bom.version}，最高可晋升 `{status}`，未达 released",
                reviewer_question="你们的功能点清单经过复核了吗？谁数的、谁复核的？",
                our_answer=(f"⚠️ 当前 BOM 为 {status} 状态。缺 rationale "
                            f"{by_gate.get('G-02b', 0)} 条、缺双人复核 "
                            f"{by_gate.get('G-02c', 0)} 条。"),
                answerable=False,
                action="补齐类型判定理由与双人复核，BOM 升到 released 后再送审",
                detail={"gates": by_gate})

        if by_gate.get("G-04"):
            self.add(
                id="C1-02", category="计数规范", severity="fail",
                title=f"{by_gate['G-04']} 个系统零 ILF",
                reviewer_question=(
                    "外部输入的定义是对内部逻辑文件执行新增、更改或删除。"
                    "你们报了大量 EI，内部逻辑文件在哪里？"),
                our_answer="⚠️ 暂无法回答 —— 这些系统确实未计 ILF，属漏计。",
                answerable=False,
                citation="表8.1 p.19 / 表6.2 p.17",
                action="按 C-entities.yaml 补齐逻辑文件实体（方向为增，不是减）")

        if by_gate.get("G-03"):
            self.add(
                id="C1-03", category="计数规范", severity="fail",
                title=f"{by_gate['G-03']} 个系统单一功能点类型占比超 80%",
                reviewer_question="这个系统全部是同一种计数项，是逐条识别的还是批量打标的？",
                our_answer="⚠️ 部分系统确为批量打标，需按 NESMA 逐条重新识别。",
                answerable=False,
                citation="表6-表10 识别规则",
                action="按 nesma-rules.md 重拆")

        if by_gate.get("G-06"):
            self.add(
                id="C1-04", category="重复计列", severity="warn",
                title=f"{by_gate['G-06']} 对条目跨系统描述高度相似",
                reviewer_question="这两个系统里的功能描述几乎一样，是不是同一个东西报了两次？",
                our_answer=("需逐对裁决：真重复的删一条；同名不同边界的补 rationale "
                            "说明差异。"),
                answerable=False,
                citation="重复计列禁令（各地标准通例）",
                action="逐对裁决 G-06 清单")

    def c2_method(self) -> None:
        """方法论 —— 功能点是正向数出来的，还是从人天反推的。"""
        from bom_schema import FP_COUNTED_CLASSES

        legacy = [i for i in self.bom.active()
                  if i.legacy_quote.get("person_days") is not None]
        if legacy:
            self.add(
                id="C2-01", category="方法论", severity="info",
                title=f"{len(legacy)} 条条目保留了历史人天报价（仅作交叉校验）",
                reviewer_question="你们是先有价再凑功能点，还是先数功能点再算价？",
                our_answer=(
                    "先数功能点。BOM 中的 `legacy_quote` 是历史人天报价，"
                    "明确标注「仅供交叉校验，不得用于定价」；本次金额由功能点法"
                    "正向推导，公式与参数出处见编制说明。"),
                answerable=True,
                citation=f"{self.pack.data['standard_doc']} 功能点法")

        fp_items = [i for i in self.bom.active()
                    if i.cls in FP_COUNTED_CLASSES and i.nesma]
        with_reason = [i for i in fp_items if (i.nesma.rationale or "").strip()]
        self.add(
            id="C2-02", category="方法论", severity="info" if
            len(with_reason) == len(fp_items) else "warn",
            title=f"类型判定理由覆盖 {len(with_reason)}/{len(fp_items)}",
            reviewer_question="这一条为什么判为 EO 不是 EQ？依据是什么？",
            our_answer=(
                f"已填 rationale 的 {len(with_reason)} 条可逐条引用识别规则条款作答；"
                f"其余 {len(fp_items) - len(with_reason)} 条 ⚠️ 暂无法作答。"),
            answerable=len(with_reason) == len(fp_items),
            citation="表9.3 p.20 / 表10.5 p.22（EO 与 EQ 的判别标准）",
            action="补齐 rationale")

    def c3_scope(self) -> None:
        """范围完整性 —— 漏项和待核价。"""
        excluded = self.result["scope"]["excluded"]
        if excluded.get("占位未确认"):
            n = excluded["占位未确认"]
            self.add(
                id="C3-01", category="范围", severity="warn",
                title=f"{n} 条占位条目未计入报价",
                reviewer_question="这部分功能到底做不做？不做为什么列在方案里？",
                our_answer=(
                    f"这 {n} 条对应「每个模型/智能体是否有独立的规则配置与结果查询"
                    f"入口」这一未决问题，尚未确认，故**未计入金额**。"
                    f"确认后如需增加将另行说明。列而不计，不虚报。"),
                answerable=True,
                action="飞书共创确认后重新测算")

        pending = self.result.get("pending_pricing") or []
        if pending:
            self.add(
                id="C3-02", category="范围", severity="fail",
                title=f"{len(pending)} 项走许可/买断但未核价",
                reviewer_question="这几项的许可费是多少？没有价格怎么评审？",
                our_answer="⚠️ 许可价待商务核价，本次未计入金额 —— 报价不完整。",
                answerable=False,
                action="补商务核价后重出清单",
                detail={"items": pending})

        # 本区域不覆盖的科目
        ns = self.pack.data.get("delivery_mode_support", {})
        oos = {k: v for k, v in ns.items()
               if v.get("status") in ("not_in_scope", "forbidden")}
        if oos:
            self.add(
                id="C3-03", category="范围", severity="info",
                title=f"{len(oos)} 类交付形态在本标准中无科目或属负面清单",
                reviewer_question="运维怎么办？后续费用是不是还要再报一次？",
                our_answer=(
                    "本标准仅覆盖建设期。运维与租赁在本标准无对应科目，"
                    "已在编制说明中显式列出并说明需另行立项申报 —— "
                    "**列而不计**，而非遗漏。"),
                answerable=True,
                citation=f"{self.pack.pack_id} delivery_mode_support",
                detail={"modes": list(oos)})

    def c4_delivery_rules(self) -> None:
        """交付方案一致性 —— R-1..R-8。"""
        if not (self.modes_path and self.plan_path):
            return
        from bom_schema import Bom
        from costing_engine import CostingEngine, DealConfig
        from delivery_matrix import DeliveryPlan

        deal = DealConfig(deal_id="selfreview")
        items, _ = CostingEngine(self.bom, self.pack, deal).in_scope()
        dp = DeliveryPlan.load(self.modes_path, self.plan_path)
        for v in dp.check(items, dp.assign(items), self.pack):
            rule = dp.rules.get(v.rule, {})
            self.add(
                id=f"C4-{v.rule}", category="交付一致性",
                severity=v.severity, title=v.message[:60],
                reviewer_question=rule.get("desc", v.message),
                our_answer=("⚠️ 该项不成立，需修正交付方案。" if v.severity == "fail"
                            else "已知项，可作说明。"),
                answerable=v.severity != "fail",
                citation=rule.get("basis", ""),
                action="修正交付方案或补举证",
                detail={"targets": v.targets})

    def c5_evidence(self) -> None:
        """举证完备性 —— 询价单、质保、数据模型人月举证。"""
        ev = self.pack.data.get("procurement_evidence", {})
        hw = self.result.get("hardware", {})
        n = hw.get("quotes_required_count", 0)
        if n:
            self.add(
                id="C5-01", category="举证", severity="warn",
                title=f"{n} 项硬件达到询价举证门槛",
                reviewer_question="这些设备的三家盖章询价单呢？",
                our_answer=f"⚠️ 询价单待补。{hw.get('quotes_required_rule', '')}",
                answerable=False,
                citation=(f"p.{ev.get('hardware', {}).get('citation', {}).get('page', '')} "
                          f"{ev.get('hardware', {}).get('citation', {}).get('section', '')}"),
                action="按标准要求补三家不同品牌厂商盖章询价单")

        # 数据模型订阅走购置科目时的人月举证
        dm = ev.get("data_model")
        modes_used = (self.result.get("delivery") or {}).get("modes_used", [])
        if dm and "D5" in modes_used:
            self.add(
                id="C5-02", category="举证", severity="warn",
                title="模型订阅落「数据资源和服务购置费」科目，需人月工作量举证",
                reviewer_question="这个模型订阅价是怎么定的？依据在哪？",
                our_answer=(
                    "本标准明确要求数据模型询价单包含"
                    f"「{'、'.join(dm.get('required_fields', []))}」。"
                    "⚠️ 需按此格式准备询价材料 —— 订阅定价的人月工作量口径"
                    "与该要求天然契合。"),
                answerable=False,
                citation=(f"p.{dm.get('citation', {}).get('page', '')} "
                          f"{dm.get('citation', {}).get('section', '')}"),
                action="按 required_fields 准备数据模型询价材料")

        w = ev.get("warranty", {})
        if w:
            self.add(
                id="C5-03", category="举证", severity="info",
                title="质保与运维包含期要求",
                reviewer_question="硬件质保几年？软件购置费含不含运维？",
                our_answer=(
                    f"硬件须含 ≥{w.get('hardware_min_years')} 年原厂质保；"
                    f"期限授权软件购置费须含授权期内运维。清单中已按此口径编制。"),
                answerable=True,
                citation=(f"p.{w.get('citation', {}).get('page', '')} "
                          f"{w.get('citation', {}).get('section', '')}"))

    def c6_negative_list(self) -> None:
        """负面清单扫描。"""
        hits: list[dict[str, Any]] = []
        for r in self.result.get("hardware", {}).get("rows", []):
            for n in self.pack.check_negative_list(r["name"]):
                hits.append({"item": r["name"], "rule": n["item"]})
        if hits:
            self.add(
                id="C6-01", category="负面清单", severity="fail",
                title=f"{len(hits)} 项疑似命中负面清单",
                reviewer_question="这些内容按标准不得列入建设项目预算，为什么在清单里？",
                our_answer="⚠️ 需逐项核实并移出建设预算。",
                answerable=False,
                citation=f"{self.pack.pack_id} negative_list",
                detail={"hits": hits[:10]})
        else:
            self.add(
                id="C6-02", category="负面清单", severity="info",
                title="负面清单扫描无命中",
                reviewer_question="有没有把政务云资源、设备租赁、通用办公设备混进来？",
                our_answer=(
                    f"已按 {self.pack.pack_id} 的负面清单"
                    f"（{len(self.pack.data.get('negative_list', []))} 条）扫描，无命中。"),
                answerable=True)

    def c7_fee_eligibility(self) -> None:
        """费用资格门槛 —— 未计列的费用要说明原因，不能静默省略。"""
        for f in self.result.get("other_fees", []):
            if f.get("blocked_reason"):
                self.add(
                    id=f"C7-{f['id']}", category="费用科目", severity="info",
                    title=f"{f['name']} 未计列",
                    reviewer_question=f"{f['name']}为什么没报？",
                    our_answer=f["blocked_reason"],
                    answerable=True,
                    citation=(f"p.{(f.get('citation') or {}).get('page', '')} "
                              f"{(f.get('citation') or {}).get('section', '')}"))

    # ---- 编排 ----

    def run(self) -> dict[str, Any]:
        for fn in (self.c1_counting_quality, self.c2_method, self.c3_scope,
                   self.c4_delivery_rules, self.c5_evidence,
                   self.c6_negative_list, self.c7_fee_eligibility):
            fn()

        penalty = sum(SEVERITY_WEIGHT[f.severity] for f in self.findings)
        score = max(0, 100 - penalty)
        unanswerable = [f for f in self.findings if not f.answerable]
        return {
            "deal": self.result["deal"],
            "bom_version": self.result["bom_version"],
            "pack_id": self.pack.pack_id,
            "score": score,
            "counts": {s: sum(1 for f in self.findings if f.severity == s)
                       for s in ("fail", "warn", "info")},
            "unanswerable": len(unanswerable),
            "findings": [asdict(f) for f in self.findings],
        }


def render(result: dict[str, Any], pack_doc: str) -> str:
    c = result["counts"]
    L = [f"# 财评自审报告 — {result['deal']}", "",
         f"编制日期：{date.today().isoformat()}　|　BOM {result['bom_version']}　|　"
         f"标准包 {result['pack_id']}", "",
         "> **这是预审，不是监管审计。** 它模拟外部财评专家可能提出的挑战，"
         "检查我方能否作答，本身不构成合规结论。", "",
         "## 总评", "",
         f"- 自审得分：**{result['score']}/100**",
         f"- 否决 {c['fail']} 项 / 警告 {c['warn']} 项 / 说明 {c['info']} 项",
         f"- **现在答不上来的：{result['unanswerable']} 项** —— 这是送审前必须补的工作",
         f"- 评审基准：{pack_doc}", ""]

    if result["unanswerable"]:
        L += ["## ⚠️ 送审前必须补齐", "",
              "以下问题一旦被问到，目前无法作答：", ""]
        for f in result["findings"]:
            if not f["answerable"]:
                L.append(f"- **{f['title']}** — {f['action']}")
        L.append("")

    by_cat: dict[str, list[dict]] = {}
    for f in result["findings"]:
        by_cat.setdefault(f["category"], []).append(f)

    L += ["## 逐项预演", ""]
    for cat, fs in by_cat.items():
        L += [f"### {cat}", ""]
        for f in fs:
            mark = {"fail": "❌", "warn": "⚠️", "info": "ℹ️"}[f["severity"]]
            L += [f"#### {mark} {f['title']}", "",
                  f"**评审会问**：{f['reviewer_question']}", "",
                  f"**我方回答**：{f['our_answer']}", ""]
            if f["citation"]:
                L.append(f"*条款依据*：{f['citation']}")
                L.append("")
            if f["action"]:
                L.append(f"*待办*：{f['action']}")
                L.append("")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description="报价财评自审")
    ap.add_argument("--deal", required=True, type=Path)
    ap.add_argument("--bom", required=True, type=Path)
    ap.add_argument("--pack", required=True, type=Path)
    ap.add_argument("--modes", type=Path)
    ap.add_argument("--delivery-plan", type=Path)
    args = ap.parse_args()

    sr = SelfReview(args.deal, args.bom, args.pack, args.modes, args.delivery_plan)
    result = sr.run()
    out = args.deal / "out"
    (out / "06-财评自审报告.md").write_text(
        render(result, sr.pack.data["standard_doc"]), encoding="utf-8")
    (out / "review-findings.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    c = result["counts"]
    print(f"自审得分 {result['score']}/100　"
          f"否决 {c['fail']} / 警告 {c['warn']} / 说明 {c['info']}")
    print(f"**现在答不上来的 {result['unanswerable']} 项** —— 送审前必须补")
    for f in result["findings"]:
        if not f["answerable"]:
            print(f"  · {f['title']}")


if __name__ == "__main__":
    main()
