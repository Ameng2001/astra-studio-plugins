"""auto_approve — generate an approved-plan.json by auto-accepting all suggestions.

Used for smoke-testing the optimize → rewrite loop end-to-end without the
interactive HIL step. Real users go through quote-optimize's natural-language
approval flow.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path


def main(session_dir: str) -> None:
    s = Path(session_dir)
    suggestions = json.loads((s / "optimize-suggestions.json").read_text())
    plan = {
        "session_id": suggestions.get("session_dir", str(s)),
        "approved_at": datetime.now().isoformat(timespec="seconds"),
        "decisions": [
            {
                **sg,                      # carry id/category/target/proposed_change/standard_refs
                "decision": "accept",
            }
            for sg in suggestions["suggestions"]
        ],
    }
    (s / "approved-plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    print(f"approved-plan.json written, {len(plan['decisions'])} decisions auto-accepted")


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1] if len(sys.argv) > 1 else ".fund-review/smoke")
