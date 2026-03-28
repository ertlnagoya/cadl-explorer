"""Tests for Unity adapters (dry-run, no Unity required)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tempfile
from pathlib import Path
from backend.services.cadl_service import make_config
from backend.adapters.unity_config_adapter import generate_config, write_config
from backend.adapters.unity_runner_adapter import is_available
from backend.adapters.unity_result_adapter import parse_metrics_report


def test_generate_config():
    cfg = make_config("A-SoS", "uniform", 0.0)
    result = generate_config(cfg)
    assert isinstance(result, dict)
    assert "simulatorConfig" in result
    assert "motivationConfig" in result


def test_write_config():
    cfg = make_config("C-SoS", "linear", 0.0)
    with tempfile.TemporaryDirectory() as td:
        path = write_config(cfg, Path(td) / "test_config.json")
        assert path.exists()
        import json
        with open(path) as f:
            data = json.load(f)
        assert data["simulatorConfig"]["sosType"] == "collaborative"


def test_is_available_returns_false():
    assert is_available() is False
    assert is_available("/nonexistent/path") is False


def test_parse_metrics_report_missing():
    result = parse_metrics_report("/nonexistent/metrics.txt")
    assert result["duration"] == 0.0
    assert result["timeseries"] == []


def test_parse_metrics_report_valid():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("# CADL Metrics Report\n")
        f.write("# Duration: 300.0s\n")
        f.write("# Robots: 5\n")
        f.write("#\n")
        f.write("# time[s]\tgoal_sum\tgoal_min\tcollision_rate\tretry_rate\n")
        f.write("10.0\t5\t1\t0.1000\t0.0500\n")
        f.write("20.0\t12\t2\t0.0800\t0.0400\n")
        f.write("#\n")
        f.write("# Target Evaluation:\n")
        f.write("#   goal_sum = 12.0000 >= 0 -> PASS\n")
        f.name_path = f.name

    try:
        result = parse_metrics_report(f.name_path)
        assert result["duration"] == 300.0
        assert result["num_robots"] == 5
        assert len(result["timeseries"]) == 2
        assert result["final"]["goal_sum"] == 12
        assert "goal_sum" in result["targets"]
    finally:
        os.unlink(f.name_path)
