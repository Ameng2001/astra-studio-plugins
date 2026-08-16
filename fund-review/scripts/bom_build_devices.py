"""bom_build_devices —— 从场景设备报价表反向抽出**设备 BOM 第一层**。

与软件那条链同构：

    软件   估算表 → bom/items/*.yaml（产品级）→ deals/<id>/（商机级）
    设备   Z03    → bom/devices/（产品级）    → deals/<id>/device-config.yaml（商机级）

## 两层的判据

**换个商机会不会变。** 不变的进第一层：

  · `materials.yaml`  物料主数据 —— 编码、名称、型号、参数、单位、单价、价格状态
  · `catalog.yaml`    场景 × 物料 —— 这个场景下有哪些可选设备、按什么口径配
  · `taxonomy.yaml`   场景树 —— 一级场景 → 规范子场景

变的留在第二层（本脚本不产出）：哪个园选哪个场景、选哪些设备、规模多大。

## 为什么键是（子场景 × 物料）而不是物料

配置口径**随场景变**。PAD X9 在「智慧教学场景」是「每个教室1台、体育老师1台」，
在「教学助手场景」是「全量增补」。按物料建目录会把两套口径压成一套，
而压掉的那套不会有任何地方报错。

物料的**参数**（型号/单价/单位）不随场景变，所以单独放 materials.yaml，
catalog 只引编码。一处维护，不复制。

## 规则解析：宁可不解析，不可猜

配置口径现在是自由文本（「每个园配1个」「厨房1台」「全量增补」…）。
本脚本只解析**能明确对上模式**的，其余一律标 `unparsed` 并原样保留文本。

本项目在这件事上已经栽过一次：用正则从描述里抽名词，抽出来「编辑和禁用等」
「通过大」这种垃圾，而且看起来像是有结果的。所以这里的原则是
**解析不了就说解析不了**，交给共创去补，不要产出一个像模像样的错值。

用法：
    python3 bom_build_devices.py --root <项目根> [--source <Z03路径>]
"""
from __future__ import annotations

import argparse
import re
import sys
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import openpyxl
import yaml

# ============================================================
# 配置口径的词汇表
# ============================================================
#
# 每条 = (正则, kind, 取数量的组名或固定值)。**顺序即优先级**，先匹配先算。
# 加新模式前先问：这个模式会不会把一个本该「待判定」的文本吃成一个错值？
# 吃错了不会报错 —— 它只会让数量推导出一个看起来正常的数。

_NUM = r"(?P<n>[0-9１-９一二两三四五六七八九十]+)"
_CN2I = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
         "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

RULE_PATTERNS: list[tuple[str, str, str]] = [
    # —— 每园 ——
    (rf"每个?(?:幼儿)?园(?:所|区)?(?:配置?|需要?)?\s*{_NUM}\s*[个套台张部]", "per_garden", "n"),
    (r"每个?(?:幼儿)?园(?:所|区)?(?:配置?|需要?)?一[个套台张部]", "per_garden", "1"),
    # 「1个园1个」—— 第一个数是「园」的量词，取的是第二个数
    (r"[0-9１-９一]\s*个园\s*(?P<n>[0-9１-９一二两三四五六七八九十]+)\s*[个套台张部]",
     "per_garden", "n"),
    (r"配置?一套?即可", "per_garden", "1"),
    # —— 每班 ——
    (rf"每个?班(?:级)?(?:配置?)?\s*{_NUM}\s*[个套台张部]", "per_class", "n"),
    (rf"每班配?\s*{_NUM}\s*[个套台张部]", "per_class", "n"),
    (r"按一个班级配", "per_class", "1"),
    (r"每个班级一", "per_class", "1"),
    # —— 每教室 ——
    (rf"每个?教室\s*{_NUM}\s*[个套台张部]", "per_room", "n"),
    # —— 每人 ——
    (r"每人一[个套台]", "per_child", "1"),
    (r"每位?(?:学生|幼儿)一[个套台]", "per_child", "1"),
    # —— 功能室 / 点位 ——
    (rf"(?P<at>厨房|园医室?|门口|校门口|保健室|体育老师)\s*(?:各配)?\s*{_NUM}\s*[个套台]",
     "per_facility", "n"),
    (r"(?P<at>幼儿园门口)配置一台", "per_facility", "1"),
]

#: 明确**不是**配置口径的备注 —— 是过程痕迹或状态标记，不该被当成规则。
NON_RULE = ("全量增补", "由基准清单补录", "待补单价", "待确认", "仅单点对接",
            "根据实际", "根据人数", "待定")

#: 含内部成本口径的备注，结构化时归内部字段，**不进对外目录**。
INTERNAL_HINT = ("成本", "毛利", "渠道价", "备货价")


#: 残余文本里还有「数字 + 量词」就说明没解析干净
_LEFTOVER_QTY = re.compile(r"[0-9１-９一二两三四五六七八九十]\s*[个套台张部]")
#: 残余里出现**位点词**同样算没覆盖干净 —— 数字不一定跟着它。
#: 「厨房和园医室各配一台」里那个「一台」只跟在园医室后面，
#: 匹配完只剩「厨房」，没有数字，按数字判会得出「已完整覆盖」，
#: 而厨房那一台就这么丢了。
_LEFTOVER_AT = re.compile(r"厨房|园医|保健|门口|教室|班级|体育老师|每人|每位|每班|每园")


def parse_rule(note: str) -> dict[str, Any]:
    """把一条备注解析成配置口径。**解析不干净就说不干净。**

    一条备注常含多段口径：「每个班级一，园医室一台」「每班配1台，厨房和园医室
    各配一台」「每个教室1台、体育老师1台」。只取第一段的话，规则看起来解析成功了，
    按它推数量却系统性偏少 —— 又是那个形状：不报错，只是数错。

    所以这里扫**全部**模式取所有不重叠的匹配，再看剩下的文本里还有没有
    「数字+量词」。还有就标 `covered: false` 并把残余原文留下，交给共创补。
    """
    t = (note or "").strip()
    if not t:
        return {"kind": "unparsed", "reason": "备注为空", "covered": False}
    if any(h in t for h in INTERNAL_HINT):
        return {"kind": "internal_only", "covered": False,
                "reason": "含内部成本口径，不进对外目录"}

    hits: list[tuple[int, int, dict]] = []
    for pat, kind, grp in RULE_PATTERNS:
        for m in re.finditer(pat, t):
            if any(not (m.end() <= s0 or m.start() >= e0) for s0, e0, _ in hits):
                continue                      # 与已取的段重叠，跳过
            if grp == "n":
                raw = m.groupdict().get("n") or ""
                qty = _CN2I.get(raw)
                if qty is None:
                    try:
                        qty = int(re.sub(r"[^\d]", "", raw) or 0)
                    except ValueError:
                        qty = 0
                if not qty:
                    continue
            else:
                qty = int(grp)
            one = {"kind": kind, "qty": qty}
            at = m.groupdict().get("at")
            if at:
                one["at"] = at
            hits.append((m.start(), m.end(), one))
    hits.sort()

    left = t
    for s0, e0, _ in sorted(hits, reverse=True):
        left = left[:s0] + "\x00" + left[e0:]
    left = re.sub(r"[\x00\s、，,；;。/和及与]+", "", left)

    if not hits:
        why = ("过程痕迹/状态标记，非配置口径"
               if any(h in t for h in NON_RULE) else "文本未命中任何已登记口径")
        return {"kind": "unparsed", "reason": why, "covered": False}

    covered = not (_LEFTOVER_QTY.search(left) or _LEFTOVER_AT.search(left))
    if len(hits) == 1:
        out = dict(hits[0][2])
        out["covered"] = covered
        if not covered:
            out["unmatched"] = left[:80]
        return out
    out = {"kind": "composite", "parts": [h[2] for h in hits], "covered": covered}
    if not covered:
        out["unmatched"] = left[:80]
    return out


# ============================================================
# 读源
# ============================================================

def read_source(path: Path) -> tuple[list[dict], list[dict]]:
    """返回 (逐行配置, 场景命名对照)。**只读，不改源表。**"""
    wb = openpyxl.load_workbook(path, data_only=True)
    rows: list[dict] = []
    for sn in wb.sheetnames:
        if not sn[:2].isdigit() or sn.startswith(("88", "99")):
            continue
        ws = wb[sn]
        h = {ws.cell(2, c).value: c for c in range(1, ws.max_column + 1)}
        need = {"设备名称", "物料编码", "数量", "子场景/用途"}
        if not need <= set(h):
            raise SystemExit(f"{sn}: 缺列 {sorted(need - set(h))}")
        for r in range(3, ws.max_row + 1):
            g = lambda k: ws.cell(r, h[k]).value if k in h else None
            if not g("设备名称"):
                continue
            rows.append({
                "garden": sn, "row": r,
                "l1": str(g("一级场景") or "").strip(),
                "scene": str(g("子场景/用途") or "").strip(),
                "name": str(g("设备名称")).strip(),
                "model": str(g("品牌型号") or "").strip(),
                "code": str(g("物料编码") or "").strip(),
                "unit": str(g("单位") or "").strip(),
                "self_made": str(g("是否自研") or "").strip(),
                "qty": g("数量") if isinstance(g("数量"), (int, float)) else None,
                "price": g("市场单价") if isinstance(g("市场单价"), (int, float)) else None,
                "note": str(g("备注") or "").strip(),
            })
    tax = []
    if "99_场景命名对照" in wb.sheetnames:
        ws = wb["99_场景命名对照"]
        for r in range(3, ws.max_row + 1):
            n = ws.cell(r, 1).value
            if n:
                tax.append({"scene": str(n).strip(),
                            "l1": str(ws.cell(r, 2).value or "").strip(),
                            "aliases": str(ws.cell(r, 3).value or "").strip(),
                            "origin": str(ws.cell(r, 4).value or "").strip()})
    dic = {}
    if "88_物料主数据字典" in wb.sheetnames:
        ws = wb["88_物料主数据字典"]
        h = {ws.cell(2, c).value: c for c in range(1, ws.max_column + 1)}
        # ⚠️ 白名单，只读这几列。以下几列**读都不读**：
        #
        #   供应商        商业信息
        #   渠道单价      内部口径
        #   ★canonical单价  实测是**成本价**（76/101 条低于建议市场单价）
        #   说明/原因     里面写着「统一单价930/237/128」—— 逐条核过，
        #                 那三个数正是 canonical 单价，即成本价；
        #                 另有一条写着供应商名「台州美居纳」。
        #
        # 最后一条是这次差点漏掉的：它看着只是一列工作说明，实际同时泄成本和供应商。
        # 脱敏靠「没取过」比靠「取了再删」可靠 —— 取了再删要求每加一列都记得复查。
        SAFE = ("核心参数", "品牌", "参考对比厂家", "冲突标记", "★处置方式",
                "★canonical设备名", "★canonical型号", "★canonical单位",
                "建议市场单价")
        for r in range(3, ws.max_row + 1):
            c = ws.cell(r, h["物料编码"]).value
            if c:
                dic[str(c).strip()] = {k: str(ws.cell(r, h[k]).value or "").strip()
                                       for k in SAFE if k in h}
    return rows, tax, dic


#: 未归类的场景标记 —— 源表里业务侧的补录标记，不是场景名
UNSCENED = ("", "✓", "基准补录", "None")

#: 这些不是物料编码，是占位符。当成编码的话，7 个不同的算法会被并成一个物料，
#: 而合并后的那条看起来完全正常（有名字、有单价），只是名字是七选一。
NO_CODE = ("", "无", "-", "—", "/", "None", "待补")


def _mat_key(r: dict) -> str:
    c = (r.get("code") or "").strip()
    return c if c not in NO_CODE else f"NOCODE-{r['name']}"


def scene_code(idx: int) -> str:
    return f"S{idx:02d}"


def _independent_tally(path: Path) -> tuple[dict[str, float], float]:
    """**独立**重读工作簿，汇总各表的数量与市场总价。

    这个函数存在的唯一理由是：它不能与 `read_source` 共用任何中间结果。
    对账拿被检查方自己的产物当基准，等于没对账。

    合计行没有设备名也没有数量（已核），故按「数量为数值」筛即可；
    金额取源表自己的「市场总价」列 —— 与 数量×单价 是两列数据。
    """
    wb = openpyxl.load_workbook(path, data_only=True)
    q: dict[str, float] = {}
    money = 0.0
    for sn in wb.sheetnames:
        if not sn[:2].isdigit() or sn.startswith(("88", "99")):
            continue
        ws = wb[sn]
        h = {ws.cell(2, c).value: c for c in range(1, ws.max_column + 1)}
        for r in range(3, ws.max_row + 1):
            if str(ws.cell(r, 1).value or "").strip() in ("合计", "小计"):
                continue
            v = ws.cell(r, h["数量"]).value if "数量" in h else None
            if isinstance(v, (int, float)):
                q[sn] = q.get(sn, 0.0) + v
            t = ws.cell(r, h["市场总价"]).value if "市场总价" in h else None
            if isinstance(t, (int, float)):
                money += t
    return q, money


def _service_fee_codes(root: Path) -> set[str]:
    """`service-fees.yaml` 里声明的料号 —— 这些是**按年收的经常性费用，不是设备**。

    拆出来的理由不是分类洁癖：柳州标准 三.(三) 运维是独立预算科目、不列入
    建设期预算。单位写着「年」的行留在硬件设备购置费里，评审一眼就会问。

    文件缺失返回空集（不拆），不报错 —— 老商机没有这个文件。
    """
    f = root / "bom" / "devices" / "service-fees.yaml"
    if not f.exists():
        return set()
    d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    return {x["code"] for x in (d.get("fees") or []) if x.get("code")}


def _idmap(bt: str, tid: str, field: str):
    """record_id → 该字段值。飞书关联列存的是 record_id，要自己换回业务键。"""
    sys.path.insert(0, str(Path(__file__).parent))
    from lark_table import lark
    d = lark("base", "+record-list", "--base-token", bt, "--table-id", tid,
             "--as", "user", "--limit", "200").get("data") or {}
    cols, rows, ids = d.get("fields") or [], d.get("data") or [], \
        d.get("record_id_list") or []
    if field not in cols:
        return []
    i = cols.index(field)
    return [(rid, (row[i],)) for rid, row in zip(ids, rows)]


def load_lark_devices(root: Path) -> dict[str, Any]:
    """从飞书「可售设备BOM」取回物料 / 场景 / 配置口径。

    **方向是反的了。** 原来是 Z03 → 本地 → 推飞书，飞书只是评审层；
    2026-08-12 起飞书是**产品级设备目录的唯一事实源**，Z03 只留两样
    商机级数据：各园实配数量、以及（作为种子的）市场单价。

    为什么非改不可：物料在飞书上被持续维护（新增设备、判可售、补参考机型、
    去重），而本地 materials.yaml 标着 `generated_by`，任何人跑一次重建
    就把这些维护成果清空 —— 实测清掉过 9 条。一个「会被定期清零的下游」
    不是下游，是数据丢失点。

    读不到就**硬失败**，不回落 Z03：静默回落会让「飞书没连上」表现为
    「物料少了 16 条」，而少了的那些恰好是飞书上新增的那批。
    """
    cfg = root / "bom" / "devices" / "lark-sync.json"
    if not cfg.exists():
        raise SystemExit(f"缺 {cfg} —— 飞书是设备目录的事实源，没有它无法构建")
    c = json.loads(cfg.read_text(encoding="utf-8"))
    bt, tb = c["base_token"], c["tables"]
    sys.path.insert(0, str(Path(__file__).parent))
    from lark_bom_split_pull import list_all, _txt

    def num(v: str) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    mats, cost_ref = {}, {}
    for r in list_all(bt, tb["1 物料主数据"]):
        code = _txt(r.get("标识·物料编码")).strip()
        if not code:
            continue
        if code in mats:
            raise SystemExit(
                f"飞书物料主数据有重复料号「{code}」—— 以料号为键会撞键。"
                f"请先在飞书去重（同料号多行时保留有血缘的那行）")
        mats[code] = {
            "code": code,
            "name": _txt(r.get("标准设备名称")) or "**待补**",
            "model": _txt(r.get("参数·型号")) or "**待补**",
            "unit": _txt(r.get("参数·单位")) or "**待补**",
            "self_made": _txt(r.get("商务·是否自研")) or "**待补**",
            "price_yuan": num(_txt(r.get("商务·市场单价(元)"))),
            "sellable": _txt(r.get("商务·是否可售")) or "待确认",
            "spec": _txt(r.get("参数·性能参数")) or "**待补**",
            "brand": _txt(r.get("参数·品牌")),
            # ⚠ **不要按分隔符切这一列来数机型数。** 飞书那列当前装的是
            # 填写模板（「厂家1：型号1：价格1｜厂家2：…｜厂家3：…」），
            # 切出来是 9 段，加本机型算成 10 —— 112 条设备因此被判成
            # 「参考机型≥3，达标」，而甲本 02 写着「0 项不满足」。
            # 表7 注3 是硬件段最容易被整体质疑的一条，在这里给出一个
            # 假达标，方向还对我方有利，是最不能出的错。
            # 机型数只认飞书人工维护的「商务·参考机型数」，切分只做展示。
            # **不按 `/` 切**：型号里带斜杠很常见（DS-D5A86FB/E、AC1900/AX1500），
            # 按它切会把一条厂家拆成两条 —— 实测「海康：DS-D5A86FB/E：10818」
            # 被拆成「海康：DS-D5A86FB」+「E：10818」，参考机型数凭空多一个，
            # 而表7 注3 的合规判定正是数这个数。厂家之间用顿号/逗号/分号分隔。
            "ref_vendors": [x for x in re.split(
                r"[、,，;；|｜\n]+", _txt(r.get("商务·参考对厂家型号")))
                if x.strip() and x.strip() not in ("无", "-")
                and not re.fullmatch(r"[厂家型号价格\s：:0-9]+", x.strip())],
            "ref_count": int(float(_txt(r.get("商务·参考机型数")) or 0)),
            "in_dict": _txt(r.get("来源·在物料字典中")) == "是",
            # OA 名只作**消歧兜底**：同名同型号时用它区分（幼视宝算法四条
            # 名称型号全同，区分信息只在这一列）。不作展示主名。
            **({"oa_name": _txt(r.get("OA系统设备名称"))}
               if _txt(r.get("OA系统设备名称")) else {}),
        }
        # 成本价**单独收**，不进 mats —— materials.yaml 一路通到甲附，
        # 把成本塞进去就是给泄露留一条随时会触发的路径。今天已经见过
        # 「一列成本被标成对外市场单价」的事故。
        try:
            _c = float(_txt(r.get("商务·成本单价(元)")) or 0)
        except ValueError:
            _c = 0.0
        if _c:
            cost_ref[code] = _c
        mats[code]["price_status"] = ("已定价" if mats[code]["price_yuan"]
                                      else "**待核价**")
        if _txt(r.get("来源·字典处置方式")):
            mats[code]["dict_disposal"] = _txt(r.get("来源·字典处置方式"))
    # 键名与本地 taxonomy 对齐用 `scene`，不用 `name` —— 下游按 `scene` 取。
    # 飞书那张表**同名多行**（AI助教场景 / 智慧教学场景 各两行，因为它们各由
    # 两个旧名合并而来），按名字去重，别名合并到一处。
    scenes: dict[str, dict] = {}
    for r in list_all(bt, tb["0 场景树"]):
        n_ = _txt(r.get("规范子场景"))
        if not n_:
            continue
        e = scenes.setdefault(n_, {"scene": n_,
                                   "l1": _txt(r.get("场景·一级场景")),
                                   "code": _txt(r.get("场景·场景码")),
                                   "src": []})
        if _txt(r.get("来源·涵盖的原始叫法")):
            e["src"].append(_txt(r.get("来源·涵盖的原始叫法")))
    scenes = list(scenes.values())
    # 配置目录：料号 → 该物料在飞书上归属的场景集合。
    # 这是「这台设备属哪个场景」的**事实源** —— Z03 各园表的「子场景/用途」列
    # 在新版里有 39 行是 #REF!，还留着一批已正名的旧场景名。
    sid = {rid: row[0] for rid, row in _idmap(bt, tb["0 场景树"], "规范子场景")}
    mid = {rid: row[0] for rid, row in _idmap(bt, tb["1 物料主数据"], "标识·物料编码")}
    cat_scene: dict[str, set] = {}
    for r in list_all(bt, tb["2 配置目录"]):
        m_, s_ = r.get("物料"), r.get("场景")
        if not (isinstance(m_, list) and m_ and isinstance(s_, list) and s_):
            continue
        c_ = str(mid.get(m_[0]["id"]) or "").strip()
        n_ = str(sid.get(s_[0]["id"]) or "").strip()
        if c_ and n_:
            cat_scene.setdefault(c_, set()).add(n_)

    rn = root / "bom" / "devices" / "scene-renames.yaml"
    ren = (yaml.safe_load(rn.read_text(encoding="utf-8")) or {}).get(
        "renames", {}) if rn.exists() else {}
    return {"materials": mats, "scenes": scenes, "scene_renames": ren,
            "cat_scene": cat_scene, "cost_ref": cost_ref}


def build(root: Path, src: Path, lark: dict | None = None) -> dict[str, Any]:
    rows, tax_rows, dic = read_source(src)
    out_dir = root / "bom" / "devices"
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- 场景树。code 一经写入就是**声明值**，重建时沿用 ----
    tpath = out_dir / "taxonomy.yaml"
    prev_codes = {}
    if tpath.exists():
        old = yaml.safe_load(tpath.read_text(encoding="utf-8")) or {}
        for s in (old.get("scenes") or []):
            prev_codes[s["scene"]] = s["code"]
    scenes = []
    nxt = max([int(c[1:]) for c in prev_codes.values()] or [0]) + 1
    for t in tax_rows:
        code = prev_codes.get(t["scene"])
        if not code:
            code = scene_code(nxt); nxt += 1
        scenes.append({"scene": t["scene"], "code": code, "l1": t["l1"],
                       "aliases": [a.strip() for a in re.split(r"[/、,，]", t["aliases"]) if a.strip()],
                       "origin": t["origin"]})
    used = {r["scene"] for r in rows if r["scene"] not in UNSCENED}
    declared = {s["scene"] for s in scenes}
    for s in sorted(used - declared):          # 用到了但没登记 —— 必须列出来
        scenes.append({"scene": s, "code": scene_code(nxt), "l1": "**待补**",
                       "aliases": [], "origin": "**未登记**：各园表在用但 99 表未声明"})
        nxt += 1
    code_of = {s["scene"]: s["code"] for s in scenes}

    # ---- 物料主数据 ----
    mats: dict[str, dict] = {}
    for r in rows:
        c = _mat_key(r)
        m = mats.setdefault(c, {"code": c, "names": set(), "models": set(),
                                "units": set(), "prices": set(),
                                "self_made": set(), "rows": 0})
        m["rows"] += 1
        m["names"].add(r["name"]); m["models"].add(r["model"])
        m["units"].add(r["unit"]); m["self_made"].add(r["self_made"])
        if r["price"]:
            m["prices"].add(r["price"])
    materials, mat_issues = [], []
    price_diverge = []
    for c, m in sorted(mats.items()):
        d = dic.get(c, {})
        conflicts = {k: sorted(m[k]) for k in ("names", "models", "units")
                     if len(m[k]) > 1}
        if len(m["prices"]) > 1:
            conflicts["prices"] = sorted(m["prices"])
        if conflicts:
            mat_issues.append({"code": c, "conflict": conflicts})

        # 字典的 canonical 值用来**补空**，不用来覆盖 ——
        # 各园表是实际配置，字典是主数据规范；两者不一致时列出来给人裁，
        # 静默取一边会让另一边的信息消失。
        name = sorted(m["names"])[0]
        model = sorted(m["models"])[0]
        unit = sorted(m["units"])[0]
        canon = {"name": d.get("★canonical设备名", ""),
                 "model": d.get("★canonical型号", ""),
                 "unit": d.get("★canonical单位", "")}
        diverge = {k: [v, canon[k]] for k, v in
                   (("name", name), ("model", model), ("unit", unit))
                   if v and canon[k] and v != canon[k]}
        filled = [k for k, v in (("model", model), ("unit", unit)) if not v and canon[k]]
        model = model or canon["model"]
        unit = unit or canon["unit"]

        price = sorted(m["prices"])[0] if m["prices"] else 0
        dp = d.get("建议市场单价")
        try:
            dp = float(dp) if str(dp).strip() else None
        except (TypeError, ValueError):
            dp = None
        # 字典的建议市场单价与各园表的市场单价必须一致（实测 77/77 一致）。
        # 分家了就是两处报价，而汇总表只会用其中一处 —— 差额静默消失。
        if dp is not None and price and abs(dp - price) > 0.01:
            price_diverge.append((c, name, price, dp))
        if not price and dp:
            price = dp                      # 各园表没价、字典有 → 用字典的补

        materials.append({
            "code": c,
            "name": name,
            "model": model or "**待补**",
            "unit": unit or "**待补**",
            "self_made": sorted(m["self_made"])[0] or "**待补**",
            "price_yuan": price,
            "price_status": "已定价" if price else "**待核价**",
            # 新商机默认全部可售。不可售的（停产、内部专用）由共创标出来 ——
            # 目录里躺着一台已停产的设备，配上去之前没人会发现。
            "sellable": "可售",
            "spec": d.get("核心参数", "") or "**待补**",
            "brand": d.get("品牌", ""),
            "ref_vendors": [x for x in re.split(r"[、,，/;；\s]+", d.get("参考对比厂家", ""))
                            if x and x not in ("无", "-")],
            "in_dict": bool(d),
            **({"dict_flag": d["冲突标记"]} if d.get("冲突标记") else {}),
            **({"dict_disposal": d["★处置方式"]} if d.get("★处置方式") else {}),
            **({"canon_filled": filled} if filled else {}),
            **({"canon_diverge": diverge} if diverge else {}),
            **({"conflict": conflicts} if conflicts else {}),
        })
    if price_diverge:
        raise SystemExit(
            "字典「建议市场单价」与各园表「市场单价」不一致，已拒绝出目录：\n"
            + "".join(f"  {c} {n}：园所表 {a:,.2f} vs 字典 {b:,.2f}\n"
                      for c, n, a, b in price_diverge)
            + "  两处报价并存时汇总表只会用其中一处，差额静默消失。请先统一源数据。")

    # ---- 目录：（子场景 × 物料）→ 位点 ----
    cat: dict[tuple, dict] = {}
    for r in rows:
        sc = r["scene"] if r["scene"] not in UNSCENED else "未归类（源表场景名待补）"
        c = _mat_key(r)
        key = (sc, c)
        e = cat.setdefault(key, {
            "id": f"DEV.{c}.{code_of.get(sc, 'S99')}",
            "scene": sc, "l1": r["l1"], "material": c, "name": r["name"],
            "placements": {}, "gardens": {},
        })
        note = r["note"]
        p = e["placements"].setdefault(note, {"note": note, "rule": parse_rule(note),
                                              "qty_by_garden": {}})
        p["qty_by_garden"][r["garden"]] = (
            (p["qty_by_garden"].get(r["garden"]) or 0) + (r["qty"] or 0))
        e["gardens"][r["garden"]] = (e["gardens"].get(r["garden"]) or 0) + (r["qty"] or 0)
    catalog = []
    for (sc, c), e in sorted(cat.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        e["placements"] = sorted(e["placements"].values(), key=lambda p: p["note"])
        catalog.append(e)
    # ---- 完整性对账：目录必须无损覆盖源表 ----
    #
    # ⚠️ 必须**独立重读工作簿**，不能拿 `rows` 当「源表」——
    # catalog 就是从 rows 派生的，两边同源的话漏一行两边一起少，
    # 对账永远通过。第一版就是这么写的：看着是个完整性检查，
    # 实际什么都没验（实测漏掉 201 台的一行仍然通过）。
    #
    # 独立性来自两处：① 重新打开工作簿逐格读；② 金额用源表自己的
    # 「市场总价」列，而不是 数量×单价 —— 那是另一列数据。
    src_q, src_money = _independent_tally(src)
    got_q: dict[str, float] = {}
    price_of = {m["code"]: m["price_yuan"] for m in materials}
    got_money = 0.0
    for e in catalog:
        for g, q in e["gardens"].items():
            got_q[g] = got_q.get(g, 0.0) + (q or 0)
        got_money += (price_of.get(e["material"]) or 0) * sum(
            v or 0 for v in e["gardens"].values())
    diff = {g: (src_q.get(g, 0), got_q.get(g, 0))
            for g in set(src_q) | set(got_q)
            if abs(src_q.get(g, 0) - got_q.get(g, 0)) > 1e-9}
    if diff or abs(src_money - got_money) > 1:
        raise SystemExit(
            f"目录与源表对不上，已拒绝出目录：\n"
            f"  数量差异（园所: 源表 vs 目录）：{diff}\n"
            f"  金额：源表 {src_money:,.2f} vs 目录 {got_money:,.2f}\n"
            f"  抽取漏行不会有任何地方报错 —— 目录看起来正常，只是少了几台设备。")

    # ---- 运营服务费从设备目录里拆出来 ----
    # **拆在对账之后**，不在之前：上面那个守卫比的是「源表全量 vs 目录全量」，
    # 先拆再对账就等于把 6 条从两边同时减掉，守卫照样通过而漏行照样发现不了 ——
    # 和它注释里写的「两边同源，对账永远通过」是同一个陷阱。
    # 拆完再断言 设备 + 服务费 == 拆前，少一条就炸。
    fee_codes, fee_catalog = _service_fee_codes(root), []
    if fee_codes:
        _n0 = len(catalog)
        _q0 = sum(v or 0 for e in catalog for v in e["gardens"].values())
        fee_catalog = [e for e in catalog if e["material"] in fee_codes]
        catalog = [e for e in catalog if e["material"] not in fee_codes]
        materials = [m for m in materials if m["code"] not in fee_codes]
        _q1 = sum(v or 0 for e in catalog + fee_catalog
                  for v in e["gardens"].values())
        if len(catalog) + len(fee_catalog) != _n0 or abs(_q1 - _q0) > 1e-9:
            raise SystemExit("服务费拆分丢了条目或数量 —— 拆分必须是无损划分")

    lark_missing: list = []
    lark_scene_moved: list = []
    lark_scene_ambiguous: list = []
    lark_catalog_added: list = []
    if lark:
        # ---- 物料改用飞书为准 ----
        # 只换**物料参数与价格**，不换数量：数量仍来自 Z03 各园表，
        # 上面那个「源表 vs 目录」的对账守卫因此仍然有效。
        # ---- 场景树也换飞书为准 ----
        # 这一段原来漏了：`lark["scenes"]` 取回来却没用，而输出打印写着
        # 「物料/场景取自飞书」—— 说了没做。后果是飞书那边正名过的旧场景名
        # （智教趣伴一体场景 / 教学助手场景 / 幼视宝（视力健康）场景 …）
        # 一路流到甲本 02_硬件设备购置费汇总，评审看到的是已经废弃的叫法。
        #
        # 旧名不删，转成 aliases：catalog 与 device-config 里存的是旧名，
        # 直接换掉会让那些条目一次性全部对不上场景。别名保留 → 新旧都认，
        # 输出用新名。哪些旧名归到哪个新名，由 `scene_renames` 声明。
        _ren = lark.get("scene_renames") or {}
        _new = {s["scene"]: s for s in lark["scenes"]}
        _keep = []
        for s in scenes:
            tgt = _ren.get(s["scene"], s["scene"])
            if tgt in _new:
                _keep.append(tgt)
        _merged = []
        for s in lark["scenes"]:
            olds = sorted({o for o, n2 in _ren.items() if n2 == s["scene"]}
                          | {a for x in scenes if x["scene"] == s["scene"]
                             for a in (x.get("aliases") or [])})
            _merged.append({"scene": s["scene"], "code": s["code"],
                            "l1": s["l1"],
                            **({"aliases": olds} if olds else {}),
                            "origin": "飞书场景树（2026-08-12 起为事实源）"})
        _orphan = sorted({s["scene"] for s in scenes} - set(_new)
                         - set(_ren))
        if _orphan:
            raise SystemExit(
                f"本地这些场景在飞书场景树里没有、也没声明改名：{_orphan}\n"
                f"  要么在飞书补登记，要么在 bom/devices/scene-renames.yaml 里"
                f"写明它改成了哪个新名。\n"
                f"  不自动丢：catalog 与 device-config 里还挂着这些场景的条目，"
                f"丢掉场景那些条目会变成「未归类」，而金额一分不少 —— "
                f"看起来正常，实际是分组错了。")
        scenes = _merged

        # ---- 场景归属按飞书「2 配置目录」归位 ----
        # Z03 各园表的「子场景/用途」列不再作为场景的事实源，只作候选：
        # 新版源表里 39 行是 #REF!，另有一批已在飞书正名的旧场景名。
        # 归位规则（**不猜**）：
        #   ① Z03 的场景经别名解析后，飞书确认该物料确实属于它 → 用它
        #   ② 否则该物料在飞书**只归一个场景** → 用那一个，并记为已归位
        #   ③ 否则（飞书归多个场景而 Z03 的又用不了）→ 保持原样并列入报告
        # ②是唯一的推断，且只在无歧义时发生；③宁可留「未归类」也不挑一个。
        _cs = lark.get("cat_scene") or {}
        _canon = {}
        for s in lark["scenes"]:
            _canon[s["scene"]] = s["scene"]
        for o_, n_ in (lark.get("scene_renames") or {}).items():
            _canon[o_] = n_
        _moved, _amb = [], []
        for e in catalog:
            # **改名无条件做，归位才看飞书。** 这两件事不同：
            # 改名是「这个场景现在叫什么」（台账已定），归位是「这台设备属哪个
            # 场景」（要飞书确认）。原来写成 `if not fs: continue`，飞书未登记的
            # 物料连改名都跳过 —— 7 条幼视宝算法因此把旧名「幼视宝（视力健康）
            # 场景」带进了丙附的场景下拉，还带了个 S99/**待补** 的占位码。
            cur = _canon.get(e["scene"], e["scene"])
            e["scene"] = cur
            # **设备名以 materials 为准。** catalog 的 name 来自 Z03，
            # 而 materials 的 name 已换成飞书规范名（数智展示设备 →
            # 85寸一体机大屏（双系统）、收纳盒 → 收纳盒（鱼亮胸牌）…）。
            # 两处不一致，任何按名字建的键都会失配 —— 丙附的配置口径
            # 就是这么查不到的。名字是展示字段，键要用料号。
            _m = lark["materials"].get(e["material"])
            if _m and _m.get("name") and e.get("name") != _m["name"]:
                e["name"] = _m["name"]
            fs = _cs.get(e["material"])
            if not fs:
                continue
            if cur in fs:
                e["scene"] = cur
            elif len(fs) == 1:
                only = next(iter(fs))
                if only != e["scene"]:
                    _moved.append((e["name"], e["scene"], only))
                e["scene"] = only
            else:
                _amb.append((e["name"], e["scene"], sorted(fs)))
        # 归位后同名（场景×物料）的条目要合并，否则一个物料在同一场景下
        # 会出现两行，甲本 02 按场景分组时看起来像重复计列。
        if _moved:
            _mg: dict = {}
            for e in catalog:
                k = (e["scene"], e["material"])
                if k in _mg:
                    _p = _mg[k]
                    _p["placements"] += e["placements"]
                    for g, q in e["gardens"].items():
                        _p["gardens"][g] = _p["gardens"].get(g, 0) + q
                else:
                    _mg[k] = e
            catalog = list(_mg.values())
        # ---- 补齐飞书配置目录里有、目录里没有的（场景×物料）----
        # 飞书「2 配置目录」是（场景×物料）的**事实源**，catalog 应完整反映它。
        # 不补的后果实测过：device-config 的归位规则与这里的不同
        # （那边 len(fs)==1 就归，这边歧义时保持原样），两条路径落到不同场景，
        # 于是第二层引用了第一层没有的组合 —— 丙附的配置口径查不到，
        # 而查不到时若回落到「按设备名查」，显示的会是另一个场景的口径。
        # 补进来的条目**数量为空**：数量来自 Z03，这里只补「这个场景下可选这台设备」。
        _nm = {c: m["name"] for c, m in lark["materials"].items()}
        _exist = {(e["scene"], e["material"]) for e in catalog}
        _added = []
        for _code, _scs in _cs.items():
            if _code not in lark["materials"]:
                continue
            for _sc in _scs:
                if (_sc, _code) in _exist:
                    continue
                catalog.append({
                    "id": f"DEV.{_code}.{_sc}",
                    "scene": _sc,
                    "l1": (_new.get(_sc) or {}).get("l1", ""),
                    "material": _code,
                    "name": _nm.get(_code, _code),
                    "placements": [{
                        "note": "",
                        "rule": {"kind": "unparsed", "covered": False,
                                 "reason": "飞书配置目录有此组合，Z03 无对应行"},
                    }],
                    "gardens": {},
                    "from_lark_only": True,
                })
                _added.append((_sc, _nm.get(_code, _code)))
        lark_scene_moved, lark_scene_ambiguous = _moved, _amb
        lark_catalog_added = _added

        lm = lark["materials"]
        used = {e["material"] for e in catalog}
        # 各园表用到、飞书还没登记的料号：**列出来，不阻断**。
        # 这批实测全是单价 0 的待核价条目（7 条幼视宝算法源表无料号、
        # 2 条待申请料号、1 条料号栏塞了 4 个编码的脏数据），拦住整条链
        # 换不来任何金额上的正确性。而「飞书未登记」本来就该是一份**持续
        # 可见的清单**，不是一次性的阻断错误 —— 阻断只会逼人绕过守卫。
        # 它们保留 Z03 版本的条目，打上 lark_unregistered 标记随目录走。
        missing = sorted(used - set(lm))
        keep = {m["code"]: m for m in materials}
        for c in missing:
            if c in keep:
                keep[c] = {**keep[c], "lark_unregistered": True}
        lark_missing = [(c, (keep.get(c) or {}).get("name", "?"),
                         (keep.get(c) or {}).get("price_yuan") or 0)
                        for c in missing]
        merged = []
        for code, m in lm.items():
            old = keep.get(code) or {}
            # Z03 独有、飞书没有的诊断字段（字典冲突、canon 回填痕迹）留着 ——
            # 它们是血缘，飞书上没有对应列，丢了就查不回来了。
            merged.append({**{k: v for k, v in old.items()
                              if k in ("dict_flag", "canon_filled",
                                       "canon_diverge", "conflict")}, **m})
        for c in missing:                       # 未登记的原样带上，别丢
            if c in keep:
                merged.append(keep[c])
        materials = sorted(merged, key=lambda m: m["code"])
        _priced = [x for x in lark_missing if x[2]]
        if _priced:
            raise SystemExit(
                f"飞书未登记的物料里有 {len(_priced)} 条**已定价**："
                f"{[x[1] for x in _priced][:5]} —— 有价就会进金额，"
                f"而它的参数不在事实源里，表7 注3 的参考机型无从核。先在飞书登记。")

    return {"cost_ref": (lark or {}).get("cost_ref") or {},
            "lark_missing": lark_missing,
            "lark_scene_moved": lark_scene_moved,
            "lark_scene_ambiguous": lark_scene_ambiguous,
            "lark_catalog_added": lark_catalog_added,
            "scenes": scenes, "materials": materials, "catalog": catalog,
            "service_fee_catalog": fee_catalog,
            "mat_issues": mat_issues, "rows": rows, "code_of": code_of,
            "recon": {"gardens": len(src_q), "qty": sum(src_q.values()),
                      "money": src_money}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--source", type=Path)
    ap.add_argument("--no-lark", action="store_true",
                    help="不读飞书，只用 Z03 建目录。**会丢掉飞书上维护的物料** —— "
                         "仅用于飞书不可达时的应急核对，出的目录不要提交")
    a = ap.parse_args()
    root = a.root
    src = a.source or next((root / "raw-input").glob("*Z03_场景设备报价*.xlsx"))
    lark = None if a.no_lark else load_lark_devices(root)
    b = build(root, src, lark)
    out = root / "bom" / "devices"

    rel = src.relative_to(root) if src.is_relative_to(root) else src
    head = {"version": "0.1.0", "source": str(rel),
            "generated_by": "bom_build_devices.py"}
    (out / "taxonomy.yaml").write_text(yaml.safe_dump(
        {**head,
         "note": ("场景树。code 一经写入即为**声明值**，重建时沿用 —— "
                  "id 稳定性靠它，不靠遍历顺序。"),
         "scenes": b["scenes"]}, allow_unicode=True, sort_keys=False,
        default_flow_style=False, width=100), encoding="utf-8")
    if b.get("cost_ref"):
        (out / "cost-reference.yaml").write_text(yaml.safe_dump(
            {**head,
             "note": ("⚠⚠ **内部件：设备成本单价。严禁进入任何送审件。** ⚠⚠\n"
                      "用途只有一个：丙附「00_本商机报价」页给定价的人做参考。\n"
                      "**故意与 materials.yaml 分开存**：那个文件经 "
                      "read_device_config 一路通到甲附，成本价放进去就是给泄露"
                      "留一条随时会触发的路径 —— 已经发生过一次"
                      "「一列成本被标成『对外市场单价』推上飞书」的事故。\n"
                      "只有 device_quote_tool（出丙附）读本文件；"
                      "quote_generate_liuzhou 出甲/甲附/乙时不读，"
                      "read_hardware() 另有硬拦截拒绝成本类列名。"),
             "prices": b["cost_ref"]}, allow_unicode=True, sort_keys=False,
            default_flow_style=False, width=100), encoding="utf-8")
    (out / "materials.yaml").write_text(yaml.safe_dump(
        {**head,
         "note": ("物料主数据。参数不随场景变，故单独一处，catalog 只引编码。"
                  "**只含对外可用字段** —— 供应商/渠道价/成本价从未读入。"),
         "materials": b["materials"]}, allow_unicode=True, sort_keys=False,
        default_flow_style=False, width=100), encoding="utf-8")
    (out / "catalog.yaml").write_text(yaml.safe_dump(
        {**head,
         "note": ("（子场景 × 物料）目录。placements 是该场景下这台设备的配置位点，"
                  "rule.kind=unparsed 表示口径待业务侧补 —— **不猜**。"
                  "qty_by_garden 是从源表带过来的现状，第二层建成后应由规则推导。"),
         "entries": b["catalog"]}, allow_unicode=True, sort_keys=False,
        default_flow_style=False, width=100), encoding="utf-8")
    # 服务费的场景×园所数量。规则（触发设备/口径）在手工维护的
    # service-fees.yaml 里，本文件只放**源表带过来的现状数量** ——
    # 两者分开，是因为规则要人判、数量不能人改。
    if b.get("service_fee_catalog"):
        (out / "service-fee-catalog.yaml").write_text(yaml.safe_dump(
            {**head,
             "note": ("运营服务费的（场景 × 园所数量）现状，由 service-fees.yaml "
                      "声明的料号从源表拆出。**不进硬件设备购置费汇总** —— "
                      "柳州标准 三.(三) 运维为独立预算科目。"
                      "触发规则见 service-fees.yaml，数量应由规则派生后与本表核对。"),
             "entries": b["service_fee_catalog"]}, allow_unicode=True,
            sort_keys=False, default_flow_style=False, width=100),
            encoding="utf-8")

    k = Counter(p["rule"]["kind"] for e in b["catalog"] for p in e["placements"])
    print(f"设备 BOM 第一层 → {out}"
          + ("　（**--no-lark 应急模式**，物料仅来自 Z03）" if a.no_lark
             else "　物料/场景取自飞书「可售设备BOM」，数量取自 Z03"))
    print(f"  场景 {len(b['scenes'])} 个（99 表声明 {sum(1 for s in b['scenes'] if s['origin'] != '**未登记**：各园表在用但 99 表未声明')}）")
    if b.get("service_fee_catalog"):
        _fq = sum(v or 0 for e in b["service_fee_catalog"]
                  for v in e["gardens"].values())
        print(f"  运营服务费 {len(b['service_fee_catalog'])} 条目 / "
              f"{_fq:,.0f} 数量 → service-fee-catalog.yaml（**不计入硬件**）")
    if b.get("lark_missing"):
        print(f"  ⚠ 各园表用到、**飞书未登记** {len(b['lark_missing'])} 条"
              f"（全部单价 0、不进金额；飞书补齐后本行自动清零）：")
        for c, n_, _p in b["lark_missing"]:
            print(f"      {n_[:22]:<24}{c[:34]!r}")
    if b.get("lark_scene_moved"):
        print(f"  场景按飞书「2 配置目录」归位 {len(b['lark_scene_moved'])} 条：")
        for n_, a_, c_ in b["lark_scene_moved"][:8]:
            print(f"      {n_[:22]:<24}{a_[:16]:<18}→ {c_}")
        if len(b["lark_scene_moved"]) > 8:
            print(f"      …另有 {len(b['lark_scene_moved']) - 8} 条")
    if b.get("lark_catalog_added"):
        print(f"  按飞书配置目录补齐（场景×物料）{len(b['lark_catalog_added'])} 条"
              f"（数量为空，只补「该场景下可选这台设备」）：")
        for s_, n_ in b["lark_catalog_added"][:8]:
            print(f"      {s_[:14]:<16}{n_[:30]}")
        if len(b["lark_catalog_added"]) > 8:
            print(f"      …另有 {len(b['lark_catalog_added']) - 8} 条")
    if b.get("lark_scene_ambiguous"):
        print(f"  ⚠ 场景**归不了位** {len(b['lark_scene_ambiguous'])} 条"
              f"（飞书归多个场景，而源表那一格用不了）：")
        for n_, a_, f_ in b["lark_scene_ambiguous"][:6]:
            print(f"      {n_[:22]:<24}源表「{a_[:14]}」　飞书 {f_}")
    if b.get("cost_ref"):
        print(f"  成本参考 {len(b['cost_ref'])} 条 → cost-reference.yaml"
              f"（**内部件，只供丙附定价参考，不进任何送审件**）")
    print(f"  物料 {len(b['materials'])} 个"
          f"（待核价 {sum(1 for m in b['materials'] if m['price_status'].endswith('待核价**'))}，"
          f"字段冲突 {len(b['mat_issues'])}）")
    print(f"  目录条目 {len(b['catalog'])}（场景×物料），位点 {sum(len(e['placements']) for e in b['catalog'])}")
    print("  配置口径解析：" + "　".join(f"{a_}={c}" for a_, c in k.most_common()))
    rc = b["recon"]
    print(f"  对账通过：{rc['gardens']} 个园所/批次，{rc['qty']:,.0f} 台，"
          f"¥{rc['money']:,.2f}（与源表逐园所一致）")


if __name__ == "__main__":
    main()
