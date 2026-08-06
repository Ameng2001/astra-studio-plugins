"""lark_costsheet — 生成/推送某个省的《功能点计算书》Base。

与 `lark_worksheet` 的分工，是这套工具最关键的一条边界：

    lark_worksheet  →  BOM Base       地域无关。产品事实：有什么、多大、属哪类。
                                      方案人员在这里共创，改的是 BOM 本体。
    lark_costsheet  →  计算书 Base     地域专属。BOM × 某省标准包渲染出的送审材料。
                                      **只读派生物**，改它没有意义 —— 下次重推就覆盖。

为什么必须分开：BOM 里存 `app_type: 智能信息`（分类名），山东标准说它取 1.5、
广东标准同一概念叫「人工智能」也取 1.5。把 1.5 写进 BOM，换省就得改 BOM ——
而 BOM 恰恰是最不该随省份变的东西。raw-input 的 `参数设置` sheet 就是这么烂掉的：
标准参数、产品事实、项目决策三类混在一张表，换任何一个都要动同一处。

计算书不按子系统拆表：它是拿来读汇总、拿来送审的，不是拿来逐行编辑的。
拆 15 张表只会让「全项目多少 AFP」重新变成跨表手工加总。用分组视图代替。

表结构：
    0 区域参数表   本省的系数 + 元信息（标准包 id、计数方法）
    功能点计算书   1 行 = 1 个功能点，US 为公式列，引用本 Base 的参数表
    测算汇总       按 (系统, 开发类别) 分组的 UFP/AFP/工作量/费率/费用

用法：
    python3 lark_costsheet.py init  --bom <dir> --pack <dir> --folder <token> --out <cfg>
    python3 lark_costsheet.py push  --bom <dir> --pack <dir> --config <cfg>
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any

from bom_schema import Bom, BomItem, is_fp_counted
from costing_engine import PLACEHOLDER_TAG, CostingEngine, DealConfig
from nesma_weights import xlround
from standard_pack import StandardPack

BATCH = 200
TYPE_TO_SHEET = {"ELF": "EIF"}

#: 计算书列。与样例表《功能点计算书》对齐，外加条目ID 供回溯到 BOM。
DETAIL_FIELDS = [
    ("条目ID", "text"), ("子系统", "text"),
    ("一级模块", "text"), ("二级模块", "text"),
    ("三级模块", "text"), ("四级模块", "text"),
    ("功能点计数项名称", "text"), ("类别", "text"),
    ("UFP", "number"), ("应用类型", "text"),
    ("复用度", "text"), ("修改类型", "text"),
    ("备注", "text"),
]


def lark(*args: str) -> dict[str, Any]:
    r = subprocess.run(["lark-cli", *args, "--format", "json"],
                       capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": {"raw": (r.stdout or r.stderr)[:300]}}


def _tables(bt: str) -> dict[str, str]:
    """表名 → table_id。

    注意返回体：`data.tables[]` 里的键是 `id` / `name`，**不是**
    `items[].table_id`。猜键名会让「表已存在」被误判成「建表失败」，
    然后重复建 —— 这个 Base 的第一版就是这么多出来的。
    """
    r = lark("base", "+table-list", "--base-token", bt, "--as", "user")
    return {x["name"]: x["id"] for x in (r.get("data") or {}).get("tables", [])}


def _fields(bt: str, tid: str) -> dict[str, dict]:
    r = lark("base", "+field-list", "--base-token", bt, "--table-id", tid, "--as", "user")
    return {f["name"]: f for f in (r.get("data") or {}).get("fields", [])}


def _wait_field(bt: str, tid: str, name: str, tries: int = 10) -> dict[str, dict]:
    """建字段返回 ok 后 field-list 未必立刻可见 —— 飞书这边是最终一致的。"""
    for _ in range(tries):
        f = _fields(bt, tid)
        if name in f:
            return f
        time.sleep(1)
    raise RuntimeError(f"{name} 已建但 {tries} 秒内未可见")


# ---- 参数表内容 --------------------------------------------------------


def param_rows(pack: StandardPack, counting_method: str) -> list[dict[str, Any]]:
    """把标准包的系数摊平成参数表。**每一行都带 citation** —— 财评逐条可查。"""
    def cite(node: dict) -> str:
        c = node.get("citation") or {}
        return f"{pack.pack_id} {c.get('section', '')} p.{c.get('page', '')}".strip()

    rows = [
        {"参数类别": "元信息", "参数名": "标准包",
         "依据": f"{pack.pack_id} | {pack.data['standard_doc']}"},
        {"参数类别": "元信息", "参数名": "计数方法",
         "依据": f"{counting_method} | 决定功能点权重档位与规模变更因子档位"},
        {"参数类别": "元信息", "参数名": "数据来源",
         "依据": "本 Base 由 BOM（地域无关）× 本标准包渲染生成，是**只读派生物**。"
                 "改这里没有意义，下次重推即覆盖；要改产品事实请改 BOM Base。"},
    ]
    f = pack.data["factors"]
    weights = pack.data["fp_counting"][counting_method]["weights"]
    wc = pack.data["fp_counting"][counting_method].get("citation") or {}
    for k, v in weights.items():
        rows.append({"参数类别": "功能点权重",
                     "参数名": TYPE_TO_SHEET.get(k, k), "取值": v,
                     "依据": f"{pack.pack_id} {wc.get('section','')} p.{wc.get('page','')}"})
    for cat, key in (("规模变更因子", "size_change"), ("复用度系数", "reuse"),
                     ("应用类型系数", "app_type"), ("开发类别系数", "dev_category")):
        node = f.get(key) or {}
        for name, v in (node.get("values") or {}).items():
            rows.append({"参数类别": cat, "参数名": name, "取值": v,
                         "依据": cite(node)})
    rows.append({"参数类别": "修改类型系数", "参数名": "新增", "取值": 1,
                 "依据": "样例表 模板使用说明&基础参数；本项目全部新建，恒为 1"})
    rows.append({"参数类别": "修改类型系数", "参数名": "修改", "取值": 0.8,
                 "依据": "同上"})
    rows.append({"参数类别": "修改类型系数", "参数名": "删除", "取值": 0.2,
                 "依据": "同上"})
    return rows


def us_expression(pack: StandardPack, counting_method: str,
                  param_table_name: str = "0 区域参数表") -> str:
    """按标准包的公式档案拼 US 表达式。

    **不同省是公式结构不同，不是取值不同。** 山东 = UFP × 规模变更 × 复用 ×
    应用类型；广东没有规模变更这一环。所以这里按 pack 里该因子是否存在来决定
    要不要乘，而不是给广东塞一个 1.0 蒙混过去 —— 塞 1.0 能算对，但计算书上
    会白纸黑字多出一列本省标准里根本不存在的因子，财评一眼就问住。
    """
    def lookup(cat: str, key: str) -> str:
        return (f'SUM([{param_table_name}].FILTER(AND('
                f'CurrentValue.[参数类别]="{cat}",'
                f'CurrentValue.[参数名]={key})).[取值])')

    parts = ["[UFP]"]
    f = pack.data["factors"]
    if (f.get("size_change") or {}).get("values", {}).get(counting_method, 1) != 1:
        parts.append(lookup("规模变更因子", f'"{counting_method}"'))
    parts.append(lookup("复用度系数", "[复用度]"))
    parts.append(lookup("修改类型系数", "[修改类型]"))
    parts.append(lookup("应用类型系数", "[应用类型]"))
    # 逐条舍入到 2 位，与引擎 xlround(afp, 2) 对齐。不加这层，AI 类子系统
    # 每个 EO 差 0.005（5×1.21×1.5=9.075 vs 9.08），200+ 行就差出零头，
    # 财评拿计算书对测算表时对不上。
    return "ROUND(" + "*".join(parts) + ",2)"


# ---- 行数据 ------------------------------------------------------------


def detail_rows(bom: Bom, pack: StandardPack,
                reuse_level: str = "新建") -> list[dict[str, Any]]:
    """一行一个功能点。应用类型取 BOM 的分类名，必要时按标准包词表映射。

    山东叫「智能信息」，广东同一概念叫「人工智能」。BOM 存前者，
    渲染到广东计算书时映射成后者 —— BOM 一个字不用改，这正是分层的兑现。

    **复用度的词表也是各省自己的。** 样例表用「高/中/低」，那是广东系的说法；
    山东标准的键是「新建 / 升级改造_新增数据功能 / 升级改造_既有功能优化完善」。
    照抄样例表的「低」写进山东计算书，参数表里查不到这一行，FILTER 落空得 0，
    整列 US 直接塌成 0 —— 而且不报任何错。所以取值必须来自 pack 本身。
    """
    reuse_vals = (pack.data["factors"].get("reuse") or {}).get("values") or {}
    if reuse_level not in reuse_vals:
        raise RuntimeError(
            f"复用度 {reuse_level!r} 不在 {pack.pack_id} 的词表 {sorted(reuse_vals)} 中")
    alias = ((pack.data["factors"].get("app_type") or {}).get("aliases") or {})
    out = []
    for i in bom.active():
        if not is_fp_counted(i):
            continue
        app = i.app_type or "业务处理"
        out.append({
            "条目ID": i.id, "子系统": i.path.system,
            "一级模块": i.path.l1 or "", "二级模块": i.path.l2 or "",
            "三级模块": i.path.l3 or "", "四级模块": i.path.l4 or "",
            "功能点计数项名称": i.name,
            "类别": TYPE_TO_SHEET.get(i.nesma.type, i.nesma.type),
            "UFP": pack.fp_weight("估算功能点法", i.nesma.type),
            "应用类型": alias.get(app, app),
            "复用度": reuse_level, "修改类型": "新增",
            "备注": "【占位待确认】不计入报价" if PLACEHOLDER_TAG in i.tags else "",
        })
    return out


def summary_rows(bom: Bom, pack: StandardPack, counting_method: str) -> list[dict[str, Any]]:
    """测算汇总 —— 直接用造价引擎算，不在飞书里重算一遍。

    飞书公式重算一份、引擎算一份，两份迟早会漂。汇总以引擎为准，
    明细行的 US 公式只是给人逐条核验用的。
    """
    deal = DealConfig("worksheet", counting_method=counting_method)
    eng = CostingEngine(bom, pack, deal)
    items, _ = eng.in_scope()
    out = []
    for s in eng.software_dev(items):
        out.append({
            "系统": s.system, "开发类别": s.dev_category, "条目数": s.items,
            "未调整功能点": s.ufp, "调整后功能点": s.afp,
            "开发工作量（人月）": s.effort_man_months,
            "人月费率（元）": s.man_month_rate,
            "软件开发费用（元）": s.cost,
            "类型分布": json.dumps(s.by_type, ensure_ascii=False),
        })
    return out


def check_vocabulary(details: list[dict[str, Any]],
                     params: list[dict[str, Any]]) -> None:
    """明细里用到的每个查表键，参数表里必须真有对应行。

    这是这套东西最容易静默出错的地方：FILTER 查不到只会返回空数组，
    SUM 得 0，整列 US 塌成 0 —— 飞书不报错、公式也不标红，看上去就是
    「算出来是 0」。实测山东计算书照抄样例表的复用度「低」，1763 行
    全部 US=0 才被数字核对发现。宁可在写入前硬失败。
    """
    have: dict[str, set[str]] = {}
    for r in params:
        have.setdefault(str(r["参数类别"]), set()).add(str(r["参数名"]))
    missing: dict[str, set[str]] = {}
    for r in details:
        for col, cat in (("类别", "功能点权重"), ("应用类型", "应用类型系数"),
                         ("复用度", "复用度系数"), ("修改类型", "修改类型系数")):
            v = str(r.get(col, ""))
            if v and v not in have.get(cat, set()):
                missing.setdefault(cat, set()).add(v)
    if missing:
        raise RuntimeError(
            "词表对不上，写入会让 US 静默塌成 0：\n" + "\n".join(
                f"  参数表「{k}」缺 {sorted(v)}（现有 {sorted(have.get(k, []))}）"
                for k, v in missing.items()))


# ---- 建 Base -----------------------------------------------------------


def _find_base(folder: str, name: str) -> str | None:
    """按名字在文件夹里找 Base。

    列文件是 `drive files list`（原生 API 资源），**不是** `drive +list` ——
    后者不存在。这个查重是防重复建 Base 的唯一屏障，它静默失败一次就会
    多出一个同名 Base，所以查不到时要能区分「真没有」和「命令用错了」。
    """
    r = lark("drive", "files", "list", "--folder-token", folder, "--as", "user")
    if not r.get("ok"):
        raise RuntimeError(f"列文件夹失败，无法查重，拒绝建 Base：{r.get('error')}")
    d = r.get("data") or {}
    for f in (d.get("files") or d.get("items") or []):
        if f.get("type") == "bitable" and f.get("name") == name:
            return f.get("token")
    return None


def init(bom: Bom, pack: StandardPack, folder: str, counting_method: str) -> dict[str, Any]:
    name = f"功能点计算书（{pack.data.get('region_label') or pack.pack_id}）"

    # 先查重再建。建资源类命令会在 JSON 前打一行散文，解析崩**不代表失败** ——
    # 之前就是据此重跑，建出了两份文件夹和两个 Base。
    bt = _find_base(folder, name)
    if bt:
        print(f"  复用已存在的 Base {name} {bt}")
    else:
        r = lark("base", "+base-create", "--name", name, "--folder-token", folder, "--as", "user")
        bt = _find_base(folder, name)
        if not bt:
            raise RuntimeError(f"建 Base 失败：{r.get('error')}")

    tables: dict[str, str] = {}
    for tname, fields in (
        ("0 区域参数表", [("参数类别", "text"), ("参数名", "text"),
                          ("取值", "number"), ("依据", "text")]),
        ("功能点计算书", DETAIL_FIELDS),
        ("测算汇总", [("系统", "text"), ("开发类别", "text"), ("条目数", "number"),
                      ("未调整功能点", "number"), ("调整后功能点", "number"),
                      ("开发工作量（人月）", "number"), ("人月费率（元）", "number"),
                      ("软件开发费用（元）", "number"), ("类型分布", "text")]),
    ):
        # +table-create 要 --name + --fields（JSON 数组）。这里的 `--json` 是
        # 「输出格式简写」，不是载荷 —— 传载荷会被当成位置参数直接报错。
        got = _tables(bt)
        if tname not in got:
            c = lark("base", "+table-create", "--base-token", bt, "--as", "user",
                     "--name", tname,
                     "--fields", json.dumps([{"name": n, "type": ty}
                                             for n, ty in fields], ensure_ascii=False))
            got = _tables(bt)          # 回查，不信返回体
            if tname not in got:
                raise RuntimeError(f"建表 {tname} 失败：{c.get('error')}")
        tables[tname] = got[tname]

    # US 公式列必须在参数表有数据之后建，否则公式校验期查不到引用的行
    _push_table(bt, tables["0 区域参数表"], param_rows(pack, counting_method))
    expr = us_expression(pack, counting_method)
    if "US" in _fields(bt, tables["功能点计算书"]):
        return {"base_token": bt, "base_name": name, "pack_id": pack.pack_id,
                "counting_method": counting_method, "reuse_level": "新建",
                "tables": tables,
                "note": "只读派生物：BOM（地域无关）× 本标准包渲染。"}
    c = lark("base", "+field-create", "--base-token", bt, "--table-id", tables["功能点计算书"],
             "--as", "user", "--i-have-read-guide",
             "--json", json.dumps({
                 "name": "US", "type": "formula", "expression": expr,
                 "description": "调整后功能点。系数全部查「0 区域参数表」，"
                                "改系数只改参数表一处。逐条舍入 2 位与造价引擎一致。",
             }, ensure_ascii=False))
    if not c.get("ok"):
        raise RuntimeError(f"建 US 公式列失败：{c.get('error')}")
    _wait_field(bt, tables["功能点计算书"], "US")

    return {"base_token": bt, "base_name": name, "pack_id": pack.pack_id,
            "counting_method": counting_method, "reuse_level": "新建",
            "tables": tables,
            "note": "只读派生物：BOM（地域无关）× 本标准包渲染。改这里没有意义，"
                    "下次 push 即覆盖；产品事实请改 BOM Base。"}


# ---- push --------------------------------------------------------------


def _push_table(bt: str, tid: str, rows: list[dict[str, Any]]):
    while True:
        r = lark("base", "+record-list", "--base-token", bt, "--table-id", tid,
                 "--as", "user", "--limit", str(BATCH))
        ids = (r.get("data") or {}).get("record_id_list", [])
        if not ids:
            break
        d = lark("base", "+record-delete", "--base-token", bt, "--table-id", tid,
                 "--as", "user", "--yes",
                 "--json", json.dumps({"record_id_list": ids}))
        if not d.get("ok"):
            raise RuntimeError(f"清表失败：{d.get('error')}")
    written = 0
    for s in range(0, len(rows), BATCH):
        r = lark("base", "+record-batch-create", "--base-token", bt, "--table-id", tid,
                 "--as", "user",
                 "--json", json.dumps({"create_records": rows[s:s + BATCH]},
                                      ensure_ascii=False))
        if not r.get("ok"):
            raise RuntimeError(f"写入失败：{r.get('error')}")
        written += len(r["data"].get("record_id_list", []))
    return written


def push(bom: Bom, pack: StandardPack, cfg: dict[str, Any]) -> dict[str, Any]:
    if cfg.get("pack_id") != pack.pack_id:
        raise RuntimeError(
            f"标准包不一致：配置声明 {cfg.get('pack_id')}，传入的是 {pack.pack_id}。"
            f"换省不是改参数表的数字 —— US 的公式结构本身不同，须另建一个 Base。")
    bt, t = cfg["base_token"], cfg["tables"]
    cm = cfg["counting_method"]
    reuse = cfg.get("reuse_level", "新建")
    params, details = param_rows(pack, cm), detail_rows(bom, pack, reuse)
    check_vocabulary(details, params)
    n_p = _push_table(bt, t["0 区域参数表"], params)
    n_d = _push_table(bt, t["功能点计算书"], details)
    n_s = _push_table(bt, t["测算汇总"], summary_rows(bom, pack, cm))
    return {"ok": True, "参数": n_p, "计算书": n_d, "汇总": n_s,
            "bom_version": bom.version, "pack_id": pack.pack_id}


def main() -> None:
    ap = argparse.ArgumentParser(description="生成/推送某省的《功能点计算书》Base")
    ap.add_argument("action", choices=["init", "push"])
    ap.add_argument("--bom", required=True, type=Path)
    ap.add_argument("--pack", required=True, type=Path)
    ap.add_argument("--folder", help="init：目标文件夹 token")
    ap.add_argument("--config", type=Path, help="push：计算书 Base 配置")
    ap.add_argument("--out", type=Path, help="init：配置写到哪")
    ap.add_argument("--counting-method", default="估算功能点法")
    args = ap.parse_args()

    bom = Bom.load(args.bom)
    pack = StandardPack.load(args.pack)

    if args.action == "init":
        cfg = init(bom, pack, args.folder, args.counting_method)
        r = push(bom, pack, cfg)
        cfg["pushed_bom_version"] = bom.version
        out = args.out or (args.bom / f"lark-costsheet-{pack.pack_id}.json")
        out.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        print(f"建成 {cfg['base_name']}　{cfg['base_token']}")
        print(f"  参数 {r['参数']} 行　计算书 {r['计算书']} 行　汇总 {r['汇总']} 行")
        print(f"  配置 → {out}")
    else:
        cfg = json.loads(args.config.read_text(encoding="utf-8"))
        r = push(bom, pack, cfg)
        print(f"push 成功：参数 {r['参数']} / 计算书 {r['计算书']} / 汇总 {r['汇总']} 行"
              f"（BOM v{bom.version} × {pack.pack_id}）")


if __name__ == "__main__":
    main()
