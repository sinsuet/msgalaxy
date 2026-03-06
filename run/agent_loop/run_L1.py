#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
agent_loop L1 entry.
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
            title="MsGalaxy agent_loop L1",
            level_label="L1",
            component_count=6,
            target_note="foundation full-stack scenario",
            default_bom=str(PROJECT_ROOT / "config" / "bom" / "agent_loop" / "level_L1_foundation_stack.json"),
            default_iterations=4,
        )
    )


if __name__ == "__main__":
    sys.exit(main())




