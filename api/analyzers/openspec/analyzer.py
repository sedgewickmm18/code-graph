"""OpenSpec analyzer — extracts Capability, Requirement, Scenario, Change, Task,
and DeltaSpec nodes from an ``openspec/`` directory tree.

Directory layout understood by this analyzer::

    openspec/
    ├── specs/
    │   └── <capability>/
    │       └── spec.md        # WHAT + WHY (requirements + scenarios)
    └── changes/
        └── <change-name>/
            ├── .openspec.yaml # schema, created, skip_specs, dependsOn
            ├── proposal.md    # free-form change rationale
            ├── tasks.md       # checkbox task lists
            └── specs/
                └── <capability>/
                    └── spec.md  # delta: ADDED / MODIFIED / REMOVED / RENAMED

Graph nodes created:
  - ``OpenSpecCapability``  — capability directory
  - ``OpenSpecRequirement`` — ``### Requirement:`` block  (:Searchable)
  - ``OpenSpecScenario``    — ``#### Scenario:`` block    (:Searchable)
  - ``OpenSpecChange``      — change directory
  - ``OpenSpecTask``        — checkbox item from tasks.md
  - ``OpenSpecDeltaSpec``   — delta spec file

Pure Python implementation (no tree-sitter).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from api.graph import Graph

try:
    import yaml  # PyYAML (already a transitive dep via various packages)
    _YAML_AVAILABLE = True
except ImportError:  # pragma: no cover
    _YAML_AVAILABLE = False

logger = logging.getLogger("code_graph")

# ---------------------------------------------------------------------------
# Compiled regexes
# ---------------------------------------------------------------------------

# H2 heading (general)
_H2 = re.compile(r"^##\s+(.+)$", re.MULTILINE)

# Checkbox task item: "- [ ] text" or "- [x] text"
_TASK_RE = re.compile(r"^\s*-\s+\[([ xX])\]\s+(.+)$", re.MULTILINE)

# Numeric prefix like "1.1" at start of task text
_TASK_NUM_RE = re.compile(r"^(\d+(?:\.\d+)*)\s+(.*)")

# Bold keyword bullets: "- **GIVEN** text", "- **WHEN** text", etc.
_STEP_RE = re.compile(
    r"^\s*-\s+\*\*(GIVEN|WHEN|THEN|AND)\*\*\s*(.*)$", re.IGNORECASE
)

# FROM: / TO: lines inside RENAMED sections
_FROM_RE = re.compile(r"^-\s+FROM:\s+`###\s+Requirement:\s+(.+?)`", re.MULTILINE)
_TO_RE = re.compile(r"^-\s+TO:\s+`###\s+Requirement:\s+(.+?)`", re.MULTILINE)


# ---------------------------------------------------------------------------
# Data classes (plain dicts for lightweight parsing)
# ---------------------------------------------------------------------------

class _ParsedRequirement:
    __slots__ = ("name", "text", "lineno", "scenarios")

    def __init__(self, name: str, text: str, lineno: int) -> None:
        self.name = name
        self.text = text
        self.lineno = lineno
        self.scenarios: list[_ParsedScenario] = []


class _ParsedScenario:
    __slots__ = ("name", "given", "when_clause", "then_clause", "lineno")

    def __init__(self, name: str, lineno: int) -> None:
        self.name = name
        self.given: list[str] = []
        self.when_clause: list[str] = []
        self.then_clause: list[str] = []
        self.lineno = lineno


class _ParsedDeltaSection:
    __slots__ = ("delta_type", "requirements")

    def __init__(self, delta_type: str) -> None:
        self.delta_type = delta_type
        # List of (req_name, req_text, lineno)
        self.requirements: list[tuple[str, str, int]] = []


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_spec_md(text: str) -> tuple[str, list[_ParsedRequirement]]:
    """Parse a spec.md file.

    Returns:
        (purpose_text, requirements)
    """
    lines = text.splitlines()
    purpose_lines: list[str] = []
    in_purpose = False

    requirements: list[_ParsedRequirement] = []
    current_req: Optional[_ParsedRequirement] = None
    current_scenario: Optional[_ParsedScenario] = None
    req_body_lines: list[str] = []

    for lineno, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip()

        # --- H2 sections ---
        h2 = _H2.match(line)
        if h2:
            # Finish any open requirement body
            if current_req is not None and req_body_lines:
                current_req.text = " ".join(req_body_lines).strip()
                req_body_lines = []

            title = h2.group(1).strip()
            in_purpose = title.lower() == "purpose"
            current_req = None
            current_scenario = None
            continue

        # --- Collect Purpose text ---
        if in_purpose and line:
            # Skip lines that start a new section
            if not line.startswith("#"):
                purpose_lines.append(line)
            continue

        # --- Level-3 Requirement heading ---
        if line.startswith("###") and not line.startswith("####"):
            req_m = re.match(r"^###\s+Requirement:\s+(.+)$", line)
            if req_m:
                if current_req is not None and req_body_lines:
                    current_req.text = " ".join(req_body_lines).strip()
                    req_body_lines = []

                current_req = _ParsedRequirement(
                    name=req_m.group(1).strip(),
                    text="",
                    lineno=lineno,
                )
                current_scenario = None
                requirements.append(current_req)
                continue

        # --- Level-4 Scenario heading ---
        if line.startswith("####"):
            scen_m = re.match(r"^####\s+Scenario:\s+(.+)$", line)
            if scen_m and current_req is not None:
                # Flush requirement body
                if req_body_lines:
                    current_req.text = " ".join(req_body_lines).strip()
                    req_body_lines = []

                current_scenario = _ParsedScenario(
                    name=scen_m.group(1).strip(),
                    lineno=lineno,
                )
                current_req.scenarios.append(current_scenario)
            continue

        # --- Step bullets ---
        step_m = _STEP_RE.match(line)
        if step_m and current_scenario is not None:
            keyword = step_m.group(1).upper()
            content = step_m.group(2).strip()
            if keyword == "GIVEN":
                current_scenario.given.append(content)
            elif keyword == "WHEN":
                current_scenario.when_clause.append(content)
            elif keyword == "THEN":
                current_scenario.then_clause.append(content)
            elif keyword == "AND":
                # Append to whichever list was last populated
                if current_scenario.then_clause:
                    current_scenario.then_clause.append(content)
                elif current_scenario.when_clause:
                    current_scenario.when_clause.append(content)
                else:
                    current_scenario.given.append(content)
            continue

        # --- Requirement body text (before first scenario) ---
        if current_req is not None and current_scenario is None and line:
            if not line.startswith("#"):
                req_body_lines.append(line)

    # Flush last requirement body
    if current_req is not None and req_body_lines:
        current_req.text = " ".join(req_body_lines).strip()

    purpose_text = " ".join(purpose_lines).strip()
    return purpose_text, requirements


def _parse_delta_spec_md(text: str) -> list[_ParsedDeltaSection]:
    """Parse a delta spec.md from a change's specs/ subdirectory.

    Returns a list of _ParsedDeltaSection objects, one per ADDED/MODIFIED/
    REMOVED/RENAMED section found.
    """
    lines = text.splitlines()
    sections: list[_ParsedDeltaSection] = []
    current_section: Optional[_ParsedDeltaSection] = None
    current_req: Optional[_ParsedRequirement] = None
    req_body_lines: list[str] = []

    # For RENAMED sections collect FROM/TO pairs
    renamed_from: Optional[str] = None

    def _flush_req() -> None:
        nonlocal current_req, req_body_lines
        if current_req is not None and current_section is not None:
            if req_body_lines:
                current_req.text = " ".join(req_body_lines).strip()
            current_section.requirements.append(
                (current_req.name, current_req.text, current_req.lineno)
            )
        current_req = None
        req_body_lines = []

    for lineno, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip()

        # --- H2 delta section headers ---
        delta_m = re.match(
            r"^##\s+(ADDED|MODIFIED|REMOVED|RENAMED)\s+Requirements?",
            line,
            re.IGNORECASE,
        )
        if delta_m:
            _flush_req()
            current_section = _ParsedDeltaSection(delta_type=delta_m.group(1).upper())
            sections.append(current_section)
            renamed_from = None
            continue

        # Skip lines outside a recognized delta section
        if current_section is None:
            continue

        # --- H3 Requirement heading ---
        if line.startswith("###") and not line.startswith("####"):
            req_m = re.match(r"^###\s+Requirement:\s+(.+)$", line)
            if req_m:
                _flush_req()
                current_req = _ParsedRequirement(
                    name=req_m.group(1).strip(),
                    text="",
                    lineno=lineno,
                )
                continue

        # --- RENAMED section: FROM / TO ---
        if current_section.delta_type == "RENAMED":
            from_m = _FROM_RE.match(line)
            if from_m:
                renamed_from = from_m.group(1).strip()
                continue
            to_m = _TO_RE.match(line)
            if to_m:
                to_name = to_m.group(1).strip()
                if renamed_from:
                    # Record both names as a single requirement entry using TO name
                    current_section.requirements.append(
                        (to_name, f"(renamed from: {renamed_from})", lineno)
                    )
                    renamed_from = None
                continue

        # --- Requirement body ---
        if current_req is not None and line and not line.startswith("#"):
            req_body_lines.append(line)

    _flush_req()
    return sections


def _parse_tasks_md(text: str) -> list[tuple[str, bool, str, str, int]]:
    """Parse a tasks.md file.

    Returns a list of (text, checked, group, task_num, lineno) tuples.
    """
    lines = text.splitlines()
    current_group = ""
    results: list[tuple[str, bool, str, str, int]] = []

    for lineno, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip()

        h2_m = _H2.match(line)
        if h2_m:
            current_group = h2_m.group(1).strip()
            continue

        task_m = _TASK_RE.match(line)
        if task_m:
            checked = task_m.group(1).lower() == "x"
            raw_text = task_m.group(2).strip()
            # Extract optional numeric prefix
            num_m = _TASK_NUM_RE.match(raw_text)
            if num_m:
                task_num = num_m.group(1)
                text = num_m.group(2).strip()
            else:
                task_num = ""
                text = raw_text
            results.append((text, checked, current_group, task_num, lineno))

    return results


def _load_openspec_yaml(yaml_path: Path) -> dict:
    """Load .openspec.yaml, return {} on any failure."""
    if not yaml_path.exists():
        return {}
    try:
        content = yaml_path.read_text(encoding="utf-8", errors="replace")
        if _YAML_AVAILABLE:
            data = yaml.safe_load(content) or {}
        else:
            # Minimal fallback: parse key: value lines
            data = {}
            for line in content.splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    data[k.strip()] = v.strip()
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("OpenSpecAnalyzer: cannot parse %s: %s", yaml_path, exc)
        return {}


# ---------------------------------------------------------------------------
# Main analyzer
# ---------------------------------------------------------------------------

class OpenSpecAnalyzer:
    """Analyzer for OpenSpec document trees.

    Does **not** subclass ``AbstractAnalyzer``; ``SourceAnalyzer`` calls
    ``analyze_directory`` directly when an ``openspec/`` root is detected.
    """

    def analyze_directory(self, openspec_root: Path, graph: "Graph") -> None:
        """Walk the openspec/ tree and persist all nodes + edges.

        Args:
            openspec_root: Path to the ``openspec/`` directory.
            graph:         Graph instance to write into.
        """
        specs_root = openspec_root / "specs"
        changes_root = openspec_root / "changes"

        # Build a name -> capability_id map so delta specs can link back
        capability_ids: dict[str, int] = {}

        # --- 1. Current specs ---
        if specs_root.is_dir():
            for cap_dir in sorted(specs_root.iterdir()):
                if not cap_dir.is_dir():
                    continue
                spec_file = cap_dir / "spec.md"
                if not spec_file.exists():
                    continue
                try:
                    text = spec_file.read_text(encoding="utf-8", errors="replace")
                except OSError as exc:
                    logger.warning("OpenSpecAnalyzer: cannot read %s: %s", spec_file, exc)
                    continue

                purpose, requirements = _parse_spec_md(text)
                cap_id = graph.add_openspec_capability(
                    name=cap_dir.name,
                    path=str(cap_dir),
                    purpose=purpose,
                    src_file=str(spec_file),
                )
                capability_ids[cap_dir.name] = cap_id
                logger.debug(
                    "OpenSpecAnalyzer: capability %s — %d requirements",
                    cap_dir.name,
                    len(requirements),
                )

                for req in requirements:
                    req_id = graph.add_openspec_requirement(
                        name=req.name,
                        text=req.text,
                        src_file=str(spec_file),
                        src_line=req.lineno,
                        capability_id=cap_id,
                    )
                    for scen in req.scenarios:
                        graph.add_openspec_scenario(
                            name=scen.name,
                            given=" | ".join(scen.given),
                            when_clause=" | ".join(scen.when_clause),
                            then_clause=" | ".join(scen.then_clause),
                            src_file=str(spec_file),
                            src_line=scen.lineno,
                            req_id=req_id,
                        )

        # --- 2. Changes ---
        if not changes_root.is_dir():
            return

        for change_dir in sorted(changes_root.iterdir()):
            if not change_dir.is_dir():
                continue
            # Skip the archive sub-directory's children when iterating at top level
            if change_dir.name == "archive":
                continue

            yaml_data = _load_openspec_yaml(change_dir / ".openspec.yaml")
            change_id = graph.add_openspec_change(
                name=change_dir.name,
                path=str(change_dir),
                created=str(yaml_data.get("created", "")),
                schema=str(yaml_data.get("schema", "")),
                skip_specs=bool(yaml_data.get("skip_specs", False)),
            )

            # tasks.md
            tasks_file = change_dir / "tasks.md"
            if tasks_file.exists():
                try:
                    tasks_text = tasks_file.read_text(encoding="utf-8", errors="replace")
                except OSError as exc:
                    logger.warning("OpenSpecAnalyzer: cannot read %s: %s", tasks_file, exc)
                    tasks_text = ""
                for text, checked, group, task_num, src_line in _parse_tasks_md(tasks_text):
                    graph.add_openspec_task(
                        text=text,
                        checked=checked,
                        group=group,
                        task_num=task_num,
                        src_file=str(tasks_file),
                        src_line=src_line,
                        change_id=change_id,
                    )

            # delta specs under changes/<name>/specs/
            delta_specs_root = change_dir / "specs"
            if not delta_specs_root.is_dir():
                continue

            for delta_cap_dir in sorted(delta_specs_root.iterdir()):
                if not delta_cap_dir.is_dir():
                    continue
                delta_spec_file = delta_cap_dir / "spec.md"
                if not delta_spec_file.exists():
                    continue
                try:
                    delta_text = delta_spec_file.read_text(encoding="utf-8", errors="replace")
                except OSError as exc:
                    logger.warning(
                        "OpenSpecAnalyzer: cannot read %s: %s", delta_spec_file, exc
                    )
                    continue

                sections = _parse_delta_spec_md(delta_text)
                cap_id = capability_ids.get(delta_cap_dir.name)

                for section in sections:
                    for req_name, req_text, _lineno in section.requirements:
                        graph.add_openspec_delta(
                            capability_name=delta_cap_dir.name,
                            change_name=change_dir.name,
                            src_file=str(delta_spec_file),
                            delta_type=section.delta_type,
                            requirement_name=req_name,
                            req_text=req_text,
                            change_id=change_id,
                            capability_id=cap_id,
                        )

        logger.info(
            "OpenSpecAnalyzer: finished analyzing %s (%d capabilities)",
            openspec_root,
            len(capability_ids),
        )
