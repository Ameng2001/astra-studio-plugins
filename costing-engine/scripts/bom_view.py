"""v2 分层 BOM → v1 平铺视图的兼容层。

## 为什么要这一层

`quote_generate` 4,358 行、`baseline_build` 599 行的计价逻辑是 v1 用真实项目
趟出来的：取整发生在哪一层、复用系数逐模块乘还是整块乘、五阶段占比怎么摊、
甲乙两本的交叉校验……这些是**踩过坑换来的**（"取整一次还是逐档取整"差过 170 元、
"逐条目累加 vs 按模块分档"差过 307 UFP）。重写等于把坑再踩一遍。

所以不改计价逻辑，改数据形态：把 `bom/shared/**` + `bom/verticals/<v>/**`
按 deal.compose 组合、裁剪、施加 overrides 之后，**物化成一个 v1 结构的临时目录**，
再把 `root` 指向它。对下游而言，它看到的仍是熟悉的 `bom/items/*.yaml` +
`bom/taxonomy.yaml`。

## 为什么是物化目录而不是猴补丁

下游有 11 处直接 `glob(root/"bom"/"items"/"*.yaml")`、3 处直接读 taxonomy。
逐处打补丁要改 16 个位置且容易漏；漏掉的那处会**静默读到空目录**，
表现为"某个子系统金额为 0"而不是报错 —— 正是最难查的那类缺陷。
物化一次，所有读法自动一致。

## 验收

用同一份数据出表，金额须与 v1 一分不差。这是唯一能证明兼容层没改变
计价语义的检验（架构规矩四）。
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from bom_schema import Bom


def materialize(bom_root: Path, deal: dict[str, Any], out: Path | None = None) -> Path:
    """把分层 BOM 组合成 v1 平铺结构，返回可作 `root` 用的目录。

    产出：
        <out>/bom/VERSION           组合版本串
        <out>/bom/items/*.yaml      按子系统一文件（与 v1 同）
        <out>/bom/taxonomy.yaml     各层 taxonomy 合并，overrides 已施加
        <out>/bom/logical-files.yaml
        <out>/bom/effort-wbs.yaml   若各层有则合并
        <out>/bom/vocabulary.yaml   若有
    """
    bom_root = Path(bom_root)
    out = Path(out or tempfile.mkdtemp(prefix="bomview-"))
    d = out / "bom"
    if d.exists():
        shutil.rmtree(d)
    (d / "items").mkdir(parents=True)

    b = Bom.compose(bom_root, deal["compose"])
    disp = deal.get("display_names") or {}
    (d / "VERSION").write_text(b.version + "\n", encoding="utf-8")

    # ---- items：按子系统分片，与 v1 的 default_shard_key 同口径 ----
    # ---- 显示名：**只改物化视图，产品级 BOM 一个字不动** ----
    # 商机可以要求沿用历史叫法（柳州已按旧名报过一轮，改名评审会问），
    # 但那是这一个商机的展示口径，不是产品事实 —— 写进 BOM 就会溢出到
    # 其他商机（架构规矩二、三）。
    #
    # **总表与明细分两路映射**，因为它们的取数源不同：总表按 taxonomy 的
    # 产品线分组，明细按条目自己的 path.product_line 显示。v1 这两处本就
    # 不一致（taxonomy 说 AI计算平台 属支撑基座，条目 path 写行业大模型），
    # 用一份映射对齐不了两处。
    d_detail = disp.get("product_line_detail") or {}
    d_by_sys = disp.get("by_system") or {}
    # 条目 ID 的展示前缀。ID 在库里是**键**（规矩一：带命名空间、永不复用），
    # 但印在送审件上它同时是给评审看的编号，而柳州已按不带 vertical 前缀的
    # 旧编号报过一轮。所以允许商机在展示层剥掉该前缀 —— 库里一个字不改。
    strip = disp.get("strip_id_prefix") or []
    if isinstance(strip, str):
        strip = [strip]
    by_sys: dict[str, list] = {}
    seen_id: dict[str, str] = {}
    for it in b.items:
        row = it.to_dict()
        pl = (row.get("path") or {}).get("product_line")
        new = d_by_sys.get(it.path.system) or d_detail.get(pl)
        if new and new != pl:
            row["path"]["product_line"] = new
            row["_display_name_from"] = pl      # 留痕：改前叫什么
        for pre in strip:
            iid = str(row.get("id") or "")
            if pre in iid:
                short = iid.replace(pre, "", 1)
                # **剥前缀会不会撞车，必须当场验。** 前缀正是用来隔离命名空间的，
                # 剥掉之后两层可能出现同号；重号会让 UFP 被计两遍或被去重掉一条，
                # 而金额照样算得出来（v1 真出过 110 个重号、472 UFP 双计）。
                if short in seen_id:
                    raise ValueError(
                        f"strip_id_prefix {pre!r} 使 {iid} 与 {seen_id[short]} "
                        f"同为 {short} —— 剥掉命名空间后重号。"
                        f"重号不会报错，只会让 UFP 少算或多算，所以这里硬失败。")
                seen_id[short] = iid
                row["_id_full"] = iid
                row["id"] = short
                break
        else:
            seen_id.setdefault(str(row.get("id") or ""), str(row.get("id") or ""))
        by_sys.setdefault(it.path.system, []).append(row)
    for s, items in by_sys.items():
        # 文件名用子系统名；v1 也是这么分的，下游只 glob 不认文件名
        safe = str(s).replace("/", "-")
        (d / "items" / f"{safe}.yaml").write_text(
            yaml.safe_dump({"schema_version": 1, "bom_version": b.version,
                            "items": items},
                           allow_unicode=True, sort_keys=False, width=120),
            encoding="utf-8")

    # ---- taxonomy：合并各层 + 施加 deal.overrides ----
    tax: dict[str, Any] = {"schema_version": 2, "product_lines": {}}
    conv = bom_root / "conventions.yaml"
    if conv.exists():
        c = yaml.safe_load(conv.read_text(encoding="utf-8")) or {}
        for k in ("id_scheme", "maturity_vocabulary", "costing_method_by_layer"):
            if c.get(k):
                tax[k] = c[k]
    d_sum = disp.get("product_line_summary") or {}
    for pl, pd in (b.taxonomy.get("product_lines") or {}).items():
        name = d_sum.get(pl, pl)
        if name != pl:
            pd = {**pd, "_display_name_from": pl}
        tax["product_lines"][name] = pd
    _apply_overrides(tax, deal.get("overrides") or {}, deal)
    (d / "taxonomy.yaml").write_text(
        yaml.safe_dump(tax, allow_unicode=True, sort_keys=False), encoding="utf-8")

    # ---- 逐层同名文件的合并 ----
    _merge_groups(bom_root, deal, d, "logical-files.yaml", "groups")
    _merge_groups(bom_root, deal, d, "effort-wbs.yaml", "templates")
    # vocabulary 是跨层通用的受控字段词表，放 bom/ 根而非层目录；
    # 也允许层内覆盖（先找层，再回落根）。
    for name in ("vocabulary.yaml",):
        for src in _layer_dirs(bom_root, deal) + [bom_root]:
            p = src / name
            if p.exists():
                shutil.copy(p, d / name)
                break
    return out


def merge_devices(bom_root: Path, deal: dict, dst: Path) -> dict[str, int]:
    """合并各层设备 BOM 到 <dst>/devices，并把出仓的价格合回物料表。

    **价格与物料分文件存放**（materials.yaml 推飞书共创，pricing.yaml 不推）：
    市场单价是产品级参考价、ref_vendors 含供应商名称与其报价 —— 两者在共创
    阶段都不该被看到。但**出表金额不能因此改变**，所以引擎在这里按料号合回来。

    成本价（cost-reference.yaml）不参与合并 —— 那是内部件中的内部件，
    只有丙附的生成器读它，任何送审件与共创表都不得出现。
    """
    import os
    d = dst / "devices"
    d.mkdir(parents=True, exist_ok=True)
    layers = [bom_root / "shared" / "devices"]
    for vert in (deal["compose"].get("vertical") or {}):
        layers.append(bom_root / "verticals" / vert / "devices")
    layers = [p for p in layers if p.exists()]
    stat: dict[str, int] = {}
    for name, key in (("taxonomy.yaml", "scenes"), ("materials.yaml", "materials"),
                      ("catalog.yaml", "entries"), ("service-fees.yaml", "fees"),
                      ("service-fee-catalog.yaml", "entries"),
                      ("scene-renames.yaml", None)):
        rows: list = []
        hdr: dict[str, Any] = {}
        found = False
        for lay in layers:
            p = lay / name
            if not p.exists():
                continue
            found = True
            doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            if key is None:
                hdr.update(doc)
                continue
            for k, v in doc.items():
                if k != key:
                    hdr.setdefault(k, v)
            rows.extend(doc.get(key) or [])
        if not found:
            continue
        # ---- 展示顺序：按各行自己的 display_seq 排，不靠层的先后 ----
        # 层顺序不是展示顺序。把网络覆盖拆到共享层之后，它就从第 18 位跳到
        # 第 1 位 —— 内容一条没变，120 行全部错位，金额分毫不差，
        # 所以没有任何一处校验会响。
        #
        # **序号挂在条目上，不挂在场景上。** 源表的行序并没有按场景聚合
        # （健康体测的条目散在第 2–15 与第 81 行），按场景排会把它们并到
        # 一起 —— 那是「整理得更整齐」，但与已报过的版本对不上了。
        # 丙附是配置录入工具，行序变了等于让填表的人重新找行。
        if key in ("scenes", "entries") and any("display_seq" in r for r in rows):
            rows.sort(key=lambda x: (x.get("display_seq") is None,
                                     x.get("display_seq") or 0))
        if key == "materials":
            # 价格合回：按料号取 pricing.yaml。缺价格文件时物料无价，
            # 出表会走「待核价」而非静默按 0 计 —— 后者会让金额悄悄变小。
            px: dict[str, dict] = {}
            for lay in layers:
                pp = lay / "pricing.yaml"
                if pp.exists():
                    px.update((yaml.safe_load(pp.read_text(encoding="utf-8")) or {})
                              .get("prices") or {})
            for m in rows:
                m.update(px.get(m.get("code")) or {})
            stat["priced"] = sum(1 for m in rows if m.get("price_yuan"))
        (d / name).write_text(
            yaml.safe_dump({**hdr, **({key: rows} if key else {})},
                           allow_unicode=True, sort_keys=False),
            encoding="utf-8")
        if key:
            stat[name] = len(rows)
    # ---- 成本价：从 pricing.yaml 的 cost 区收，单独落盘 ----
    # **隔离由代码保证，不靠人记得**：cost 区绝不并入 materials.yaml，
    # 只写到 cost-reference.yaml —— 下游只有丙附的生成器读那个文件，
    # 甲/甲附/乙 读的是 materials.yaml，那里没有成本价。
    # v1 发生过「一列成本被标成对外市场单价」的事故，靠的就是这条物理隔离兜住。
    cost: dict[str, Any] = {}
    for lay in layers:
        pp = lay / "pricing.yaml"
        if not pp.exists():
            continue
        for code, v in ((yaml.safe_load(pp.read_text(encoding="utf-8")) or {})
                        .get("cost") or {}).items():
            if v and v.get("cost_yuan"):
                cost[code] = v["cost_yuan"]
    stat.pop("_scene_seq", None)      # 内部传递用，不进对外统计
    if cost:
        (d / "cost-reference.yaml").write_text(
            yaml.safe_dump({"version": "0.1.0",
                            "source": "各层 pricing.yaml 的 cost 区",
                            "note": ("⚠ 内部件中的内部件：只供丙附定价参考，"
                                     "严禁进入任何送审件与共创表。"
                                     "本文件由 merge_devices 从 pricing.yaml 生成，"
                                     "不要手工编辑 —— 改成本价请改各层 pricing.yaml。"),
                            "prices": cost},
                           allow_unicode=True, sort_keys=False),
            encoding="utf-8")
        stat["cost"] = len(cost)
    return stat


def _layer_dirs(bom_root: Path, deal: dict) -> list[Path]:
    """deal 引用到的各层根目录（shared 与 vertical 的层根，非分组目录）。"""
    out = [bom_root / "shared"]
    for vert in (deal["compose"].get("vertical") or {}):
        out.append(bom_root / "verticals" / vert)
    return [p for p in out if p.exists()]


def _merge_groups(bom_root: Path, deal: dict, dst: Path, name: str, key: str) -> None:
    """把各层的同名文件按主键合并。缺文件就跳过，不报错。

    **主键的类型决定合并方式**：`logical-files.groups` 是 list（逐条追加），
    `effort-wbs.templates` 是 dict（按子系统名归并）。一律当 list 处理会让
    下游拿到 list 后 `.get()` 报 AttributeError —— 这类错至少是当场炸，
    比静默合出一个半对的结构好查。
    """
    merged: dict[str, Any] = {}
    rows: list = []
    mapping: dict[str, Any] = {}
    is_map = False
    for src in _layer_dirs(bom_root, deal):
        p = src / name
        if not p.exists():
            continue
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        for k, v in doc.items():
            if k != key:
                merged.setdefault(k, v)
        val = doc.get(key)
        if isinstance(val, dict):
            is_map = True
            for k, v in val.items():
                if k in mapping and mapping[k] != v:
                    raise ValueError(
                        f"{name}: 键 {k!r} 在多层中定义且内容不同 —— "
                        f"同名模板跨层冲突，须显式裁定归属哪一层")
                mapping[k] = v
        elif val:
            rows.extend(val)
    if is_map or rows or merged:
        merged[key] = mapping if is_map else rows
        (dst / name).write_text(
            yaml.safe_dump(merged, allow_unicode=True, sort_keys=False),
            encoding="utf-8")


def _apply_overrides(tax: dict, ovr: dict, deal: dict | None = None) -> None:
    """把 deal.overrides 写进 taxonomy，使下游按覆盖后的参数计价。

    **只覆盖 deal 显式点名的子系统**。曾经把「某行业的类别取值」写成按行业
    全局生效，结果共享层的三个引擎在该行业贵了 50% —— 同一个 Harness 引擎
    在两个项目报出两个价，违反跨行业一致性。覆盖必须逐子系统指定。

    ## overrides 真正改变的是「归哪一类」，不是「这一类值多少」

    `overrides.app_type[子系统].app_type` 改子系统的类别归属 —— 这个生效。
    同处的 `value` 只是把该类因子**重述一遍**：真正被计价读到的是
    `deal.fp_method_settings.app_type_factors[类别].value`（出套表）与
    标准包的该类缺省（出基线）。

    早先这里把 `value` 写成 `app_type_override_value` 存进 taxonomy，
    而下游两条链路都不读它 —— 一个写了没人读的字段，看起来覆盖生效了，
    实则由别处的值说了算。两处一旦分叉，金额会安静地按另一个数出，
    没有任何一处报错。所以这里不再写它，改为**校验两处一致**：
    不一致就当场失败，把「哪个数说了算」摆到台面上。
    """
    at = ovr.get("app_type") or {}
    ru = ovr.get("reuse") or {}
    factors = (((deal or {}).get("fp_method_settings") or {})
               .get("app_type_factors") or {})
    for s, o in at.items():
        v, cls = o.get("value"), o.get("app_type")
        if v is None or not cls:
            continue
        declared = (factors.get(cls) or {}).get("value")
        if declared is not None and float(declared) != float(v):
            raise ValueError(
                f"overrides.app_type[{s!r}].value = {v} 与 "
                f"fp_method_settings.app_type_factors[{cls!r}].value = {declared} 不一致。\n"
                f"  计价读的是后者 —— 前者只是重述。改一处不改另一处，"
                f"金额会按后者出且不报错。请改成一致，或删掉 overrides 里的 value。")
    for pl, pd in (tax.get("product_lines") or {}).items():
        for s, sd in (pd.get("systems") or {}).items():
            if s in at:
                o = at[s]
                if o.get("app_type"):
                    sd["app_type"] = o["app_type"]
                sd["app_type_basis"] = o.get("basis", sd.get("app_type_basis", ""))
            if s in ru:
                o = ru[s]
                for m in (sd.get("modules") or {}).values():
                    m["maturity_override"] = o.get("level")
                    m["maturity_basis"] = o.get("basis", m.get("maturity_basis", ""))
