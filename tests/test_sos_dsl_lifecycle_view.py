"""Tests for the SoS-DSL Lifecycle View renderer.

Stdlib-only smoke tests for cadl_sim.sos_dsl.lifecycle_view; the
Streamlit page itself is not exercised here.
"""

import json
from pathlib import Path

import pytest

from cadl_sim.sos_dsl import (
    LifecycleView,
    build_lifecycle_view,
    lifecycle_to_dot,
    monitors_summary,
)


EXAMPLE = (
    Path(__file__).parent.parent
    / "cadl_sim" / "sos_dsl" / "examples"
    / "sos_dsl_robot_delivery.ir.json"
)


@pytest.fixture(scope="module")
def ir_doc():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def contract(ir_doc):
    return ir_doc["institution"]["contracts"][0]


class TestBuildView:
    def test_build_view_extracts_lifecycle(self, contract):
        v = build_lifecycle_view(contract)
        assert isinstance(v, LifecycleView)
        assert v.contract_id == "DELIVERY_SLA"
        assert v.initial == "Proposed"
        assert "Violated" in v.terminal
        assert len(v.transitions) == 5

    def test_build_view_returns_none_without_lifecycle(self):
        assert build_lifecycle_view({"id": "X"}) is None
        assert build_lifecycle_view({"id": "X", "lifecycle": None}) is None


class TestDOTRendering:
    def test_dot_contains_all_states(self, contract):
        v = build_lifecycle_view(contract)
        dot = lifecycle_to_dot(v)
        for state in [
            "Proposed", "Assigned", "Accepted",
            "Delivering", "Completed", "Violated", "Terminated",
        ]:
            assert f'"{state}"' in dot

    def test_initial_state_is_doublecircle(self, contract):
        v = build_lifecycle_view(contract)
        dot = lifecycle_to_dot(v)
        assert "doublecircle" in dot
        # initial node line carries doublecircle
        assert any(
            "Proposed" in line and "doublecircle" in line
            for line in dot.splitlines()
        )

    def test_terminal_states_are_dashed(self, contract):
        v = build_lifecycle_view(contract)
        dot = lifecycle_to_dot(v)
        # at least one terminal state line uses dashed style
        terminal_lines = [
            line for line in dot.splitlines()
            if any(t in line for t in ["Completed", "Violated", "Terminated"])
            and "shape=box" in line
        ]
        assert any("dashed" in line for line in terminal_lines)

    def test_deadline_emits_label_with_delta(self, contract):
        v = build_lifecycle_view(contract)
        dot = lifecycle_to_dot(v)
        # "Δ 5s" appears for the accept transition (5000 ms)
        assert "5s" in dot

    def test_on_violation_emits_dashed_red_edge(self, contract):
        v = build_lifecycle_view(contract)
        dot = lifecycle_to_dot(v)
        # the accept transition has on_violation: Violated; that lift
        # should appear as a dashed red edge to Violated
        assert "color=red" in dot
        assert "violation" in dot

    def test_state_set_from_expands_to_multiple_edges(self, contract):
        """`from: [Assigned, Accepted, Delivering]` produces 3 edges."""
        v = build_lifecycle_view(contract)
        dot = lifecycle_to_dot(v)
        # Each of the three from-states gets an edge to Violated
        for fr in ("Assigned", "Accepted", "Delivering"):
            assert (
                f'"{fr}" -> "Violated"' in dot
            ), f"missing edge from {fr} to Violated"


class TestMonitorsSummary:
    def test_three_rows(self, contract):
        rows = monitors_summary(contract)
        assert [r["id"] for r in rows] == [
            "battery_guard", "collision_watch", "deadline_watch",
        ]

    def test_periodic_sampling_label(self, contract):
        rows = monitors_summary(contract)
        bg = next(r for r in rows if r["id"] == "battery_guard")
        assert bg["sampling"] == "periodic(500ms)"

    def test_critical_severity_visible(self, contract):
        rows = monitors_summary(contract)
        cw = next(r for r in rows if r["id"] == "collision_watch")
        assert cw["severity"] == "Critical"
