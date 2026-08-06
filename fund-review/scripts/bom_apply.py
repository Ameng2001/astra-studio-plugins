"""bom_apply — 把 bom_p2_proposal 的调整项落实到 BOM，产出新版本。

原则：
  · **条目永不物理删除** —— 只置 status=deprecated + deprecated_in，
    保证任何历史报价都能凭 deal.lock.json 精确重算
  · **合并不丢信息** —— 被折叠行的描述并入保留行，而非丢弃
  · **每次落实都填 rationale** —— 内容是触发的标准条款，可追溯

目前实现调整项 A（数据功能折叠）。B–E 需人工确认实体边界后再落实。

用法：
    python3 bom_apply.py --bom <dir> --adjustment A --new-version 0.10.0 [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

from bom_schema import Bom, BomItem, Nesma, Path_, Runtime
from nesma_weights import ESTIMATED_WEIGHTS as W

#: 成熟度保守序 —— 合并后取组内最不成熟者（该逻辑文件整体仍需建设）
MATURITY_ORDER = {"new": 0, "partial": 1, "existing": 2}

COLLAPSE_CITATION = "表6.4 p.17 / 表7.3 p.18"
COLLAPSE_RULE = ("已被识别出的内部/外部逻辑文件可能存在多种不同的用户视图、"
                 "访问路径、文件索引，但该逻辑文件只能计为一项")


def _clean(desc: str) -> str:
    """去掉机械切分留下的「支持」前缀与句末句号，便于拼接。"""
    d = (desc or "").strip()
    d = re.sub(r"^支持", "", d)
    return d.rstrip("。．;；").strip()


def collapse_data_functions(bom: Bom, new_version: str) -> tuple[Bom, dict[str, Any]]:
    """调整项 A：同一逻辑文件的多行折叠为一条。"""
    groups: dict[tuple, list[BomItem]] = defaultdict(list)
    for it in bom.items:
        if it.status != "deprecated" and it.nesma and it.nesma.type in ("ILF", "ELF"):
            groups[(it.path.system, it.path.l2, it.path.l3, it.nesma.type)].append(it)

    log: list[dict[str, Any]] = []
    delta = 0

    for (system, l2, l3, ftype), members in groups.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda i: i.id)
        keeper, dropped = members[0], members[1:]

        # 描述合并 —— 被折叠行写的是同一文件的字段/接入方式/脱敏规则，属补充信息
        parts, seen = [], set()
        for m in members:
            c = _clean(m.description)
            if c and c not in seen:
                seen.add(c)
                parts.append(c)
        if parts:
            keeper.description = "；".join(parts) + "。"

        # 名称用实体名 —— 折叠后该条目代表的就是这个逻辑文件
        if l3:
            keeper.name = str(l3)

        # 成熟度取组内最不成熟者
        worst = min(members, key=lambda i: MATURITY_ORDER.get(i.maturity, 0))
        if worst.maturity != keeper.maturity:
            keeper.maturity = worst.maturity
            keeper.maturity_evidence = (worst.maturity_evidence or keeper.maturity_evidence)
        if keeper.maturity_evidence:
            keeper.maturity_evidence += f"（折叠自 {len(members)} 行，取最不成熟者）"

        keeper.nesma.rationale = (
            f"判为 {ftype}：{COLLAPSE_RULE}（{COLLAPSE_CITATION}）。"
            f"本条已合并同一逻辑文件的 {len(members)} 行"
            f"（原分列其数据内容、接入方式、字段与脱敏规则），合并前 id："
            f"{', '.join(m.id for m in members)}"
        )
        keeper.nesma.counted_by = f"{keeper.nesma.counted_by or ''}; collapse:A@{new_version}"

        # 运营期证据合并
        ev = sorted({e for m in members for e in m.runtime.evidence})
        keeper.runtime.evidence = ev
        keeper.runtime.needs_inference_gpu = any(m.runtime.needs_inference_gpu for m in members)
        keeper.runtime.calls_external_llm = any(m.runtime.calls_external_llm for m in members)

        for d in dropped:
            d.status = "deprecated"
            d.deprecated_in = new_version

        d_ufp = -len(dropped) * W[ftype]
        delta += d_ufp
        log.append({
            "system": system, "entity": f"{l2} / {l3}", "type": ftype,
            "collapsed": len(members), "keeper": keeper.id,
            "deprecated": [d.id for d in dropped], "delta_ufp": d_ufp,
        })

    # 注意：不改任何条目的 since —— 它记录首次引入版本，与折叠无关
    bom.version = new_version
    return bom, {"adjustment": "A", "citation": COLLAPSE_CITATION,
                 "groups": len(log), "delta_ufp": delta, "detail": log}


#: 子句切分 —— 只在分句标点处切。不按逗号/顿号切：
#: 「新增、死亡及迁出数量」里的「新增」是统计口径不是动作，按逗号切会造出假功能点。
CLAUSE_SPLIT = re.compile(r"[；;。\n]")
MIN_CLAUSE_LEN = 4

SPLIT_CITATION = "表8.6 p.19 / 表9.6 p.20"
SPLIT_RULE = ("在输入屏幕或窗口上实现的（新增、更改或删除）不同功能计为不同的外部输入；"
              "一个输出产品包含可被单独检索的不同逻辑布局时计为多个外部输出")


def _lead_action(text: str) -> str | None:
    """取子句中最先出现的动作词，用于给拆出的条目命名（如「病程记录-导出」）。"""
    import nesma_classify as nc

    hits = [(text.index(k), k) for rule in nc.RULES for k in rule.keywords if k in text]
    return min(hits)[1] if hits else None


def split_multi_process(bom: Bom, new_version: str) -> tuple[Bom, dict[str, Any]]:
    """调整项 F：一行含多个基本处理的，按**子句实际文本**拆开。

    只拆有文本证据的 —— 描述能按分句标点切出分属不同类型的子句。
    同一子句内命中多类型的不自动拆：样本显示相当比例是名词短语误报
    （「由我发起的流程」的「发起」、「本月度新增数量」的「新增」），
    机械拆分会造出不存在的功能点。那些进人工裁决清单。
    """
    import nesma_classify as nc

    # 每个 id 前缀的当前最大序号，用于分配新 id
    max_seq: dict[str, int] = defaultdict(int)
    for it in bom.items:
        prefix, _, seq = it.id.rpartition(".")
        if seq.isdigit():
            max_seq[prefix] = max(max_seq[prefix], int(seq))

    log: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    delta = 0
    new_items: list[BomItem] = []

    for it in list(bom.active()):
        if not it.nesma:
            continue
        verdict = nc.classify(f"{it.name} {it.description}")
        if not verdict.competing:
            continue

        clauses = [c.strip() for c in CLAUSE_SPLIT.split(it.description or "")
                   if len(c.strip()) >= MIN_CLAUSE_LEN]
        by_type: dict[str, list[str]] = defaultdict(list)
        for c in clauses:
            cv = nc.classify(c)
            if cv.type:
                by_type[cv.type].append(c)

        if len(by_type) <= 1:
            pending.append({
                "id": it.id, "system": it.path.system, "name": it.name,
                "imported_type": it.nesma.type,
                "competing": [t for t, _ in verdict.competing],
                "evidence": {t: h for t, h in verdict.competing},
                "description": it.description,
                "reason": "竞争类型在同一子句内，无文本依据可拆 —— 需人工判定是否真为多个基本处理",
            })
            continue

        original_full = it.description
        keep_type = it.nesma.type if it.nesma.type in by_type else verdict.type
        prefix = it.id.rpartition(".")[0]

        for ftype, texts in sorted(by_type.items()):
            text = "；".join(texts) + "。"
            if ftype == keep_type:
                it.description = text
                it.nesma.rationale = (
                    f"判为 {ftype}：{SPLIT_RULE}（{SPLIT_CITATION}）。"
                    f"本行原含 {len(by_type)} 个基本处理，已按子句拆分；"
                    f"原描述：{original_full}"
                )
                it.nesma.counted_by = f"{it.nesma.counted_by or ''}; split:F@{new_version}"
                continue
            max_seq[prefix] += 1
            new_id = f"{prefix}.{max_seq[prefix]:04d}"
            # 拆出的条目必须自带主语 —— 「支持导出。」脱离原行无法判定导出什么，
            # 既撑不住评审也会直接踩 G-05（描述不可判定）。
            action = _lead_action(text) or ftype
            new_items.append(BomItem(
                id=new_id, cls=it.cls, name=f"{it.name}-{action}",
                path=Path_(**{k: v for k, v in asdict(it.path).items()}),
                description=f"{it.name}：{text}",
                nesma=Nesma(type=ftype,
                            rationale=(f"判为 {ftype}：{SPLIT_RULE}（{SPLIT_CITATION}）。"
                                       f"自 {it.id} 拆出 —— 该行原含 {len(by_type)} 个基本处理；"
                                       f"原描述：{original_full}"),
                            counted_by=f"split:F@{new_version} from {it.id}"),
                app_type=it.app_type, dev_category=it.dev_category,
                maturity=it.maturity, maturity_evidence=it.maturity_evidence,
                runtime=Runtime(**asdict(it.runtime)),
                since=new_version, status="draft", source=it.source,
                tags=list(it.tags),
            ))
            delta += W[ftype]

        log.append({"source": it.id, "system": it.path.system,
                    "types": sorted(by_type), "kept": keep_type,
                    "added": [n.id for n in new_items if n.id.startswith(prefix)][-len(by_type) + 1:],
                    "delta_ufp": sum(W[t] for t in by_type if t != keep_type)})

    bom.items.extend(new_items)
    bom.version = new_version
    return bom, {"adjustment": "F", "citation": SPLIT_CITATION,
                 "split": len(log), "added_items": len(new_items),
                 "delta_ufp": delta, "detail": log, "pending_adjudication": pending}


E_CITATION = "表9.15 p.21 / 表9.16 p.21"
PLACEHOLDER_TAG = "placeholder"


def _clone_path(it: BomItem) -> Path_:
    return Path_(**asdict(it.path))


def decompose_ai_assets(bom: Bom, new_version: str,
                        spec_path: Path | None = None) -> tuple[Bom, dict[str, Any]]:
    """调整项 E：AI 资产按**人工策展的**输出清单重拆，并为不确定项生成占位。

    与 A/F 不同，E 无法从描述机械推导 —— 枚举里混着输入、测量维度与知识领域
    （「年龄、性别、疾病史」是输入，「身高、体重、BMI」是测量项，
    「运动学、医学、康复护理」是知识领域）。机械抽取会造出假功能点，
    与源表当初的机械切分是同一个错误。

    因此拆分依据来自人工策展的 `E-decomposition.yaml`，每条附 source_text 原文。
    无法判定的（每个模型是否有独立配置/查询入口）生成占位条目，
    标 tag `placeholder`，由门禁 G-12 阻止其进入 reviewed。
    """
    import yaml

    spec = yaml.safe_load(Path(spec_path).read_text(encoding="utf-8"))
    by_id = {i.id: i for i in bom.active()}
    max_seq: dict[str, int] = defaultdict(int)
    for it in bom.items:
        prefix, _, seq = it.id.rpartition(".")
        if seq.isdigit():
            max_seq[prefix] = max(max_seq[prefix], int(seq))

    new_items: list[BomItem] = []
    log: list[dict[str, Any]] = []
    delta = 0

    # ---- 一、有文本依据的输出拆分 ----
    for entry in spec.get("evidenced", []):
        src = by_id.get(entry["id"])
        if src is None:
            log.append({"id": entry["id"], "status": "SKIPPED - 条目不存在"})
            continue
        outputs = entry["outputs"]
        prefix = src.id.rpartition(".")[0]

        # 首个输出留在原条目，其余各建一条
        src.nesma.rationale = (
            f"判为 EO：每个可独立运行且包含不同处理过程的功能计为一个外部输出"
            f"（{E_CITATION}）。本模型描述列举了 {len(outputs)} 个不同输出，"
            f"已拆分；本条对应「{outputs[0]}」。原文依据：{entry['source_text']}"
        )
        src.name = f"{entry['name']}-{outputs[0]}"
        src.nesma.counted_by = f"{src.nesma.counted_by or ''}; split:E@{new_version}"

        for out in outputs[1:]:
            max_seq[prefix] += 1
            new_items.append(BomItem(
                id=f"{prefix}.{max_seq[prefix]:04d}", cls=src.cls,
                name=f"{entry['name']}-{out}",
                path=_clone_path(src),
                description=f"{entry['name']}：{out}。",
                nesma=Nesma(type="EO",
                            rationale=(f"判为 EO：每个可独立运行且包含不同处理过程的功能"
                                       f"计为一个外部输出（{E_CITATION}）。"
                                       f"自 {src.id} 拆出，对应「{out}」。"
                                       f"原文依据：{entry['source_text']}"),
                            counted_by=f"split:E@{new_version} from {src.id}"),
                app_type=src.app_type, dev_category=src.dev_category,
                maturity=src.maturity, maturity_evidence=src.maturity_evidence,
                runtime=Runtime(**asdict(src.runtime)),
                since=new_version, status="draft", source=src.source,
                tags=list(src.tags),
            ))
            delta += W["EO"]
        log.append({"id": src.id, "outputs": outputs, "extra_eo": len(outputs) - 1})

    # ---- 二、占位条目 ----
    ph = spec.get("placeholders", {})
    ph_count = 0
    if ph:
        scope = set(ph.get("scope_systems", []))
        # 按实体（l3 或 l2）去重 —— 同一实体多行只生成一组占位
        entities: dict[tuple[str, str], BomItem] = {}
        for it in bom.active():
            if it.path.system in scope and it.nesma:
                key = (it.path.system, str(it.path.l3 or it.path.l2))
                entities.setdefault(key, it)

        for (system, ent_name), sample in sorted(entities.items()):
            prefix = sample.id.rpartition(".")[0]
            for gen in ph["generate"]:
                max_seq[prefix] += 1
                new_items.append(BomItem(
                    id=f"{prefix}.{max_seq[prefix]:04d}", cls=sample.cls,
                    name=f"{ent_name}{gen['name_suffix']}",
                    path=_clone_path(sample),
                    description=gen["description_template"].format(name=ent_name),
                    nesma=Nesma(type=gen["type"],
                                rationale=("【占位，待飞书共创确认】"
                                           + ph["question"].strip().replace("\n", " ")),
                                counted_by=f"placeholder:E@{new_version}"),
                    app_type=sample.app_type, dev_category=sample.dev_category,
                    maturity=sample.maturity,
                    maturity_evidence=sample.maturity_evidence,
                    since=new_version, status="draft",
                    source=sample.source,
                    tags=sorted({*sample.tags, ph.get("tag", PLACEHOLDER_TAG)}),
                ))
                delta += W[gen["type"]]
                ph_count += 1

    bom.items.extend(new_items)
    bom.version = new_version
    return bom, {"adjustment": "E", "citation": E_CITATION,
                 "evidenced_split": len(log), "placeholders": ph_count,
                 "added_items": len(new_items), "delta_ufp": delta, "detail": log}


ADJUSTMENTS = {"A": collapse_data_functions, "F": split_multi_process,
               "E": decompose_ai_assets}


def main() -> None:
    ap = argparse.ArgumentParser(description="落实 BOM 调整项")
    ap.add_argument("--bom", required=True, type=Path)
    ap.add_argument("--adjustment", required=True, choices=sorted(ADJUSTMENTS))
    ap.add_argument("--new-version", required=True)
    ap.add_argument("--spec", type=Path,
                    help="策展文件 —— **仅 E / ADJ 用**（E-decomposition.yaml / "
                         "裁决 csv）。传给 A / F 会被拒绝，不是被忽略")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    bom = Bom.load(args.bom)
    before = sum(W[i.nesma.type] for i in bom.active() if i.nesma)
    before_n = len(bom.active())

    # `--spec` 只有 E / ADJ 消费。此前传给 F 会被 main 的三元表达式**静默丢弃** ——
    # 命令照跑、结果与不传 spec 完全一致，于是「按裁决结果落库」实际是
    # 「按规则重跑」，而输出看不出差别。同一个失败形状在本仓库出现过太多次：
    # 不报错，只是做了另一件事。
    SPEC_REQUIRED = {"E", "ADJ"}
    if args.adjustment in SPEC_REQUIRED:
        if not args.spec:
            ap.error(f"调整项 {args.adjustment} 需要 --spec（策展文件）")
        if not args.spec.exists():
            ap.error(f"--spec 指向的文件不存在：{args.spec}")
    elif args.spec:
        ap.error(f"调整项 {args.adjustment} 不使用 --spec —— "
                 f"它按规则重跑，不读策展文件。传了会让人以为落的是裁决结果。"
                 f"需要 --spec 的是：{sorted(SPEC_REQUIRED)}")

    fn = ADJUSTMENTS[args.adjustment]
    bom, report = (fn(bom, args.new_version, args.spec)
                   if args.adjustment in ("E", "ADJ") else fn(bom, args.new_version))
    bom.validate()

    after = sum(W[i.nesma.type] for i in bom.active() if i.nesma)
    after_n = len(bom.active())
    report["ufp_before"], report["ufp_after"] = before, after
    report["active_before"], report["active_after"] = before_n, after_n

    if "groups" in report:
        print(f"调整项 {args.adjustment} — {report['groups']} 组折叠")
        print(f"  有效条目 {before_n} → {after_n}"
              f"（废弃 {before_n - after_n} 条，未物理删除）")
    elif report.get("adjustment") == "ADJ":
        print(f"共创裁决落实 — 占位删 {report['placeholder_dropped']} / 留 "
              f"{report['placeholder_kept']}，补 ILF {report['ilf_added']}，"
              f"F 拆分 {report['fp_split']} / 改判 {report['fp_retyped']} / "
              f"排除 {report['fp_excluded']}")
        if report.get("fp_retype_pending"):
            print(f"  ⚠ {report['fp_retype_pending']} 条已判定类型有误但目标类型"
                  f"需人工指定（已标 retype-pending）")
        print(f"  有效条目 {before_n} → {after_n}")
    elif "evidenced_split" in report:
        print(f"调整项 {args.adjustment} — {report['evidenced_split']} 条按原文依据拆分，"
              f"生成占位 {report['placeholders']} 条，共新增 {report['added_items']} 条")
        print(f"  有效条目 {before_n} → {after_n}")
    else:
        print(f"调整项 {args.adjustment} — {report['split']} 条拆分，"
              f"新增 {report['added_items']} 条")
        print(f"  有效条目 {before_n} → {after_n}")
        print(f"  待人工裁决 {len(report['pending_adjudication'])} 条"
              f"（竞争类型在同一子句内，无文本依据可拆）")
    print(f"  UFP {before} → {after}（{report['delta_ufp']:+d}）")

    if args.dry_run:
        print("  [dry-run] 未写入")
        return

    bom.save(args.bom)
    (args.bom / f"apply-{args.adjustment}-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  已写入 {args.bom}（VERSION → {args.new_version}）")




# ---- 共创裁决的落实 ----------------------------------------------------

VERB_MAINTAIN = ("新增 新建 创建 添加 录入 修改 编辑 更新 删除 移除 维护 配置 设置 发布 下线 上线 "
                 "置顶 审核 审批 分派 指派 标注 标记 绑定 解绑 提交 发起 办理 撤销 收取 启用 禁用 "
                 "导入 上传 签约 退款 取消 改价 派单 结算 充值 提现 归档 核销 扣减 续约 报名").split()
VERB_RETRIEVE = "查看 查阅 查询 检索 展示 显示 列表 详情 导出 浏览 下载 打印 筛选".split()
VERB_DERIVE = ("统计 汇总 分析 计算 预测 画像 评分 排名 预警 告警 生成 趋势 占比 监控 研判 复盘 "
               "识别 推荐").split()


def _second_type(desc: str, imported: str) -> str:
    """已由人工确认「确实是两个基本处理」后，判定补记的是哪一类。

    难的判断（是不是真的两个）已经人工做完；这里只是按动词族确定第二类，
    误判空间小得多。仍建议抽样复核。
    """
    has_r = any(v in desc for v in VERB_RETRIEVE)
    has_d = any(v in desc for v in VERB_DERIVE)
    if imported == "EI":
        return "EO" if has_d and not has_r else "EQ"
    if imported in ("EQ", "EO"):
        return "EI"
    return "EQ"


def _corrected_type(desc: str, imported: str) -> str:
    """类型判错时的目标类型 —— 按主导动作。"""
    has_m = any(v in desc for v in VERB_MAINTAIN)
    has_d = any(v in desc for v in VERB_DERIVE)
    has_r = any(v in desc for v in VERB_RETRIEVE)
    if not has_m and has_d:
        return "EO"
    if not has_m and has_r:
        return "EQ"
    if has_m:
        return "EI"
    return imported


def apply_adjudications(bom: Bom, new_version: str,
                        spec_path: Path | None = None) -> tuple[Bom, dict[str, Any]]:
    """落实飞书共创的 500 条裁决。

    三条**假设性**判定保持显式可见，不静默落地：
      · 占位中判「独立保留」的 25 条 —— **不摘 placeholder 标记**，
        让门禁 G-12 继续拦截，直到产品侧确认
      · 3.4 六模块归并、智能体平台知识库去重 —— 写进 rationale 供复核
    """
    import csv

    import yaml

    spec = yaml.safe_load(Path(spec_path).read_text(encoding="utf-8"))
    by_id = {i.id: i for i in bom.active()}
    max_seq: dict[str, int] = defaultdict(int)
    for it in bom.items:
        pre, _, seq = it.id.rpartition(".")
        if seq.isdigit():
            max_seq[pre] = max(max_seq[pre], int(seq))

    log: dict[str, Any] = {"placeholder_dropped": 0, "placeholder_kept": 0,
                           "ilf_added": 0, "fp_split": 0, "fp_retyped": 0,
                           "fp_excluded": 0, "fp_retype_pending": 0}
    new_items: list[BomItem] = []
    delta = 0

    # --- G 占位裁决 ---
    for row in spec.get("placeholders", []):
        it = by_id.get(row["id"])
        if it is None:
            continue
        if row["verdict"].startswith("共用"):
            it.status = "deprecated"
            it.deprecated_in = new_version
            delta -= W[it.nesma.type]
            log["placeholder_dropped"] += 1
        else:
            it.nesma.rationale = (
                "【仍为占位，待产品侧确认】暂按各模型独有保留：领域阈值（如营养风险分级）"
                "属模型特有参数，平台服务管理只覆盖部署与调度配置。"
                "若实际共用一套配置界面，本条应删除。")
            log["placeholder_kept"] += 1

    # --- H 补齐 ILF（C 项裁决确认的实体）---
    for e in spec.get("ilf_entities", []):
        code = e["code"]
        max_seq[code] += 1
        new_items.append(BomItem(
            id=f"{code}.{max_seq[code]:04d}", cls="SOFTWARE_FP", name=e["name"],
            path=Path_(product_line=e["product_line"], system=e["system"],
                       l1=e.get("l1"), l2=e.get("l2")),
            description=f"{e['name']}：{e.get('note') or '本系统维护的业务逻辑数据组'}。"
                        f"维护模块：{e.get('modules', '')}",
            nesma=Nesma(type="ILF",
                        rationale=("判为 ILF：系统边界内维护的、用户可识别的业务数据组"
                                   "（表6.1 p.17）。由 " + str(e.get("modules", "")) +
                                   " 执行新增/更改/删除，符合表8.1「EI 通常对内部逻辑文件"
                                   "执行新增、更改或删除」的反向要求。"
                                   "实体边界经飞书共创核对确认。"),
                        counted_by=f"adjudication:C@{new_version}",
                        reviewed_by=e.get("reviewed_by")),
            app_type=e.get("app_type", "业务处理"),
            dev_category=e.get("dev_category", "基于统一平台的软件开发"),
            maturity="new", since=new_version, status="draft",
            source="bom/p2-proposal/C-entities.yaml", tags=["ilf-补齐"]))
        delta += W["ILF"]
        log["ilf_added"] += 1

    # --- I F 项裁决 ---
    for row in spec.get("fp_verdicts", []):
        it = by_id.get(row["id"])
        if it is None or not it.nesma:
            continue
        v, desc = row["verdict"], it.description or ""
        if v == "exclude":
            it.status = "deprecated"
            it.deprecated_in = new_version
            it.nesma.rationale = ("【按标准不纳入计数】" + row.get("note", "") +
                                  "（三.(三)4(1)/(6) p.16、表6.3 p.17）")
            delta -= W[it.nesma.type]
            log["fp_excluded"] += 1
        elif v == "retype":
            new_t = _corrected_type(desc, it.nesma.type)
            if new_t != it.nesma.type:
                delta += W[new_t] - W[it.nesma.type]
                it.nesma.rationale = (
                    f"判为 {new_t}：原导入类型 {it.nesma.type} 与描述的主导动作矛盾，"
                    f"经逐条阅读改判（表8.1 p.19 / 表9.3 p.20 / 表10.5 p.22）。")
                it.nesma.type = new_t
                it.nesma.counted_by = f"{it.nesma.counted_by or ''}; retype@{new_version}"
                log["fp_retyped"] += 1
            else:
                # 人工判定「类型错了」，但按动词族推不出目标类型 —— 推导失败。
                # 不能静默保持错误类型，标出来让门禁和人看见。
                it.nesma.rationale = (
                    f"⚠️【类型待人工指定】逐条阅读判定当前类型 {it.nesma.type} 有误，"
                    f"但无法由动词族机械推出目标类型 —— 需人工指定。"
                    f"原描述：{desc}")
                it.tags = sorted(set(it.tags) | {"retype-pending"})
                log["fp_retype_pending"] = log.get("fp_retype_pending", 0) + 1
        elif v == "split":
            t2 = _second_type(desc, it.nesma.type)
            pre = it.id.rpartition(".")[0]
            max_seq[pre] += 1
            it.nesma.rationale = (
                f"判为 {it.nesma.type}：本行原含两个基本处理，经逐条阅读确认后拆分"
                f"（表8.6 p.19）。拆出 {t2} 部分见 {pre}.{max_seq[pre]:04d}。"
                f"原描述：{desc}")
            new_items.append(BomItem(
                id=f"{pre}.{max_seq[pre]:04d}", cls=it.cls,
                name=f"{it.name}-{ {'EQ': '查询', 'EO': '统计输出', 'EI': '维护'}[t2] }",
                path=Path_(**asdict(it.path)),
                description=f"{it.name}：{desc}",
                nesma=Nesma(type=t2,
                            rationale=(f"判为 {t2}：自 {it.id} 拆出 —— 该行原含两个基本处理，"
                                       f"经逐条阅读确认（表8.6 p.19）。原描述：{desc}"),
                            counted_by=f"split:F-adjudicated@{new_version} from {it.id}"),
                app_type=it.app_type, dev_category=it.dev_category,
                maturity=it.maturity, maturity_evidence=it.maturity_evidence,
                since=new_version, status="draft", source=it.source, tags=list(it.tags)))
            delta += W[t2]
            log["fp_split"] += 1

    bom.items.extend(new_items)
    bom.version = new_version
    return bom, {"adjustment": "ADJ", "delta_ufp": delta, **log,
                 "added_items": len(new_items)}


ADJUSTMENTS["ADJ"] = apply_adjudications


if __name__ == "__main__":
    main()
