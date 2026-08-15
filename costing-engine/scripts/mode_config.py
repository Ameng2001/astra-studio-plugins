"""mode_config — 立场内的两档策略配置.

两档都服从 global.md 立场（投标方利益 + 财评一次过），区别在于风险/利润的偏好权重：

  - 激进 (aggressive):  保利润 > 防风险
        G1 严守 ±5%，单价贴上限不留缓冲；接受 ~100+ 个 fail 作为"谈判素材"
        预期：跌幅 -4.8%，财评得分 ~50，需要现场答辩

  - 保守 (conservative): 防风险 > 保利润
        G1 放宽 ±10%，单价上限 -2% 缓冲；fail 清零
        预期：跌幅 -8.7%，财评得分 95+，"无可挑剔送审"

切换方式：环境变量 FR_MODE=激进 / 保守，或脚本传 --mode 参数。
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Mode:
    name: str
    band_pct: float           # G1 ±X
    target_buffer: float      # 1.0 = 贴上限；0.98 = -2% 缓冲
    description: str


MODES: dict[str, Mode] = {
    "激进": Mode(
        name="激进",
        band_pct=0.05,
        target_buffer=1.00,
        description="保利润：G1 严守 ±5%，单价贴上限。接受部分 fail 作为现场答辩素材。",
    ),
    "保守": Mode(
        name="保守",
        band_pct=0.10,
        target_buffer=0.98,
        description="防风险：G1 放宽 ±10%，单价上限 -2% 缓冲。fail 清零，无可挑剔送审。",
    ),
}


def current() -> Mode:
    key = os.environ.get("FR_MODE", "保守")
    return MODES.get(key, MODES["保守"])
