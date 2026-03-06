#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
agent_loop L3 entry.
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
            title="MsGalaxy agent_loop L3",
            level_label="L3",
            component_count=9,
            target_note="structural-mission full-stack scenario",
            default_bom=str(PROJECT_ROOT / "config" / "bom" / "agent_loop" / "level_L3_structural_mission_stack.json"),
            default_iterations=6,
        )
    )


if __name__ == "__main__":
    sys.exit(main())




