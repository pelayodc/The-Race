import json
from pathlib import Path
import runpy
import socket
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "simulate_challenge.py"


@pytest.mark.parametrize("seed", [0, 42, 999])
def test_simulation_without_network_or_storage(seed, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Offline simulation attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://invalid:invalid@invalid/production")
    module = runpy.run_path(str(SCRIPT))
    report = module["simulate"](seed)
    assert report["passed"], [c for c in report["checks"] if not c["passed"]]
    assert len(report["checks"]) >= 150
    assert {p["games"] for p in report["ranking"]} == {30}
    assert {case["task"] for case in report["task_scenarios"]} == set(module["TASKS"])
    assert all(case["actual"] == case["expected"] for case in report["task_scenarios"])
    assert report["ranking"] == module["simulate"](seed)["ranking"]


def test_simulation_cli_creates_report_outside_repository(tmp_path):
    output = tmp_path / "report"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(output), "--seed", "7"],
        cwd=tmp_path, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["simulation"] and report["passed"]
    assert "Clasificacion final" in (output / "index.html").read_text(encoding="utf-8")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["report"]
