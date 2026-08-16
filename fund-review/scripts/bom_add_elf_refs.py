"""为某个子系统补建「逻辑文件」模块（ELF 引用），供 G-04 触发后使用。

**一次性工具**：本项目 0.18.0 用它给 3.3 站长智助补了 12 条 ELF。
换子系统时改 `SYSTEM` / `FILE` / `REFS` 三处即可 —— REFS 是人判的，
不自动推断：引用关系要看得懂功能点描述才能定，脚本只负责把判断落成条目
并把依据写进去。

用法：
    BOM_PROJECT_ROOT=<项目目录> python3 bom_add_elf_refs.py [--apply]

---

原始场景：3.3 站长智助补建，并修复改判遗留的 rationale 矛盾。

## 为什么是 ELF 不是 ILF

站长智助是站点管理的**驾驶舱**：43 条功能点里 EO/EQ 占 36 条，全是汇总、
统计、展示、下钻。它不产生自己的主数据 —— 长者档案由 3.1 维护、服务执行
记录由 3.4 维护、订单由生态中台维护。按刚裁定的规则（一个维护方记 ILF，
其余记 ELF），它引用的都该记 ELF。

此前它 ILF 与 ELF 都是 0，而 EO/EQ 按定义必须引用逻辑文件 —— 那批功能点
在计数上等于凭空产出数据。兄弟子系统 3.1(28)/3.2(11)/3.4(17) 都建了
「逻辑文件」模块，只有 3.3 没有，是漏建不是边界选择。

## 收录门槛

**每个逻辑文件至少要有 2 条功能点明确引用**，且引用关系写进 rationale。
只被 1 条引用的不收 —— 那更可能是筛选维度而不是独立数据组。
（据此排除了「空间与楼宇」：仅 0042 按楼层/房间/床位查看这一处。）

宁可少收也不多收：多收一条就是 +7 UFP ≈ ¥9k，方向上与刚修掉的重复计列
是同一类错误。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

ROOT = Path(os.environ.get("BOM_PROJECT_ROOT",
                           "/Users/liuyameng/Codes/Fund-engineering/clife-elderly-care"))
SYSTEM = "多角色业务应用-三、站点机构运营端：3.3服务站点站长智助（APP，平板端应用）"
FILE = ROOT / "bom/items/多角色业务应用-三-站点机构运营端-3.3服务站点站长智助-APP-平板端应用.yaml"
VERSION = "0.18.0"

#: (逻辑文件名, 维护方关键词, [引用它的功能点尾号], 引用说明)
REFS = [
    ("长者档案", "3.1机构站点管理系统",
     ["0005", "0013", "0017", "0024", "0025", "0026", "0027", "0028", "0029"],
     "档案中心统计、人员筛选/搜索、档案视图、新建档案、活跃长者数、重点关注名单"),
    ("服务工单", "3.1机构站点管理系统", ["0032", "0033", "0034"],
     "工单列表按状态展示、工单搜索、工单详情与执行回执"),
    ("评估表与评估记录", "3.1机构站点管理系统", ["0004", "0023", "0039"],
     "智能评估次数与维度分布、按长者展示累计评估、体适能评估结果"),
    ("活动与签到", "3.1机构站点管理系统", ["0006", "0037", "0043"],
     "站点来访对比、来访签到场景汇总与扫码签到、活动参与与热门活动"),
    ("订单", "生态运营与交易中台", ["0011", "0012", "0035", "0036"],
     "订单列表与详情、本月已完成订单营收汇总、AI 预估月收入"),
    ("服务执行记录", "3.4服务站点护工智助", ["0003", "0020", "0022"],
     "当日服务人数/时长/完成情况、SOP 标准执行率、服务时长与满意度交叉分析"),
    ("体征与用药记录", "3.4服务站点护工智助", ["0007", "0018", "0038"],
     "按长者归集健康监测记录与指标变化、体征平稳统计、健康测量场景结果"),
    ("异常事件", "3.4服务站点护工智助", ["0001", "0016", "0030"],
     "健康风险/服务异常/待处理事件汇总、有风险长者统计、消息中心事件跟踪"),
    ("康复训练计划与记录", "3.4服务站点护工智助", ["0010", "0040", "0041"],
     "科学运动服务方案与跟踪、科学运动场景、认知训练场景"),
    ("个体化护理方案", "3.4服务站点护工智助", ["0008", "0009"],
     "营养评估与膳食方案的确认状态与执行记录、睡眠筛查到疗愈的服务闭环"),
    ("服务评价", "二、机构区域运营端", ["0021", "0022"],
     "近 7 天服务评价满意度分布、服务时长与满意度交叉分析"),
    ("商品", "二、机构区域运营端", ["0015", "0035"],
     "按商品/站点服务/上门服务拆分本月收入、订单列表中的商品服务信息"),
]

#: 引用数不足 2、故意不收的 —— 写下来是为了让「为什么不收」也可复核
EXCLUDED = {
    "空间与楼宇": "仅 0042（睡眠照护场景按楼层/房间/床位查看）一处引用，"
                  "更像筛选维度而非独立数据组；多收一条就是 +7 UFP，宁可少收",
}


def fix_stale_rationale(docs: dict[Path, dict]) -> int:
    """改判 ELF 时只改了 type，rationale 还写着「判为 ILF」—— 自相矛盾。

    这条矛盾比原来的重复计列更难发现：类型对了，理由却在说另一件事，
    而评审读的正是理由。
    """
    n = 0
    for f, d in docs.items():
        hit = False
        for it in d["items"]:
            ns = it.get("nesma") or {}
            if ns.get("logical_file_role") != "reference":
                continue
            r = ns.get("rationale") or ""
            if "判为 ILF" not in r:
                continue
            ns["rationale"] = r.replace(
                "判为 ILF：系统边界内维护的、用户可识别的业务数据组（表6.1 p.17）。",
                "判为 ELF：本系统引用、由他方维护的用户可识别业务数据组（表6.1 p.17）。"
                "原判 ILF 系未区分维护方与引用方，0.18.0 按「一份数据组只有一个维护方」改判。"
            ).replace(
                "由 ", "维护方由 ", 1) if "判为 ILF：系统边界内维护的" in r else (
                "判为 ELF（0.18.0 改判）：本系统引用、由他方维护的逻辑数据组。" + r)
            n += 1
            hit = True
        if hit:
            f.write_text(yaml.dump(d, allow_unicode=True, sort_keys=False, width=200),
                         encoding="utf-8")
    return n


def main(dry: bool) -> int:
    files = sorted((ROOT / "bom/items").glob("*.yaml"))
    docs = {f: yaml.safe_load(f.read_text(encoding="utf-8")) for f in files}

    # 找每个被引用逻辑文件的维护方条目 —— 引用必须指向**真实存在**的 ILF，
    # 不能凭空造一个数据组名。
    owners: dict[str, dict] = {}
    for d in docs.values():
        for it in d["items"]:
            ns = it.get("nesma") or {}
            if ns.get("type") != "ILF" or it.get("deprecated_in"):
                continue
            key = it["name"].replace("-维护", "").replace("-台账", "").strip()
            owners.setdefault((key, it["path"]["system"]), it)

    doc = docs[FILE]
    existing = {i["id"] for i in doc["items"]}
    nxt = max(int(i["id"].split(".")[-1]) for i in doc["items"]) + 1
    new_items = []
    for name, owner_kw, refs, how in REFS:
        cand = [(k, v) for k, v in owners.items() if k[0] == name and owner_kw in k[1]]
        if len(cand) != 1:
            print(f"  ✗ 「{name}」在 {owner_kw} 命中 {len(cand)} 条 ILF，跳过")
            continue
        (_, owner_sys), owner_item = cand[0]
        ref_ids = [f"FP.APP.MGR.{r}" for r in refs]
        missing = [r for r in ref_ids if r not in existing]
        if missing:
            print(f"  ✗ 「{name}」引用了不存在的条目 {missing}，跳过")
            continue
        iid = f"FP.APP.MGR.{nxt:04d}"
        nxt += 1
        short = owner_sys.split("：")[-1].split("（")[0]
        new_items.append({
            "id": iid,
            "class": "SOFTWARE_FP",
            "name": name,
            "path": {"product_line": "多角色业务应用", "system": SYSTEM,
                     "l1": "逻辑文件", "l2": name},
            "description": (
                f"{name}：本系统引用、由{short}维护的逻辑数据组。"
                f"站长智助为站点驾驶舱，对本数据组只读不维护。引用位置：{how}"),
            "nesma": {
                "type": "ELF",
                "rationale": (
                    f"判为 ELF：本系统引用、由他方维护的用户可识别业务数据组"
                    f"（表6.1 p.17；山东标准称外部逻辑文件 ELF，即 IFPUG 的 EIF）。"
                    f"本子系统 {len(refs)} 条功能点引用它而无任何维护动作："
                    f"{'、'.join(ref_ids)}（{how}）。"
                    f"维护方为 {owner_item['id']}（{short}）。"),
                "counted_by": f"adjudication:G-04@{VERSION}",
                "logical_file_role": "reference",
                "logical_file_note": (
                    f"引用「{name}」，维护方为{owner_sys}（{owner_item['id']}），"
                    f"故记 ELF（外部逻辑文件，权重 7）。"
                    f"本条为 0.18.0 补建：此前本子系统 ILF 与 ELF 均为 0，"
                    f"而 EO/EQ 按定义须引用逻辑文件 —— 兄弟子系统 3.1/3.2/3.4 "
                    f"均建有「逻辑文件」模块，3.3 漏建。"),
            },
            "app_type": "业务处理",
            "dev_category": "大型行业软件开发",
            "maturity_evidence": (
                f"引用的逻辑数据组由 {owner_item['id']}（{short}）维护，"
                f"该条目 maturity={owner_item.get('maturity')}；"
                f"本条为接口引用，不新建数据结构。"),
            "source": (f"adjudication:G-04@{VERSION}"
                       f"（依据本子系统 43 条功能点的引用关系推定，非 raw-input 原始行）"),
            "maturity": "existing",
            "since": VERSION,
            "status": "draft",
        })

    print(f"\n拟新增 {len(new_items)} 条 ELF × 7 = {len(new_items)*7} UFP")
    for it in new_items:
        print(f"  {it['id']}  {it['name']:14} ← {it['nesma']['logical_file_note'][:40]}…")
    print(f"\n故意不收：")
    for k, v in EXCLUDED.items():
        print(f"  {k}：{v}")

    if dry:
        print("\n（dry-run，未写盘）")
        return 0
    doc["items"].extend(new_items)
    FILE.write_text(yaml.dump(doc, allow_unicode=True, sort_keys=False, width=200),
                    encoding="utf-8")
    docs[FILE] = doc
    n = fix_stale_rationale(docs)
    print(f"\n已写盘：新增 {len(new_items)} 条；修复 rationale 矛盾 {n} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main("--apply" not in sys.argv))
