"""按「一个维护方记 ILF、其余记 ELF」改判跨子系统重复的逻辑文件。

规则由用户裁定。本脚本只负责**把规则落到条目上，并把判定依据一并写进去** ——
改完之后每一条都能回答「为什么这条是 ELF」，而不是留下一堆无从复核的改动。

维护方的选取依据写在 MAINTAINER 表里，逐组可查、可推翻。

**一次性工具**：本项目 0.18.0 用它做了一轮改判（23 条 ILF → ELF）。
此后新增的重复由门禁 G-15 逐条拦下，正常不需要再整批跑。
留着是为了换项目、或维护方裁定要推翻时能重来一遍 —— 表改一行即可。

用法：
    BOM_PROJECT_ROOT=<项目目录> python3 bom_shared_lf.py [--apply]
    不带 --apply 为 dry-run。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

ROOT = Path(os.environ.get("BOM_PROJECT_ROOT", "."))

#: 数据组 → (维护方子系统关键词, 判定依据)。
#: 依据取自条目自身的 description —— 不是我的偏好，是 BOM 里已经写下的话。
MAINTAINER = {
    # ---- 交易履约主链路：订单类主数据归生态中台 ----
    "订单": ("生态运营与交易中台",
             "家属APP「下单与订单状态流转…为同一张订单」、服务商「接单、核销、发货、"
             "物流为同一张订单的流转」—— 两端均自述为同一张订单的阶段；"
             "生态中台「支付与履约/订单与工单」是履约主链路"),
    "商品与服务": ("生态运营与交易中台",
                   "生态中台「SKU、规格、定价、上下架、价格日志为同一份商品主数据」，"
                   "服务商侧「发布与定价是同一份商品主数据的维护」"),
    "售后单": ("生态运营与交易中台", "支付与履约/售后客服与争议处理为售后主责"),
    "租赁单": ("生态运营与交易中台", "支付与履约/辅具租赁为租赁履约主责"),

    # ---- 站点业务主数据归 3.1 机构站点管理系统 ----
    "长者档案": ("3.1机构站点管理系统",
                 "3.1「在住/意向/预订长者与健康档案是同一份档案的不同状态与切面」—— "
                 "五处中唯一自述覆盖档案全生命周期者；政府端为监管汇聚、"
                 "3.2/3.4 为服务执行、区域端为汇总"),
    "服务工单": ("3.1机构站点管理系统",
                 "3.1「门店运营/工单中心」为工单中枢；"
                 "3.4 自述「签到/签退/接待确认/处理工单/完结工单是同一张服务工单」，"
                 "属执行环节"),
    "审批流程实例": ("3.1机构站点管理系统", "行政管理/审批中心为审批主责"),
    "活动与签到": ("3.1机构站点管理系统", "照护业务/活动管理为活动主责"),
    "评估表与评估记录": ("3.1机构站点管理系统", "照护业务/评估管理为评估主责"),
    "员工档案": ("3.1机构站点管理系统", "行政管理/员工管理为人事主责"),

    # ---- 区域级主数据归二、机构区域运营端 ----
    "物资库存": ("二、机构区域运营端",
                 "区域端「入库、出库、盘点、分仓为同一份库存台账」—— "
                 "分仓意味着区域级统管"),
    "财务科目": ("二、机构区域运营端", "运营管理中心/财务管理为区域财务主责"),
    "商品": ("二、机构区域运营端",
             "区域端「商品基本信息、服务项目、分组、客服配置为同一份商品主数据」"),
    "客户线索": ("二、机构区域运营端", "营销管理中心/客户管理为线索主责"),
    "设备档案": ("二、机构区域运营端", "运营管理中心/设备中心为设备主责"),
}

#: **重名但不是同一份数据** —— 不并组。
#: 「文档」在 3.1 是行政管理/文档中心（公文档案），在智能体平台是系统管理/文档中心
#: （智能体的知识文档）。两个不同业务域，各自维护各自的，都该记 ILF。
NAME_COLLISIONS = {"文档"}

#: 重名但业务上是**另一份逻辑文件**的个别条目 —— 整组不排除，只排除这一条。
#: 政府端的「长者档案」维护的是户籍变更/死亡/失联这类**民政事实**，
#: 机构端维护的是在住/意向/预订的服务档案。两者的维护动作互不覆盖，
#: 判为同一份数据组会把监管侧的登记职能抹掉。
ITEM_EXEMPT = {
    "FP.APP.GOV.0137": "政府监管侧的长者登记册（户籍变更/死亡/失联），"
                       "与机构侧的服务档案（在住/意向/预订）不是同一份逻辑数据组",
}

#: 描述里**具体列举了维护动作**、改判 ELF 后需人工复核的条目。
#: 判为 ELF 的理由是：这些动作是对同一份主数据的**状态流转**，
#: 写操作经由维护方的系统落库。但这属推断，须方案侧确认。
NEEDS_CONFIRM = {
    "FP.APP.CARE.0119": "签到/签退/完结工单是对同一张工单的状态流转",
    "FP.APP.USER.0079": "下单与状态流转经交易中台落库",
    "FP.APP.VEND.0086": "接单/核销/发货是对同一张订单的状态流转",
    "FP.APP.VEND.0080": "服务商发品定价，写操作经交易中台的商品主数据落库 —— "
                        "**若实为服务商自持商品库，则维护方应改为服务商系统**",
}


def norm(n: str) -> str:
    return n.replace("-维护", "").replace("-台账", "").strip()


def main(dry: bool) -> int:
    files = sorted((ROOT / "bom/items").glob("*.yaml"))
    docs = {f: yaml.safe_load(f.read_text(encoding="utf-8")) for f in files}

    groups: dict[str, list[tuple[Path, dict]]] = {}
    for f, d in docs.items():
        for it in d["items"]:
            if it.get("deprecated_in") or (it.get("nesma") or {}).get("type") != "ILF":
                continue
            groups.setdefault(norm(it["name"]), []).append((f, it))

    changed, kept, unresolved = 0, 0, []
    for name, members in sorted(groups.items()):
        if len(members) < 2 or name in NAME_COLLISIONS:
            continue
        if name not in MAINTAINER:
            unresolved.append((name, [it["path"]["system"] for _f, it in members]))
            continue
        owner_kw, reason = MAINTAINER[name]
        owners = [m for m in members if owner_kw in m[1]["path"]["system"]]
        if len(owners) != 1:
            unresolved.append((name, f"维护方关键词 {owner_kw!r} 命中 {len(owners)} 条"))
            continue
        owner_sys = owners[0][1]["path"]["system"]
        print(f"\n■ {name}　×{len(members)}　维护方 = {owner_sys[:44]}")
        for _f, it in members:
            if it["id"] in ITEM_EXEMPT:
                it["nesma"]["logical_file_note"] = (
                    f"与「{name}」同名但**不是同一份逻辑数据组**，故仍记 ILF。"
                    + ITEM_EXEMPT[it["id"]])
                print(f"    ILF 保留(重名不同物)  {it['id']}  {it['path']['system'][:34]}")
                continue
            if it is owners[0][1]:
                it["nesma"]["logical_file_role"] = "maintainer"
                it["nesma"]["logical_file_note"] = f"本组维护方，记 ILF。{reason}"
                kept += 1
                print(f"    ILF 保留  {it['id']}")
                continue
            it["nesma"]["type"] = "ELF"
            it["nesma"]["logical_file_role"] = "reference"
            it["nesma"]["logical_file_note"] = (
                f"引用「{name}」，维护方为{owner_sys}，故记 ELF（外部逻辑文件）。{reason}")
            # 描述里那句「本系统维护的业务逻辑数据组」现在是错的，改掉 ——
            # 留着会与 type=ELF 直接矛盾，评审一眼就能看出表里自相矛盾。
            it["description"] = it["description"].replace(
                "本系统维护的业务逻辑数据组",
                f"本系统引用、由{owner_sys.split('：')[-1].split('（')[0]}维护的逻辑数据组")
            if it["id"] in NEEDS_CONFIRM:
                it["nesma"]["logical_file_note"] += (
                    f"　⚠ 原描述具体列举了维护动作，判为引用方的理由："
                    f"{NEEDS_CONFIRM[it['id']]}　—— 须方案侧确认。")
            changed += 1
            mark = "  ⚠待确认" if it["id"] in NEEDS_CONFIRM else ""
            print(f"    → ELF    {it['id']}  {it['path']['system'][:36]}{mark}")

    print(f"\n{'='*70}")
    print(f"维护方保留 ILF {kept} 条；引用方改判 ELF {changed} 条")
    print(f"重名不并组（各自维护）：{sorted(NAME_COLLISIONS)}")
    if unresolved:
        print(f"\n⚠ 未处理 {len(unresolved)} 组（维护方未裁定）：")
        for n, v in unresolved:
            print(f"    {n}: {v}")
    if dry:
        print("\n（dry-run，未写盘）")
        return 0
    for f, d in docs.items():
        f.write_text(yaml.dump(d, allow_unicode=True, sort_keys=False, width=200),
                     encoding="utf-8")
    print("\n已写盘")
    return 0


if __name__ == "__main__":
    sys.exit(main("--apply" not in sys.argv))
