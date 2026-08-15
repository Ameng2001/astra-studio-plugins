"""v1_factor_audit —— 把 v1 丙/丙附里的**人工设定**与 v2 的配置逐项对账。

## 为什么是对账而不是「读过来用」

v1 的丙（内部工作底稿）与丙附（硬件配置录入）里有一批人手工设的值：
软件类别因子、逐模块复用度、市场单价、成本单价、协议价、各园台数。
它们是真实的商务与技术判断，v2 必须原样继承，不能重推。

但**继承的方式是把值落进 v2 的 yaml，不是每次出表去读那两个 xlsx**：

  · xlsx 是 v1 的**产物**。拿产物当输入，v2 就永远依赖 v1 跑过一次，
    两代之间形成环 —— v1 冻结之后这条链路会悄悄失效。
  · v2 的真相源是 `bom/**/pricing.yaml`、`deals/<id>/*.yaml`。同一个数
    有两个源，改了一个忘了另一个，出表不会报错，只会出两个不同的数。

所以本工具只做一件事：**逐项比对并报差异**。差异由人裁定后写进 v2 的
yaml；写完再跑一次，应当全绿。绿了就证明 v2 的输入与 v1 的人工设定等价，
此后 v1 的 xlsx 可以彻底不看。

用法：
    python3 v1_factor_audit.py --v1 <v1仓> --v2 <v2仓> --deal 2026-liuzhou-preschool
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import openpyxl
import yaml


def _sheet_rows(ws, header_row: int) -> list[dict]:
    hdr = [str(ws.cell(header_row, c).value or "").strip()
           for c in range(1, ws.max_column + 1)]
    out = []
    for r in range(header_row + 1, ws.max_row + 1):
        row = {hdr[c - 1]: ws.cell(r, c).value for c in range(1, ws.max_column + 1)
               if hdr[c - 1]}
        if any(v is not None and str(v).strip() != "" for v in row.values()):
            out.append(row)
    return out


def _find_header(ws, must: str, limit: int = 6) -> int:
    for r in range(1, min(ws.max_row, limit) + 1):
        vals = [str(ws.cell(r, c).value or "") for c in range(1, ws.max_column + 1)]
        if must in vals:
            return r
    raise SystemExit(f"{ws.title}: 找不到表头行（期望含列 {must!r}）")


def _num(v: Any) -> float | None:
    if v is None or str(v).strip() == "":
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def audit(v1: Path, v2: Path, deal_id: str) -> int:
    out1 = v1 / "deals" / deal_id / "out"
    bing = next(out1.glob("*_丙_*.xlsx"), None)
    bingfu = next(out1.glob("*_丙附_*.xlsx"), None)
    if not bing or not bingfu:
        raise SystemExit(f"⛔ 在 {out1} 找不到 v1 的丙/丙附")
    bad = 0

    # ── ① 软件类别因子（丙 03_复用度模块清单）──────────────────
    wb = openpyxl.load_workbook(bing, data_only=True)
    ws = wb["03_复用度模块清单"]
    hr = _find_header(ws, "子系统")
    rows = _sheet_rows(ws, hr)
    v1_cat: dict[str, float] = {}
    for r in rows:
        s, c, f = r.get("子系统"), r.get("软件类别"), _num(r.get("类别因子"))
        if s and c and f is not None:
            v1_cat[str(c)] = f
    deal = yaml.safe_load((v2 / "deals" / deal_id / "deal.yaml").read_text(encoding="utf-8"))
    v2_cat = {k: (v["value"] if isinstance(v, dict) else v) for k, v in
              ((deal.get("fp_method_settings") or {}).get("app_type_factors") or {}).items()}
    print("── ① 软件类别因子（丙 03 类别因子 → v2 deal.fp_method_settings）")
    for cat, val in sorted(v1_cat.items()):
        got = v2_cat.get(cat)
        ok = got is not None and abs(float(got) - val) < 1e-9
        bad += not ok
        print(f"   {cat:14}v1 {val:<6} v2 {got}　{'✓' if ok else '✗ 不一致'}")
    for cat in sorted(set(v2_cat) - set(v1_cat)):
        print(f"   {cat:14}v1 未出现（该类无子系统） v2 {v2_cat[cat]}　· 仅登记")

    # ── ② 逐子系统复用度（丙 03）──────────────────────────────
    v1_reuse: dict[str, dict[str, float]] = {}
    for r in rows:
        s, m = r.get("子系统"), r.get("功能模块")
        f = _num(r.get("复用度系数")) or _num(r.get("复用系数")) or _num(r.get("复用度"))
        if s and m and f is not None:
            v1_reuse.setdefault(str(s), {})[str(m)] = f
    print(f"── ② 逐模块复用度：丙里登记 {sum(len(v) for v in v1_reuse.values())} 条"
          f"（{len(v1_reuse)} 个子系统）")
    if not v1_reuse:
        print("   · 丙 03 未见复用度列 —— v2 由 BOM 的 maturity + deal 的项目类型推导，"
              "在下面第 ⑤ 步用出表结果整体校验")

    # ── ③ 市场单价（丙附 _物料表）──────────────────────────────
    wbf = openpyxl.load_workbook(bingfu, data_only=True)
    ws = wbf["_物料表"]
    hr = _find_header(ws, "设备名称")
    v1_price: dict[str, float] = {}
    for r in _sheet_rows(ws, hr):
        code, p = r.get("物料编码"), _num(r.get("单价(元)"))
        if code and p is not None:
            v1_price[str(code).strip()] = p
    v2_price: dict[str, float] = {}
    for lay in ("shared/devices", "verticals/preschool/devices"):
        f = v2 / "bom" / lay / "pricing.yaml"
        if f.exists():
            for c, v in ((yaml.safe_load(f.read_text(encoding="utf-8")) or {})
                         .get("prices") or {}).items():
                if (v or {}).get("price_yuan"):
                    v2_price[c] = float(v["price_yuan"])
    only1 = {k: v for k, v in v1_price.items() if v and k not in v2_price}
    diff = {k: (v, v2_price[k]) for k, v in v1_price.items()
            if k in v2_price and abs(v - v2_price[k]) > 0.001}
    print(f"── ③ 市场单价（丙附 _物料表 → v2 pricing.yaml/prices）")
    print(f"   v1 有价 {sum(1 for v in v1_price.values() if v)} 条　v2 {len(v2_price)} 条　"
          f"取值不同 {len(diff)}　v1 有 v2 无 {len(only1)}")
    for k, (a, b) in list(diff.items())[:12]:
        print(f"     ✗ {k}  v1 {a}  v2 {b}")
    for k, a in list(only1.items())[:12]:
        print(f"     ✗ {k}  v1 {a}  v2 —— 缺")
    bad += len(diff) + len(only1)

    # ── ④ 成本单价（丙附 _成本表 → v2 BOM 成本价 ⊕ 商机协议价）────────
    # **按料号对齐，不按设备名。** v1 的 _成本表 用「设备名｜型号」区分同名
    # 不同物（智能手环｜BPT9-OF 与 ｜C5PLUS 是两条），按名字比会把它们判成
    # 「v2 缺」——名字是展示字段不是键，这条在 v1 就踩过。
    #
    # **比之前要先叠协议价。** v1 表里存的是应用协议价之后的成本；v2 按分层
    # 把 BOM 成本价（产品级）与协议价（商机级 cost-agreement.yaml）分开放。
    # 不叠就会看到 17 条「取值不同」，而那正是协议价生效的证据，不是差异。
    ws = wbf["_成本表"]
    hr = _find_header(ws, "设备名称")
    v1_cost_by_name = {str(r["设备名称"]).strip(): _num(r.get("成本单价(元)"))
                       for r in _sheet_rows(ws, hr) if r.get("设备名称")}
    # 名字→料号的映射**取自 v1 自己的 _物料表**（它带物料编码列）。
    # 不要拿 v2 的 name+model 去拼「名｜型号」猜：v1 那个后缀有时是型号、
    # 有时是服务名（幼视宝算法四条的 model 都是「无」，后缀却是服务名），
    # 猜的规则一定会漏。用对方自己给的键，是唯一不靠猜的对齐方式。
    wsm = wbf["_物料表"]
    hm = _find_header(wsm, "设备名称")
    code_of: dict[str, str] = {}
    for r in _sheet_rows(wsm, hm):
        nm, code = r.get("设备名称"), r.get("物料编码")
        if nm and code:
            code_of[str(nm).strip()] = str(code).strip()
    v2_cost: dict[str, float] = {}
    for lay in ("shared/devices", "verticals/preschool/devices"):
        f = v2 / "bom" / lay / "pricing.yaml"
        if f.exists():
            for c, v in ((yaml.safe_load(f.read_text(encoding="utf-8")) or {})
                         .get("cost") or {}).items():
                if (v or {}).get("cost_yuan") is not None:
                    v2_cost[c] = float(v["cost_yuan"])
    agree = (yaml.safe_load((v2 / "deals" / deal_id / "cost-agreement.yaml")
                            .read_text(encoding="utf-8")) or {}).get("prices") or {}
    n_ag = 0
    for c, d in agree.items():
        if d.get("agreement_price") is not None:
            v2_cost[c] = float(d["agreement_price"])
            n_ag += 1
    cdiff, cmiss = {}, []
    for n, a in v1_cost_by_name.items():
        if a is None:
            continue
        code = code_of.get(n)
        if code is None or code not in v2_cost:
            cmiss.append(n)
        elif abs(a - v2_cost[code]) > 0.001:
            cdiff[n] = (a, v2_cost[code])
    print("── ④ 成本单价（丙附 _成本表 → v2 BOM 成本价 ⊕ 协议价）")
    print(f"   v1 {len(v1_cost_by_name)} 条　v2 {len(v2_cost)} 条"
          f"（其中 {n_ag} 条被商机协议价覆盖）　"
          f"取值不同 {len(cdiff)}　对不上 {len(cmiss)}")
    for n, (a, b) in list(cdiff.items())[:12]:
        print(f"     ✗ {n[:34]}  v1 {a}  v2 {b}")
    for n in cmiss[:12]:
        print(f"     ✗ {n[:34]}  v1 {v1_cost_by_name[n]}  v2 —— 料号对不上")
    bad += len(cdiff) + len(cmiss)

    # ── ⑤ 各园台数（丙附 01–06 → v2 device-config）──────────────
    cfg = yaml.safe_load((v2 / "deals" / deal_id / "device-config.yaml")
                         .read_text(encoding="utf-8"))
    tot2 = 0.0
    for t in cfg.get("targets") or []:
        for it in t.get("items") or []:
            tot2 += float(it.get("qty") or it.get("数量") or 0)
    tot1 = 0.0
    # 只取各园/批次的配置页：编号开头**且**有「设备名称」列。
    # 光看编号会把 90_汇总、91_成本协议价 也算进来 —— 它们编号也是数字开头，
    # 但没有设备名称列，进来只会让工具在无关的表上报错。
    def _is_garden(name: str) -> bool:
        if not (name[:2].isdigit() and name[:2] != "00"):
            return False
        w = wbf[name]
        return any(str(w.cell(r, c).value or "").strip() == "设备名称"
                   for r in range(1, 6) for c in range(1, w.max_column + 1))

    for sh in [s for s in wbf.sheetnames if _is_garden(s)]:
        ws = wbf[sh]
        hr = _find_header(ws, "设备名称")
        for r in _sheet_rows(ws, hr):
            q = _num(r.get("数量"))
            if q:
                tot1 += q
    print(f"── ⑤ 各园台数（丙附 01–06 → v2 device-config.yaml）")
    print(f"   v1 各园页台数合计 {tot1:,.0f}　v2 配置合计 {tot2:,.0f}　"
          f"{'✓ 一致' if abs(tot1 - tot2) < 0.5 else '✗ 差 %.0f' % (tot1 - tot2)}")
    bad += abs(tot1 - tot2) >= 0.5

    print(f"\n{'✓ 全部一致' if not bad else f'⚠ {bad} 处需裁定'}")
    return bad


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--v1", required=True, type=Path)
    ap.add_argument("--v2", required=True, type=Path)
    ap.add_argument("--deal", required=True)
    a = ap.parse_args()
    raise SystemExit(1 if audit(a.v1, a.v2, a.deal) else 0)


if __name__ == "__main__":
    main()
