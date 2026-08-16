"""lark_table — 飞书多维表格的整表覆盖写入，带写前校验与无空窗替换。

这个模块存在的原因是一次真实事故：`_push_table` 原本是「先清空再写入」，
脚本还在写一个已被删掉的列，于是清空成功、写入失败，**15 张表全空**。
飞书不报错，只是表里什么都没有了。

两条防线：

  preflight()   删任何东西**之前**先比对表结构。写不存在的列、往单选里写
                不存在的选项、往公式列写值 —— 这三类会让写入整批失败，
                而它们都能在动手前静态查出来。

  replace_all() 先写后删，不留零行窗口。失败的代价从「空表」变成「重复行」，
                而重复行**下次 push 会自动清掉**（删的是本次开始前就存在的
                那批），是自愈的；空表不会自愈。

不做临时表切换：跨表公式里嵌的是 table_id，换表要重建所有公式列和配置，
而建表/删表本身有频控。风险比它要解决的问题还大。
"""
from __future__ import annotations

import json
import subprocess
import time
from typing import Any

BATCH = 200

#: 这些类型是算出来的，写值会被覆盖或直接报错
DERIVED_TYPES = {"formula", "lookup", "auto_number", "created_time",
                 "modified_time", "created_user", "modified_user"}


class LarkTableError(RuntimeError):
    """整表写入的前置校验或写入失败 —— 一律硬失败，不静默继续。"""


def lark(*args: str) -> dict[str, Any]:
    """调用 lark-cli 并解析 JSON。

    用 `--format json` 而不是 `--json` 指定输出格式 —— 后者在
    `+record-batch-create` / `+record-delete` 上是**载荷参数**，
    追加会拼成 `--json <载荷> --json`，末尾那个没有参数直接报错。
    """
    r = subprocess.run(["lark-cli", *args, "--format", "json"],
                       capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        # 建资源类命令会在 JSON 前先打一行散文，解析崩**不代表操作失败**
        return {"ok": False, "error": {"raw": (r.stdout or r.stderr)[:300]}}


def fields(bt: str, tid: str) -> dict[str, dict[str, Any]]:
    r = lark("base", "+field-list", "--base-token", bt, "--table-id", tid, "--as", "user")
    if not r.get("ok"):
        raise LarkTableError(f"读字段失败 {tid}：{r.get('error')}")
    return {f["name"]: f for f in (r.get("data") or {}).get("fields", [])}


def preflight(bt: str, tid: str, rows: list[dict[str, Any]],
              label: str = "") -> None:
    """写入前比对表结构。**必须在删除之前调用。**"""
    if not rows:
        return
    f = fields(bt, tid)
    keys: set[str] = set()
    for r in rows:
        keys |= set(r)

    problems: list[str] = []

    missing = sorted(keys - set(f))
    if missing:
        problems.append(f"表里没有这些列：{missing}（现有 {sorted(f)}）")

    derived = sorted(k for k in keys & set(f) if f[k].get("type") in DERIVED_TYPES)
    if derived:
        problems.append(f"这些是算出来的列，不该写值：{derived}")

    for k in sorted(keys & set(f)):
        spec = f[k]
        if spec.get("type") != "select":
            continue
        allowed = {o["name"] for o in (spec.get("options") or [])}
        used = {str(r[k]) for r in rows if r.get(k) not in (None, "")}
        bad = sorted(used - allowed)
        if bad:
            problems.append(f"单选列「{k}」没有这些选项：{bad}（现有 {sorted(allowed)}）")

    if problems:
        raise LarkTableError(
            f"{label or tid} 写前校验不通过，**未做任何改动**：\n  "
            + "\n  ".join(problems))


def record_ids(bt: str, tid: str) -> list[str]:
    """全表 record_id。

    必须**反复取页直到取空** —— 单次只回一页（200 条）。只取一页就当全部，
    删除时会留下剩余旧记录与新数据叠加（实测 1764 条只删了 200）。
    """
    out: list[str] = []
    while True:
        r = lark("base", "+record-list", "--base-token", bt, "--table-id", tid,
                 "--as", "user", "--limit", str(BATCH), "--offset", str(len(out)))
        if not r.get("ok"):
            raise LarkTableError(f"列记录失败 {tid}：{r.get('error')}")
        d = r.get("data") or {}
        ids = d.get("record_id_list") or []
        out.extend(ids)
        if not d.get("has_more") or not ids:
            return out


def read_rows(bt: str, tid: str, cols: list[str] | None = None) -> list[dict]:
    """整表读回 `{列名: 值}`，翻页取尽。

    返回体的形状**不是**记录数组：`data.fields` 是列名、`data.data` 是行数组，
    没有 `records` / `items` 这两个键。按常见形状去取会读回空列表 ——
    而空列表看起来就像「表里没数据」或「没人改过」，据此去删表就毁数据了。
    这个函数存在的理由就是别让每处调用各猜一遍。
    """
    out: list[dict] = []
    while True:
        args = ["base", "+record-list", "--base-token", bt, "--table-id", tid,
                "--as", "user", "--limit", str(BATCH), "--offset", str(len(out))]
        for c in (cols or []):
            args += ["--field-id", c]
        r = lark(*args)
        if not r.get("ok"):
            raise LarkTableError(f"读记录失败 {tid}：{r.get('error')}")
        d = r.get("data") or {}
        names, rows = d.get("fields") or [], d.get("data") or []
        out += [dict(zip(names, row)) for row in rows]
        if not d.get("has_more") or not rows:
            return out


def read_rows_with_id(bt: str, tid: str, cols: list[str]) -> list[tuple[str, dict]]:
    """同 `read_rows`，但一并带回 record_id。

    同一次响应里 `record_id_list` 与 `data` 是**平行数组**，靠下标对应。
    分两次请求去拿的话，中间有人改了表就错位了 —— 而错位的关联指向另一台设备，
    看起来完全正常。
    """
    out: list[tuple[str, dict]] = []
    while True:
        args = ["base", "+record-list", "--base-token", bt, "--table-id", tid,
                "--as", "user", "--limit", str(BATCH), "--offset", str(len(out))]
        for c in cols:
            args += ["--field-id", c]
        r = lark(*args)
        if not r.get("ok"):
            raise LarkTableError(f"读记录失败 {tid}：{r.get('error')}")
        d = r.get("data") or {}
        names, rows = d.get("fields") or [], d.get("data") or []
        ids = d.get("record_id_list") or []
        if len(ids) != len(rows):
            raise LarkTableError(
                f"{tid}：record_id_list {len(ids)} 条与 data {len(rows)} 行不等长 —— "
                f"两个平行数组靠下标对应，不等长时对不上，宁可失败也不能错位")
        out += list(zip(ids, (dict(zip(names, row)) for row in rows)))
        if not d.get("has_more") or not rows:
            return out


def _delete(bt: str, tid: str, ids: list[str]) -> None:
    for s in range(0, len(ids), BATCH):
        r = lark("base", "+record-delete", "--base-token", bt, "--table-id", tid,
                 "--as", "user", "--yes",
                 "--json", json.dumps({"record_id_list": ids[s:s + BATCH]}))
        if not r.get("ok"):
            raise LarkTableError(f"删除失败 {tid}：{r.get('error')}")


def _create(bt: str, tid: str, rows: list[dict[str, Any]]) -> int:
    written = 0
    for s in range(0, len(rows), BATCH):
        r = lark("base", "+record-batch-create", "--base-token", bt, "--table-id", tid,
                 "--as", "user",
                 "--json", json.dumps({"create_records": rows[s:s + BATCH]},
                                      ensure_ascii=False))
        if not r.get("ok"):
            raise LarkTableError(
                f"写入失败 {tid}（已写 {written}/{len(rows)}，旧数据尚未删除，"
                f"表中现为新旧叠加；修好后重跑即自动清理）：{r.get('error')}")
        written += len(r["data"].get("record_id_list", []))
    return written


def replace_all(bt: str, tid: str, rows: list[dict[str, Any]],
                label: str = "") -> int:
    """整表替换：校验 → 记下旧 id → 写新 → 删旧 → 断言行数。

    顺序是「先写后删」而不是「先删后写」。任何时刻表里都不是空的；
    中途失败留下的是重复行，下次 push 会连同旧行一起删掉，自愈。
    """
    preflight(bt, tid, rows, label)
    old = record_ids(bt, tid)
    written = _create(bt, tid, rows)
    if written != len(rows):
        raise LarkTableError(
            f"{label or tid} 写入条数不符：期望 {len(rows)}，实得 {written} —— "
            f"疑似限流导致部分写入。旧数据未删，重跑即恢复")
    _delete(bt, tid, old)
    # 删除返回 ok 之后，列记录**未必立刻反映** —— 飞书这边是最终一致的。
    # 实测删完立刻读还是 76 旧 + 76 新，等几秒就对了。所以轮询而不是一读定论：
    # 一读定论会把「正常的延迟」报成「删除失败」，比不检查更糟。
    for i in range(8):
        remain = len(record_ids(bt, tid))
        if remain == len(rows):
            return written
        time.sleep(1 + i)
    raise LarkTableError(
        f"{label or tid} 收尾行数不符：期望 {len(rows)}，实得 {remain} —— "
        f"旧行疑似未删净，表中为新旧叠加；重跑即自动清理")
