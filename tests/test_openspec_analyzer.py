"""Tests for OpenSpecAnalyzer — pure-Python parser unit tests and an
optional integration test that requires a live FalkorDB instance.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from api.analyzers.openspec.analyzer import (
    OpenSpecAnalyzer,
    _parse_delta_spec_md,
    _parse_spec_md,
    _parse_tasks_md,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_spec_md(capabilities: list[tuple[str, list[tuple[str, list[str]]]]]) -> str:
    """Build a minimal spec.md string from a declarative structure.

    capabilities is a list of (req_name, [scenario_names]) tuples.
    """
    lines = ["# Test Spec", "", "## Purpose", "", "This is the purpose.", ""]
    for req_name, scenarios in capabilities:
        lines += [f"### Requirement: {req_name}", "", f"{req_name} SHALL do things.", ""]
        for scen_name in scenarios:
            lines += [
                f"#### Scenario: {scen_name}",
                "- **WHEN** something happens",
                "- **THEN** something occurs",
                "",
            ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Unit tests: _parse_spec_md
# ---------------------------------------------------------------------------


class TestParseSpecMd:
    def test_purpose_extracted(self):
        md = textwrap.dedent("""\
            # My Cap

            ## Purpose

            This is the purpose text.

            ## Requirements
            ### Requirement: First Req
            First req SHALL work.
        """)
        purpose, reqs = _parse_spec_md(md)
        assert "purpose text" in purpose

    def test_requirement_count(self):
        md = _make_spec_md([
            ("Alpha", ["Scenario A1", "Scenario A2"]),
            ("Beta", ["Scenario B1"]),
        ])
        _, reqs = _parse_spec_md(md)
        assert len(reqs) == 2

    def test_requirement_name(self):
        md = _make_spec_md([("My Requirement", ["Scen 1"])])
        _, reqs = _parse_spec_md(md)
        assert reqs[0].name == "My Requirement"

    def test_scenario_count(self):
        md = _make_spec_md([("Req", ["S1", "S2", "S3"])])
        _, reqs = _parse_spec_md(md)
        assert len(reqs[0].scenarios) == 3

    def test_scenario_name(self):
        md = _make_spec_md([("Req", ["My Scenario"])])
        _, reqs = _parse_spec_md(md)
        assert reqs[0].scenarios[0].name == "My Scenario"

    def test_when_then_extracted(self):
        md = textwrap.dedent("""\
            ### Requirement: Auth
            Auth SHALL work.

            #### Scenario: Login
            - **WHEN** user submits credentials
            - **THEN** a session is created
            - **AND** a cookie is set
        """)
        _, reqs = _parse_spec_md(md)
        scen = reqs[0].scenarios[0]
        assert "user submits credentials" in scen.when_clause[0]
        assert len(scen.then_clause) == 2  # THEN + AND

    def test_given_extracted(self):
        md = textwrap.dedent("""\
            ### Requirement: State
            State SHALL persist.

            #### Scenario: With state
            - **GIVEN** user is logged in
            - **WHEN** user navigates
            - **THEN** session is maintained
        """)
        _, reqs = _parse_spec_md(md)
        scen = reqs[0].scenarios[0]
        assert scen.given[0] == "user is logged in"

    def test_line_numbers(self):
        md = textwrap.dedent("""\
            ### Requirement: Req A
            Body text.

            #### Scenario: Scen A
            - **WHEN** x
            - **THEN** y
        """)
        _, reqs = _parse_spec_md(md)
        assert reqs[0].lineno == 1
        assert reqs[0].scenarios[0].lineno == 4

    def test_empty_spec(self):
        purpose, reqs = _parse_spec_md("")
        assert purpose == ""
        assert reqs == []

    def test_requirement_body_text(self):
        md = textwrap.dedent("""\
            ### Requirement: Speed
            The system SHALL respond within 100ms.

            #### Scenario: Fast response
            - **WHEN** a request is made
            - **THEN** it is served in under 100ms
        """)
        _, reqs = _parse_spec_md(md)
        assert "100ms" in reqs[0].text


# ---------------------------------------------------------------------------
# Unit tests: _parse_delta_spec_md
# ---------------------------------------------------------------------------


class TestParseDeltaSpecMd:
    def test_added_section(self):
        md = textwrap.dedent("""\
            ## ADDED Requirements

            ### Requirement: New Feature
            New Feature SHALL do something new.

            #### Scenario: New scenario
            - **WHEN** invoked
            - **THEN** it works
        """)
        sections = _parse_delta_spec_md(md)
        assert len(sections) == 1
        assert sections[0].delta_type == "ADDED"
        assert len(sections[0].requirements) == 1
        assert sections[0].requirements[0][0] == "New Feature"

    def test_multiple_delta_types(self):
        md = textwrap.dedent("""\
            ## ADDED Requirements

            ### Requirement: New Req
            Added req body.

            ## MODIFIED Requirements

            ### Requirement: Existing Req
            Modified body.

            ## REMOVED Requirements

            ### Requirement: Old Req
            Old req body.
        """)
        sections = _parse_delta_spec_md(md)
        delta_types = [s.delta_type for s in sections]
        assert "ADDED" in delta_types
        assert "MODIFIED" in delta_types
        assert "REMOVED" in delta_types

    def test_added_requirement_count(self):
        md = textwrap.dedent("""\
            ## ADDED Requirements

            ### Requirement: Req One
            Body one.

            ### Requirement: Req Two
            Body two.
        """)
        sections = _parse_delta_spec_md(md)
        assert len(sections[0].requirements) == 2

    def test_renamed_section(self):
        md = textwrap.dedent("""\
            ## RENAMED Requirements
            - FROM: `### Requirement: Old Name`
            - TO: `### Requirement: New Name`
        """)
        sections = _parse_delta_spec_md(md)
        assert sections[0].delta_type == "RENAMED"
        assert sections[0].requirements[0][0] == "New Name"
        assert "Old Name" in sections[0].requirements[0][1]

    def test_empty_delta(self):
        sections = _parse_delta_spec_md("# Just a title\nNo delta sections here.")
        assert sections == []


# ---------------------------------------------------------------------------
# Unit tests: _parse_tasks_md
# ---------------------------------------------------------------------------


class TestParseTasksMd:
    def test_unchecked_task(self):
        md = "## Phase 1\n- [ ] Do something\n"
        tasks = _parse_tasks_md(md)
        assert len(tasks) == 1
        text, checked, group, task_num, lineno = tasks[0]
        assert text == "Do something"
        assert checked is False
        assert group == "Phase 1"

    def test_checked_task(self):
        md = "## Done\n- [x] Completed task\n"
        tasks = _parse_tasks_md(md)
        assert tasks[0][1] is True

    def test_uppercase_X(self):
        md = "## Done\n- [X] Also done\n"
        tasks = _parse_tasks_md(md)
        assert tasks[0][1] is True

    def test_numeric_prefix_extracted(self):
        md = "## Tasks\n- [ ] 1.1 First subtask\n"
        tasks = _parse_tasks_md(md)
        text, _, _, task_num, _ = tasks[0]
        assert task_num == "1.1"
        assert text == "First subtask"

    def test_task_without_numeric_prefix(self):
        md = "## Tasks\n- [ ] Just a plain task\n"
        tasks = _parse_tasks_md(md)
        _, _, _, task_num, _ = tasks[0]
        assert task_num == ""

    def test_multiple_groups(self):
        md = textwrap.dedent("""\
            ## Group A
            - [x] Task A1
            - [ ] Task A2
            ## Group B
            - [ ] Task B1
        """)
        tasks = _parse_tasks_md(md)
        assert len(tasks) == 3
        groups = [t[2] for t in tasks]
        assert groups[0] == "Group A"
        assert groups[2] == "Group B"

    def test_checked_count(self):
        md = textwrap.dedent("""\
            ## Work
            - [x] Done 1
            - [x] Done 2
            - [ ] Pending 1
            - [ ] Pending 2
            - [ ] Pending 3
        """)
        tasks = _parse_tasks_md(md)
        checked = [t for t in tasks if t[1]]
        unchecked = [t for t in tasks if not t[1]]
        assert len(checked) == 2
        assert len(unchecked) == 3

    def test_line_numbers(self):
        md = "## G\n- [ ] First\n- [x] Second\n"
        tasks = _parse_tasks_md(md)
        assert tasks[0][4] == 2  # line 2
        assert tasks[1][4] == 3  # line 3

    def test_empty_tasks_md(self):
        assert _parse_tasks_md("") == []


# ---------------------------------------------------------------------------
# Unit tests: OpenSpecAnalyzer.analyze_directory with mock graph
# ---------------------------------------------------------------------------


@pytest.fixture()
def openspec_tree(tmp_path: Path) -> Path:
    """Create a minimal openspec/ fixture tree.

    Structure:
        openspec/
          specs/
            my-cap/
              spec.md  (1 Capability, 2 Requirements, 3 Scenarios)
          changes/
            my-change/
              .openspec.yaml
              tasks.md   (5 tasks: 2 checked, 3 unchecked)
              proposal.md
              specs/
                my-cap/
                  spec.md  (ADDED 1 requirement)
    """
    root = tmp_path / "openspec"

    # --- specs/my-cap/spec.md ---
    cap_dir = root / "specs" / "my-cap"
    cap_dir.mkdir(parents=True)
    (cap_dir / "spec.md").write_text(
        textwrap.dedent("""\
            # My Capability

            ## Purpose

            My capability does X.

            ## Requirements

            ### Requirement: Core Behavior
            The system SHALL exhibit core behavior.

            #### Scenario: Happy path
            - **WHEN** normal input is provided
            - **THEN** expected output is returned

            #### Scenario: Edge case
            - **WHEN** edge input is provided
            - **THEN** a warning is emitted

            ### Requirement: Error Handling
            The system SHALL handle errors gracefully.

            #### Scenario: Invalid input
            - **WHEN** invalid input is provided
            - **THEN** an error is returned
        """),
        encoding="utf-8",
    )

    # --- changes/my-change/ ---
    change_dir = root / "changes" / "my-change"
    change_dir.mkdir(parents=True)
    (change_dir / ".openspec.yaml").write_text(
        "schema: spec-driven\ncreated: 2025-01-15\n",
        encoding="utf-8",
    )
    (change_dir / "proposal.md").write_text("## Why\nBecause reasons.\n", encoding="utf-8")
    (change_dir / "tasks.md").write_text(
        textwrap.dedent("""\
            ## Phase 1

            - [x] 1.1 First done task
            - [x] 1.2 Second done task
            - [ ] 1.3 Third pending task

            ## Phase 2

            - [ ] 2.1 Fourth pending task
            - [ ] 2.2 Fifth pending task
        """),
        encoding="utf-8",
    )

    # --- delta spec ---
    delta_cap_dir = change_dir / "specs" / "my-cap"
    delta_cap_dir.mkdir(parents=True)
    (delta_cap_dir / "spec.md").write_text(
        textwrap.dedent("""\
            ## ADDED Requirements

            ### Requirement: New Capability
            The system SHALL also do new things.

            #### Scenario: New scenario
            - **WHEN** new input is provided
            - **THEN** new output is returned
        """),
        encoding="utf-8",
    )

    return root


def _make_mock_graph():
    """Return a MagicMock that tracks add_openspec_* calls and returns sequential IDs."""
    g = MagicMock()
    counter = iter(range(1000))
    g.add_openspec_capability.side_effect = lambda **kw: next(counter)
    g.add_openspec_requirement.side_effect = lambda **kw: next(counter)
    g.add_openspec_scenario.side_effect = lambda **kw: next(counter)
    g.add_openspec_change.side_effect = lambda **kw: next(counter)
    g.add_openspec_task.side_effect = lambda **kw: next(counter)
    g.add_openspec_delta.side_effect = lambda **kw: next(counter)
    return g


class TestOpenSpecAnalyzerMocked:
    def test_capability_created(self, openspec_tree: Path):
        g = _make_mock_graph()
        OpenSpecAnalyzer().analyze_directory(openspec_tree, g)
        assert g.add_openspec_capability.call_count == 1
        kwargs = g.add_openspec_capability.call_args.kwargs
        assert kwargs["name"] == "my-cap"
        assert "My capability does X." in kwargs["purpose"]

    def test_requirement_count(self, openspec_tree: Path):
        g = _make_mock_graph()
        OpenSpecAnalyzer().analyze_directory(openspec_tree, g)
        # 2 requirements in spec.md
        assert g.add_openspec_requirement.call_count == 2

    def test_scenario_count(self, openspec_tree: Path):
        g = _make_mock_graph()
        OpenSpecAnalyzer().analyze_directory(openspec_tree, g)
        # 3 scenarios across 2 requirements
        assert g.add_openspec_scenario.call_count == 3

    def test_change_created(self, openspec_tree: Path):
        g = _make_mock_graph()
        OpenSpecAnalyzer().analyze_directory(openspec_tree, g)
        assert g.add_openspec_change.call_count == 1
        kwargs = g.add_openspec_change.call_args.kwargs
        assert kwargs["name"] == "my-change"
        assert kwargs["schema"] == "spec-driven"

    def test_task_count(self, openspec_tree: Path):
        g = _make_mock_graph()
        OpenSpecAnalyzer().analyze_directory(openspec_tree, g)
        assert g.add_openspec_task.call_count == 5

    def test_task_checked_count(self, openspec_tree: Path):
        g = _make_mock_graph()
        OpenSpecAnalyzer().analyze_directory(openspec_tree, g)
        checked_calls = [
            c for c in g.add_openspec_task.call_args_list
            if c.kwargs.get("checked") is True
        ]
        unchecked_calls = [
            c for c in g.add_openspec_task.call_args_list
            if c.kwargs.get("checked") is False
        ]
        assert len(checked_calls) == 2
        assert len(unchecked_calls) == 3

    def test_delta_created(self, openspec_tree: Path):
        g = _make_mock_graph()
        OpenSpecAnalyzer().analyze_directory(openspec_tree, g)
        assert g.add_openspec_delta.call_count >= 1
        kwargs = g.add_openspec_delta.call_args.kwargs
        assert kwargs["delta_type"] == "ADDED"
        assert kwargs["requirement_name"] == "New Capability"

    def test_no_openspec_dir(self, tmp_path: Path):
        """analyze_directory should handle a missing tree gracefully."""
        g = _make_mock_graph()
        empty_root = tmp_path / "openspec"
        empty_root.mkdir()
        OpenSpecAnalyzer().analyze_directory(empty_root, g)
        g.add_openspec_capability.assert_not_called()


# ---------------------------------------------------------------------------
# Integration test (skipped unless FALKORDB_HOST is set)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.getenv("FALKORDB_HOST"),
    reason="FALKORDB_HOST not set — skipping live integration test",
)
class TestOpenSpecAnalyzerIntegration:
    def test_node_counts_in_graph(self, openspec_tree: Path):
        import uuid
        from api.graph import Graph

        proj = f"test-openspec-{uuid.uuid4().hex[:8]}"
        g = Graph(proj)
        try:
            OpenSpecAnalyzer().analyze_directory(openspec_tree, g)

            caps = g._query("MATCH (c:OpenSpecCapability) RETURN count(c) AS n").result_set
            assert caps[0][0] >= 1, "expected at least 1 capability"

            reqs = g._query("MATCH (r:OpenSpecRequirement) RETURN count(r) AS n").result_set
            assert reqs[0][0] >= 2, "expected at least 2 requirements"

            scens = g._query("MATCH (s:OpenSpecScenario) RETURN count(s) AS n").result_set
            assert scens[0][0] >= 3, "expected at least 3 scenarios"

            changes = g._query("MATCH (c:OpenSpecChange) RETURN count(c) AS n").result_set
            assert changes[0][0] >= 1, "expected at least 1 change"

            tasks = g._query("MATCH (t:OpenSpecTask) RETURN count(t) AS n").result_set
            assert tasks[0][0] >= 5, "expected at least 5 tasks"

        finally:
            # Clean up test graph
            try:
                from api.db import create_falkordb
                db = create_falkordb()
                from api.graph import compose_graph_name
                gname = compose_graph_name(proj)
                if gname in db.list_graphs():
                    db.select_graph(gname).delete()
            except Exception:
                pass
