#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(project_root))
    from run.vop_maas.common import run_level
else:
    from .common import run_level


def main(argv=None) -> int:
    return run_level("L1", argv)


if __name__ == "__main__":
    raise SystemExit(main())
