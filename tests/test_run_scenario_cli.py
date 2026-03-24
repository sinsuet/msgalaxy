from __future__ import annotations

from run.run_scenario import main


def test_run_scenario_cli_dry_run_mass() -> None:
    code = main(["--stack", "mass", "--scenario", "optical_remote_sensing_bus", "--dry-run"])
    assert code == 0
