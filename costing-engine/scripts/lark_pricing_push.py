"""把各层 devices/pricing.yaml 推到**受控价格 base**。

## 为什么单独一个推送器

价格不进共创表 —— 这是拆 pricing.yaml 出来的全部理由。设备推送器
（lark_device_push）现在跑「无价模式」，物料表里一列价格都没有。价格得有
地方维护，否则 BOM 层的价格变成没人管的孤儿；于是单独一个 base、单独一个
文件夹、单独一套权限。

**两张表分开，受众不同**：

    1 市场单价与参考机型   商务/方案侧维护，出表用（甲附要列参考品牌型号）
    2 成本单价（内部）     仅商务/管理层

不合成一张。合过一次的后果是「一列成本被标成对外市场单价」，全表比在用报价
低约五成，一直到出套表核毛利时才发现。两个口径的数放同一张表里，迟早有人
横向复制。

## 料号是键，名字只是给人看的

写入以 `code` 为主键。设备名在两代 BOM、OA 字典、源表里各有叫法，拿名字对齐
必然错配 —— v1 就因为「按名字找行」删错过记录。名字这里只做展示列。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

import lark_table as lt

S_ID, S_BIZ, S_SRC = "标识", "商务", "来源"


def _f(name: str, sec: str, typ: str, desc: str = "") -> dict:
    # 字段名的键是 "name"，类型是字符串字面量 —— 飞书建表接口只认这两个。
    # 用 field_name / 数字类型码会被整批拒绝（那是记录写入接口的口径）。
    d: dict = {"name": f"{sec}·{name}" if sec else name, "type": typ}
    if desc:
        d["description"] = desc
    return d


TEXT, NUM = "text", "number"

SPEC = {
    "1 市场单价与参考机型": [
        {"name": "物料编码", "type": TEXT},
        _f("设备名称", S_ID, TEXT, "展示用。对齐一律按物料编码，不按名字"),
        _f("市场单价(元)", S_BIZ, NUM, "对外报价的产品级参考价。商机的实际报价在 deal 的 device-config，不在这里"),
        _f("参考对厂家型号", S_BIZ, TEXT, "表7 注3「参考品牌型号一般不少于3个」。格式：厂家：型号：价格，多个用换行分隔"),
        _f("参考机型数", S_BIZ, NUM, "含本机型；<3 需补"),
        _f("所属层", S_SRC, TEXT, "shared / preschool / eldercare —— 改价要改对应层的 pricing.yaml"),
        _f("价格依据", S_SRC, TEXT, "询价单 / 公开成交价 / 历史合同，缺依据的价过不了财评"),
    ],
    "2 成本单价（内部）": [
        {"name": "物料编码", "type": TEXT},
        _f("设备名称", S_ID, TEXT, "展示用"),
        _f("成本单价(元)", S_BIZ, NUM,
           "⚠ 内部件中的内部件。只有丙附的生成器读它；甲/甲附/乙 一律不读。严禁贴进任何送审件或共创表"),
        _f("成本口径", S_BIZ, TEXT, "BOM 成本价 / 供应商协议价 —— 协议价优先，两者别混"),
        _f("所属层", S_SRC, TEXT, "shared / preschool / eldercare"),
    ],
}

VIS = {
    "1 市场单价与参考机型": ["物料编码", "标识·设备名称", "商务·市场单价(元)",
                             "商务·参考对厂家型号", "商务·参考机型数",
                             "来源·所属层", "来源·价格依据"],
    "2 成本单价（内部）": ["物料编码", "标识·设备名称", "商务·成本单价(元)",
                           "商务·成本口径", "来源·所属层"],
}

LAYERS = [("shared", "shared/devices"),
          ("preschool", "verticals/preschool/devices"),
          ("eldercare", "verticals/eldercare/devices")]


def build_rows(bom_root: Path) -> dict[str, list[dict]]:
    market: list[dict] = []
    cost: list[dict] = []
    for layer, rel in LAYERS:
        d = bom_root / rel
        pp, mp = d / "pricing.yaml", d / "materials.yaml"
        if not pp.exists():
            continue
        px = yaml.safe_load(pp.read_text(encoding="utf-8")) or {}
        names = {m["code"]: m.get("name", "")
                 for m in ((yaml.safe_load(mp.read_text(encoding="utf-8")) or {})
                           .get("materials") or [])} if mp.exists() else {}
        for code, v in (px.get("prices") or {}).items():
            v = v or {}
            refs = v.get("ref_vendors") or []
            # **没有价就留空，不写 0**。0 在价格列上读作「免费」，
            # 而真相是「还没核价」—— 这两件事在算总额时天差地别，
            # 且 0 会安静地把总额算小，不会有任何一处报错。
            p = v.get("price_yuan")
            market.append({
                "物料编码": code, "标识·设备名称": names.get(code, ""),
                **({"商务·市场单价(元)": float(p)} if p else {}),
                "商务·参考对厂家型号": "\n".join(refs),
                # 本机型算一个 —— 与设备推送器同口径，否则两处「参考机型数」
                # 会给出不同的数字，而它是表7 注3 的合规判据。
                "商务·参考机型数": (1 if names.get(code) else 0) + len(refs),
                "来源·所属层": layer,
                "来源·价格依据": v.get("price_basis", "") or ("★待核价" if not p else ""),
            })
        for code, v in (px.get("cost") or {}).items():
            v = v or {}
            c = v.get("cost_yuan")
            cost.append({
                "物料编码": code, "标识·设备名称": names.get(code, ""),
                **({"商务·成本单价(元)": float(c)} if c else {}),
                "商务·成本口径": v.get("cost_source", "BOM 成本价"),
                "来源·所属层": layer,
            })
    return {"1 市场单价与参考机型": market, "2 成本单价（内部）": cost}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bom", type=Path, required=True)
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    cfg = yaml.safe_load(a.config.read_text(encoding="utf-8"))
    bt = cfg["base_token"]
    rows = build_rows(a.bom)
    for t, r in rows.items():
        print(f"  {t:22}{len(r):>5} 行")
    if a.dry_run:
        print("\n--dry-run，未连飞书。")
        return

    from lark_bom_split_push import ensure_table
    for t, fields in SPEC.items():
        tid = ensure_table(bt, t, fields)
        n = lt.replace_all(bt, tid, rows[t], label=t)
        print(f"  {t:22}写入 {n} 行　{tid}")
        cfg.setdefault("tables", {})[t] = tid
        exist = {f if isinstance(f, str) else f["name"] for f in lt.fields(bt, tid)}
        cols = [c for c in VIS[t] if c in exist]
        vs = lt.lark("base", "+view-list", "--base-token", bt,
                     "--table-id", tid, "--as", "user")
        for v in ((vs.get("data") or {}).get("items")
                  or (vs.get("data") or {}).get("views") or []):
            vid = v.get("view_id") or v.get("id")
            lt.lark("base", "+view-set-visible-fields", "--base-token", bt,
                    "--table-id", tid, "--view-id", vid,
                    "--json", json.dumps({"visible_fields": cols},
                                         ensure_ascii=False), "--as", "user")
    a.config.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
                        encoding="utf-8")
    print(f"\n→ https://lcna31l6eggn.feishu.cn/base/{bt}")


if __name__ == "__main__":
    main()
