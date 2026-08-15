"""expert_reviewer — 模拟「financial-reviewer」财评专家 agent.

体系结构：
  - `enhance_challenge(finding) -> dict`：主入口，吃一条 finding，吐丰富的挑战文本
  - `_call_llm(prompt) -> str | None`：LLM 接口占位
        当前实现：返回 None（让上层回退到模板）
        未来：可改为 Anthropic / OpenAI / 本地模型调用
  - `_template_challenge(finding) -> dict`：丰富模板生成（rule-based，按 rule_code 分支）

设计原则：
  - 立场对内：扮演财评专家挑战投标方报价（红队预演）
  - 每条 challenge 必有 PDF 锚点；不允许凭空挑战
  - 中文公文/审计语气；2-4 句封顶
  - 输出包括：challenge_text / remediation / confidence (high/medium/low)
"""
from __future__ import annotations

from typing import Any


def _call_llm(prompt: str, agent_path: str = "fund-review/agents/financial-reviewer.md") -> str | None:
    """LLM 调用接口占位.

    v0.4 MVP：返回 None（上层回退到模板）。
    v0.5+：可在此实现真实 LLM 调用，例如：
        ```python
        import subprocess, json
        r = subprocess.run(
            ["claude", "agent", "run", agent_path, "--input", prompt],
            capture_output=True, text=True, timeout=30,
        )
        return r.stdout if r.returncode == 0 else None
        ```
    或通过 Anthropic SDK 直接调 Claude API。
    """
    return None


# ---- 丰富模板（v0.4 升级版） -----------------------------------------------

_CHALLENGE_TEMPLATES = {
    "labor-pricing-out-of-band": {
        "challenge": (
            "本行单价 ¥{cur}/人天明显高于标准带上限 ¥{ceiling:.0f}（{cat}/{reuse}）。"
            "按 PDF 第三章 (一) 2.1 定制软件开发费公式："
            "1.7 万/人月 × 类别因子[{cat}] × 复用度[{reuse}] / 21.75 工作日 = ¥{floor:.0f}-¥{ceiling:.0f}。"
            "现行单价 ¥{cur} 未提供 FP 估算与类别取值上限依据，**建议否决并要求补正**。"
        ),
        "remediation": (
            "1) 补 FP 估算明细（参考可研附表）；2) 单价降至 ¥{ceiling:.0f} × 0.98 = ¥{target:.0f}；"
            "3) 在投标书附「取值依据说明」明确类别归属与上限取值理由。"
        ),
        "confidence": "high",
    },
    "labor-pricing-near-band-edge": {
        "challenge": (
            "本行单价 ¥{cur}/人天贴 {cat} 类带上限 ¥{ceiling:.0f}。"
            "按 PDF 表3 注1，凡取值超过 1 的需列明依据。"
            "建议供应商在投标书附 FP 估算 + 项目主体功能性质说明，否则可质疑取值合理性。"
        ),
        "remediation": (
            "在「取值依据说明.md」（附件 A）中加入：本项目 X% 功能属 {cat} 类（举例：…）；"
            "或将单价下调至 ¥{ceiling:.0f} × 0.95 = ¥{conservative:.0f} 以留缓冲。"
        ),
        "confidence": "medium",
    },
    "device-needs-table7": {
        "challenge": (
            "设备项缺 PDF 表7 注3 要求的「≥3 个品牌型号对比」依据。"
            "无市场询价证据时不能确认是否「不得购置性能远超需求的产品、与应用场景不相符合的产品」（PDF 2.5 注2）。"
            "建议要求供应商按附录 B 补品牌型号对比 + 市场询价单。"
        ),
        "remediation": (
            "为本设备行新增「品牌/型号对比」列：海康/大华/宇视 等同档对比 3 个，附电商截图或厂商询价邮件。"
        ),
        "confidence": "high",
    },
}


def _template_challenge(finding: dict) -> dict:
    rule = finding.get("rule", "")
    summary = finding.get("summary", "")
    tmpl = _CHALLENGE_TEMPLATES.get(rule)
    if not tmpl:
        # Fallback: original templated challenge already in finding
        return {
            "challenge_text": finding.get("challenge", summary),
            "remediation": "",
            "confidence": "low",
        }

    # Parse numbers from summary using existing review_row format
    # Example: "单价 ¥1200/人天 越界（人工智能/中 标准带 ¥521-¥782）"
    import re
    m = re.search(r"¥(\d+(?:\.\d+)?)/人天.*?[（(]([^/]+)/([^\s)]+).*?¥(\d+(?:\.\d+)?)-¥(\d+(?:\.\d+)?)", summary)
    ctx: dict[str, Any] = {}
    if m:
        ctx["cur"] = float(m.group(1))
        ctx["cat"] = m.group(2).strip()
        ctx["reuse"] = m.group(3).strip()
        ctx["floor"] = float(m.group(4))
        ctx["ceiling"] = float(m.group(5))
        ctx["target"] = ctx["ceiling"] * 0.98
        ctx["conservative"] = ctx["ceiling"] * 0.95
    try:
        challenge = tmpl["challenge"].format(**ctx)
        remediation = tmpl["remediation"].format(**ctx)
    except (KeyError, IndexError):
        challenge = finding.get("challenge", summary)
        remediation = ""
    return {
        "challenge_text": challenge,
        "remediation": remediation,
        "confidence": tmpl["confidence"],
    }


def enhance_challenge(finding: dict) -> dict:
    """Public entry: try LLM first, fall back to richer template."""
    prompt = (
        f"As 财评专家, review this finding and produce 2-4 sentences of professional challenge "
        f"with a concrete remediation. Cite PDF section + page.\n\n"
        f"Finding: {finding}"
    )
    llm_resp = _call_llm(prompt)
    if llm_resp:
        return {
            "challenge_text": llm_resp,
            "remediation": "",
            "confidence": "high",
            "source": "llm",
        }
    out = _template_challenge(finding)
    out["source"] = "template"
    return out
