#!/usr/bin/env python3
"""PreToolUse hook：拦截 paper-search-mcp 的 Sci-Hub 路径。

Claude Code 把工具调用 JSON 从 stdin 传进来；exit 2 = 阻止调用并把 stderr 回给模型。
拦两种：工具名含 scihub / sci_hub；或任何 paper-search 工具的入参里 use_scihub 为真。
"""
import json
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
name = str(data.get("tool_name", ""))
inp = data.get("tool_input") or {}
if not name.startswith("mcp__paper-search__"):
    sys.exit(0)
if "scihub" in name.lower() or "sci_hub" in name.lower():
    print(f"[studio-paper] 已禁用 Sci-Hub：{name} 不可调用。请走 OA 路径（download_with_fallback 不带 use_scihub），或让用户自行获取全文。", file=sys.stderr)
    sys.exit(2)
if isinstance(inp, dict) and str(inp.get("use_scihub", "")).lower() in ("true", "1", "yes"):
    print("[studio-paper] 已禁用 Sci-Hub：不要传 use_scihub=true。去掉该参数后重试（只走开放获取与出版商许可链接）。", file=sys.stderr)
    sys.exit(2)
sys.exit(0)
