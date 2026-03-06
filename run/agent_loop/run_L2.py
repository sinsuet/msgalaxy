#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
agent_loop L2 entry.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from run.agent_loop.common import run_agent_loop_level


def main(argv=None) -> int:
    return int(
        run_agent_loop_level(
            argv=argv,
            title="MsGalaxy agent_loop L2",
            level_label="L2",
            component_count=7,
            target_note="thermal-power full-stack scenario",
            default_bom=str(PROJECT_ROOT / "config" / "bom" / "agent_loop" / "level_L2_thermal_power_stack.json"),
            default_iterations=5,
        )
    )


if __name__ == "__main__":
    sys.exit(main())




