# OpenSpec Graph Schema

This document describes the node labels, properties, and relationships added to
the code-graph FalkorDB topology when an `openspec/` directory is detected in
an indexed repository.

---

## Node Labels

### `OpenSpecCapability`

Represents a capability directory under `openspec/specs/<capability>/`.

| Property    | Type   | Description                                        |
|-------------|--------|----------------------------------------------------|
| `name`      | string | Directory name (e.g. `cli-init`)                   |
| `path`      | string | Absolute path to the capability directory          |
| `purpose`   | string | Text from the `## Purpose` section of `spec.md`    |
| `src_file`  | string | Absolute path to the `spec.md` file                |

---

### `OpenSpecRequirement` `:Searchable`

Represents a `### Requirement: <Name>` block inside a `spec.md`.

| Property    | Type   | Description                                        |
|-------------|--------|----------------------------------------------------|
| `name`      | string | Requirement name (text after `Requirement:`)       |
| `text`      | string | Full body text of the requirement paragraph        |
| `src_file`  | string | Absolute path to the containing spec file          |
| `src_line`  | int    | Line number of the `### Requirement:` heading      |

Also receives the `:Searchable` multi-label so it surfaces in auto-complete and
GraphRAG full-text queries.

---

### `OpenSpecScenario` `:Searchable`

Represents a `#### Scenario: <Name>` block inside a `spec.md`.

| Property      | Type   | Description                                          |
|---------------|--------|------------------------------------------------------|
| `name`        | string | Scenario name (text after `Scenario:`)               |
| `given`       | string | Concatenated GIVEN/AND bullets (may be empty)        |
| `when_clause` | string | Concatenated WHEN/AND bullets                        |
| `then_clause` | string | Concatenated THEN/AND bullets                        |
| `src_file`    | string | Absolute path to the containing spec file            |
| `src_line`    | int    | Line number of the `#### Scenario:` heading          |

Also receives the `:Searchable` label.

---

### `OpenSpecChange`

Represents a change directory under `openspec/changes/<name>/`.

| Property     | Type   | Description                                              |
|--------------|--------|----------------------------------------------------------|
| `name`       | string | Directory name (e.g. `add-change-stacking-awareness`)    |
| `path`       | string | Absolute path to the change directory                    |
| `created`    | string | ISO date string from `.openspec.yaml` `created` field    |
| `schema`     | string | Schema name from `.openspec.yaml` `schema` field         |
| `skip_specs` | bool   | Whether `skip_specs` is set in `.openspec.yaml`          |

---

### `OpenSpecTask`

Represents a single checkbox item (`- [ ]` or `- [x]`) from a `tasks.md`.

| Property   | Type   | Description                                           |
|------------|--------|-------------------------------------------------------|
| `text`     | string | Task description text                                 |
| `checked`  | bool   | `true` if the checkbox is marked (`[x]` or `[X]`)    |
| `group`    | string | H2 group heading the task belongs to                  |
| `task_num` | string | Numeric prefix (e.g. `1.1`) if present, else empty    |
| `src_file` | string | Absolute path to the `tasks.md` file                  |
| `src_line` | int    | Line number of the checkbox item                      |

---

### `OpenSpecDeltaSpec`

Represents a delta spec file inside `openspec/changes/<name>/specs/<capability>/spec.md`.

| Property        | Type   | Description                                               |
|-----------------|--------|-----------------------------------------------------------|
| `capability`    | string | Capability name (directory under the change's `specs/`)   |
| `change_name`   | string | Parent change directory name                              |
| `src_file`      | string | Absolute path to the delta spec file                      |
| `delta_type`    | string | One of `ADDED`, `MODIFIED`, `REMOVED`, `RENAMED`          |

---

## Relationships

| Relationship                        | From                    | To                      | Description                                               |
|-------------------------------------|-------------------------|-------------------------|-----------------------------------------------------------|
| `DEFINES_REQUIREMENT`               | `OpenSpecCapability`    | `OpenSpecRequirement`   | Capability spec owns this requirement                     |
| `HAS_SCENARIO`                      | `OpenSpecRequirement`   | `OpenSpecScenario`      | Requirement contains this scenario                        |
| `CHANGE_TARGETS_CAPABILITY`         | `OpenSpecChange`        | `OpenSpecCapability`    | Change has a delta spec for this capability               |
| `CHANGE_ADDS_REQUIREMENT`           | `OpenSpecChange`        | `OpenSpecRequirement`   | Delta adds this requirement to the capability             |
| `CHANGE_MODIFIES_REQUIREMENT`       | `OpenSpecChange`        | `OpenSpecRequirement`   | Delta modifies this requirement                           |
| `CHANGE_REMOVES_REQUIREMENT`        | `OpenSpecChange`        | `OpenSpecRequirement`   | Delta removes this requirement                            |
| `CHANGE_HAS_TASK`                   | `OpenSpecChange`        | `OpenSpecTask`          | Change owns this task item                                |

---

## Integration with Existing Graph Topology

- `OpenSpecRequirement` and `OpenSpecScenario` carry the `:Searchable`
  multi-label, making them visible in auto-complete prefix search and
  GraphRAG full-text queries without any extra configuration.
- The existing `MarkdownSection`/`Requirement` nodes produced by
  `MarkdownAnalyzer` are **not** created for files under `openspec/`; the
  `OpenSpecAnalyzer` takes ownership of the entire `openspec/` subtree.

---

## Directory Detection

The `OpenSpecAnalyzer` is invoked automatically when any file collected
during source analysis lives under a directory that contains an
`openspec/specs/` subdirectory. The analyzer locates the `openspec/` root
by walking up from the first matched path.
