"""quote_run —— v2 分层仓的一键出表：物化 → 基线 → 套表。

## 为什么要这一层

v2 的 BOM 是分层的（`bom/shared/**` + `bom/verticals/<v>/**`），而下游的
`baseline_build` / `quote_generate_*` 读的是 v1 那种平铺结构。中间靠
`bom_view.materialize()` 物化一个临时视图。

这三步以前是手工拼的：建临时目录、物化、拷 deals 与 standard-packs、
软链 raw-input、跑基线、跑出表。手工拼六步，**漏掉任何一步都不报错**，
只是出来的数不一样 —— 比如忘了重跑基线，套表会拿上一次的 UFP 出一份
看起来完全正常的表。所以固化成一条命令。

## 中间产物放哪

`<root>/.build/<deal>/`，每次重建。它不是产物，是脚手架；产物是
`deals/<deal>/out/` 里的套表。中间目录留着只为出问题时能翻。

用法：
    python3 quote_run.py --root <v2仓> --deal 2026-liuzhou-preschool
    python3 quote_run.py --root <v2仓> --deal 2026-meizhou-eldercare --emitter guangdong
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from bom_view import materialize, merge_devices

HERE = Path(__file__).resolve().parent

EMITTERS = {
    "liuzhou": "quote_generate_liuzhou.py",
    "guangdong": "quote_generate_guangdong.py",
    "shandong": "quote_generate_shandong.py",
}


def _run(args: list[str], cwd: Path) -> None:
    r = subprocess.run([sys.executable, *args], cwd=cwd)
    if r.returncode:
        raise SystemExit(f"⛔ 失败：{' '.join(str(a) for a in args)}")


def _check_pack_sources(root: Path, pack_dir: Path) -> None:
    """标准包里 source_pdf / source_file 指向的原文必须真的在。

    这些路径是**仓内相对路径**，指向 regional-standards/ 下的标准原件。
    财评现场问「这个系数出自哪一条」，答案链路是 citation 的页码 + 原文 PDF；
    页码在 pack 里，PDF 要打得开。

    v2 仓建起来时没带 regional-standards/，七处引用全部悬空了一段时间 ——
    **不影响出表**（citation 的页码与原句早已摄进 pack.yaml），所以谁都没发现，
    直到有人真去翻原文。这就是那类「不报错、只是查不动了」的缺陷，故设此关。
    """
    def refs(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("source_pdf", "source_file") and isinstance(v, str):
                    yield v
                else:
                    yield from refs(v)
        elif isinstance(o, list):
            for v in o:
                yield from refs(v)

    data = yaml.safe_load((pack_dir / "pack.yaml").read_text(encoding="utf-8"))
    missing = [r for r in refs(data) if not (root / r).exists()]
    if missing:
        raise SystemExit(
            f"⛔ 标准包 {pack_dir.name} 的原文引用悬空 {len(missing)} 处：\n"
            + "".join(f"    {m}\n" for m in missing)
            + f"  这些是仓内相对路径，应能在 {root} 下打开。\n"
            f"  标准原文是 citation 的落地处 —— 页码在 pack 里，PDF 得打得开。")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path, help="v2 数据仓根目录")
    ap.add_argument("--deal", required=True, help="deals/ 下的商机目录名")
    ap.add_argument("--emitter", default="liuzhou", choices=sorted(EMITTERS))
    ap.add_argument("--out", type=Path, help="缺省 deals/<deal>/out")
    ap.add_argument("--keep-build", action="store_true",
                    help="保留 .build 中间目录（默认也保留，此项仅为显式表达）")
    a = ap.parse_args()

    root = a.root.resolve()
    # **root 不能指向 .build 里面。** `--root .` 在 shell 恰好停在中间目录时
    # 就会这样，而中间目录长得和数据仓一模一样（有 bom/ deals/ standard-packs/），
    # 所以它**跑得通**：出的表金额还对，只是把上一轮的产物又拷了一遍，
    # 11 分钟而不是 40 秒。跑得通的错误最难发现，所以这里直接拦。
    if ".build" in root.parts:
        raise SystemExit(
            f"⛔ --root 指向了中间目录：{root}\n"
            f"  .build/ 是每次重建的脚手架，不是数据仓。"
            f"数据仓是含 bom/ deals/ standard-packs/ 的那一层，"
            f"这里应给 {Path(*root.parts[:root.parts.index('.build')])}")
    deal_dir = root / "deals" / a.deal
    deal = yaml.safe_load((deal_dir / "deal.yaml").read_text(encoding="utf-8"))

    build = root / ".build" / a.deal
    if build.exists():
        shutil.rmtree(build)
    build.mkdir(parents=True)

    # ---- 1. 物化分层 BOM ----
    materialize(root / "bom", deal, build)
    st = merge_devices(root / "bom", deal, build / "bom")
    ver = (build / "bom" / "VERSION").read_text(encoding="utf-8").strip()
    print(f"① 物化 BOM {ver}　设备 {st.get('materials.yaml', 0)} 物料 / "
          f"{st.get('catalog.yaml', 0)} 配置　有价 {st.get('priced', 0)}　"
          f"成本参考 {st.get('cost', 0)}")

    # ---- 2. 摆好下游要读的邻居目录 ----
    for d in ("deals", "standard-packs"):
        shutil.copytree(root / d, build / d)
    # 源表是**只读输入**，软链不拷 —— 拷一份就有了第二个真相，
    # 改了源表而忘了重拷，出表会拿旧表对照且不报错。
    src = (deal.get("sources") or {}).get("software")
    if src:
        want = (build / src).parent
        real = root / Path(src).parent
        if not real.exists():
            raise SystemExit(
                f"⛔ deal.sources.software 指向 {src}，但 {real} 不存在。\n"
                f"  源表是业务侧的估算表，v2 仓不存副本 —— 请在 {root} 下建\n"
                f"  {Path(src).parent} 软链指向持有它的仓，或删掉 deal 的该声明\n"
                f"  （删掉只是少了「引擎算的数 vs 业务侧估的数」对照列，不影响金额）")
        want.parent.mkdir(parents=True, exist_ok=True)
        want.symlink_to(real.resolve())

    # 标准原文**软链不拷** —— 27M 只读原件，与 raw-input 同理。
    rs = root / "regional-standards"
    if rs.exists():
        (build / "regional-standards").symlink_to(rs.resolve())

    # ---- 3. 基线 ----
    pack_id = Path(deal["baseline"]).name
    _check_pack_sources(root, root / "standard-packs" / pack_id)
    bl_out = f"baselines/{pack_id}@bom-{ver}"
    print(f"② 基线 {pack_id} × BOM {ver}")
    _run([str(HERE / "baseline_build.py"), "--bom", "bom",
          "--pack", f"standard-packs/{pack_id}", "--out", bl_out], cwd=build)

    # ---- 4. 套表 ----
    out = (a.out or (deal_dir / "out")).resolve()
    print(f"③ 出表 → {out}")
    _run([str(HERE / EMITTERS[a.emitter]),
          "--deal", f"deals/{a.deal}/deal.yaml", "--out", str(out)], cwd=build)

    # 基线是可复现的中间品，但**留一份在仓里**：出表引用的是哪一版基线，
    # 事后要能查。放 <root>/baselines/ 与 v1 同构。
    keep = root / bl_out
    if keep.exists():
        shutil.rmtree(keep)
    keep.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(build / bl_out, keep)
    print(f"   基线留档 → {bl_out}")


if __name__ == "__main__":
    main()
