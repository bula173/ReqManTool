# ReqManTool — Tool Requirements Specification (T2 Qualification)

**Version:** 1.0  
**Date:** 2026-10-10  
**Classification:** Internal — Safety-Related Tool Assessment  
**Tool:** ReqManTool (Requirements Management Tool)  
**Classification:** T2 per EN 50128 §6.7.2  
**Target SIL:** Up to SIL4 (tool supports SIL4 projects)

---

## 1. Introduction

### 1.1 Purpose
This document specifies the functional and non-functional requirements for ReqManTool when used as a T2 tool in EN 50128 safety-critical development. It serves as the basis for tool qualification per EN 50128 §6.7.

### 1.2 Scope
ReqManTool is a requirements management tool supporting:
- Hierarchical requirements (projects → modules → requirements)
- Custom attribute schemas (string, text, integer, boolean, enum, date)
- Directional traceability links with configurable link types
- Cross-project references and links
- Baselines (immutable snapshots via git tags)
- Verification/execution/impact reports
- ReqIF/CSV/XLSX/DOCX export
- Test result import (JUnit XML, Robot Framework)
- Git-backed history with auto-commit and baselines

### 1.3 Definitions
| Term | Definition |
|------|------------|
| **T2 Tool** | Tool whose output is independently verified; failure detectable by verification (EN 50128 §6.7.2) |
| **SIL** | Safety Integrity Level (0-4 per EN 50128) |
| **Baseline** | Named, immutable snapshot of project state (git annotated tag) |
| **Link Configuration** | Directional named link type between two specific modules |
| **Requirement ID** | `<PREFIX>-<NNNN>` format, globally unique within project |

---

## 2. Functional Requirements

### 2.1 Project & Module Management
| ID | Requirement | Priority | Verification |
|----|-------------|----------|--------------|
| FR-PM-01 | Create blank project with name, description | Mandatory | Test: `test_create_project_seeds_builtin_attributes` |
| FR-PM-02 | Create project from template (RBC_Template) | Mandatory | Test: `test_create_project_from_template` |
| FR-PM-03 | Create module with name, prefix, type | Mandatory | Test: `test_create_module_generates_slug_id_and_keeps_prefix` |
| FR-PM-04 | Module types: requirements, test_cases, test_execution, operational_scenarios | Mandatory | Test: `test_date_and_module_types` |
| FR-PM-05 | Module folders (hierarchical organisation) | Mandatory | Test: `test_module_folders` |
| FR-PM-06 | Subprojects (generic → specific hierarchy) | Mandatory | Test: `test_project_references` |
| FR-PM-07 | Project references (cross-project links) | Mandatory | Test: `test_project_references` |

### 2.2 Requirement Management
| ID | Requirement | Priority | Verification |
|----|-------------|----------|--------------|
| FR-RM-01 | Create requirement with ObjectText attribute | Mandatory | Test: `test_add_requirement_requires_title` |
| FR-RM-02 | Hierarchical nesting (parent_id, order) | Mandatory | Test: `test_add_requirement_with_parent` |
| FR-RM-03 | Move requirement (reparent, reorder) | Mandatory | Test: `test_move_requirement` |
| FR-RM-04 | Copy requirement (subtree) | Mandatory | Test: `test_copy_requirement` |
| FR-RM-05 | Delete requirement (promotes children) | Mandatory | Test: `test_remove_requirement_promotes_children` |
| FR-RM-06 | Unique requirement IDs (PREFIX-NNNN) | Mandatory | Test: `test_add_requirement_allocates_prefixed_id` |
| FR-RM-07 | Shared ID counter per prefix across modules | Mandatory | Test: `test_add_requirement_shares_counter_across_modules_with_same_prefix` |

### 2.3 Attribute Management
| ID | Requirement | Priority | Verification |
|----|-------------|----------|--------------|
| FR-AT-01 | Custom attributes: string, text, integer, boolean, enum, date | Mandatory | Test: `test_add_attribute_definition` |
| FR-AT-02 | Required attributes with validation | Mandatory | Test: `test_add_attribute_definition_required_with_empty_default_rejected` |
| FR-AT-03 | Enum attributes with choices | Mandatory | Test: `test_add_attribute_definition_enum` |
| FR-AT-04 | Attribute scoping by module/module type | Mandatory | Test: `test_attribute_applicability` |
| FR-AT-05 | System attributes (read-only, link mirrors) | Mandatory | Test: `test_create_link_configuration_adds_system_attribute` |
| FR-AT-06 | Attribute default values with type checking | Mandatory | Test: `test_attribute_default_validation` |

### 2.4 Traceability Links
| ID | Requirement | Priority | Verification |
|----|-------------|----------|--------------|
| FR-TL-01 | Link configurations (directional, typed) | Mandatory | Test: `test_create_link_configuration` |
| FR-TL-02 | Link direction enforcement (source→target) | Mandatory | Test: `test_link_requirements_wrong_direction_raises` |
| FR-TL-03 | Cross-project links with project-qualified IDs | Mandatory | Test: `test_cross_project_links` |
| FR-TL-04 | Link mirrors (read-only, auto-updated) | Mandatory | Test: `test_link_requirements_across_modules` |
| FR-TL-05 | Unlink clears mirrors on both sides | Mandatory | Test: `test_unlink_requirements_clears_both_mirrors` |
| FR-TL-06 | Link type validation (allowed module type pairs) | Mandatory | Test: `test_link_type_validation` |

### 2.5 Baselines & History
| ID | Requirement | Priority | Verification |
|----|-------------|----------|--------------|
| FR-BH-01 | Git auto-commit on every mutation | Mandatory | Test: `test_git_history` |
| FR-BH-02 | Baseline creation (annotated git tag) | Mandatory | Test: `test_baseline_creation` |
| FR-BH-03 | Baseline immutability (fast-forward branch) | Mandatory | Test: `test_baseline_immutability` |
| FR-BH-04 | Requirement history with per-attribute diffs | Mandatory | Test: `test_requirement_history` |
| FR-BH-05 | Baseline compare (requirement-level diffs) | Mandatory | Test: `test_baseline_compare` |
| FR-BH-06 | Commit squashing (10-min window, same author) | Mandatory | Test: `test_git_squash` |

### 2.6 Reports
| ID | Requirement | Priority | Verification |
|----|-------------|----------|--------------|
| FR-RP-01 | Traceability matrix (chains up to 10 configs) | Mandatory | Test: `test_traceability_matrix` |
| FR-RP-02 | Verification report (requirements ↔ test cases) | Mandatory | Test: `test_verification_report` |
| FR-RP-03 | Test execution report | Mandatory | Test: `test_execution_report` |
| FR-RP-04 | Impact analysis (breadth-first, depth ≤10) | Mandatory | Test: `test_impact_analysis` |
| FR-RP-05 | Verification status column (computed) | Mandatory | Test: `test_verification_status` |

### 2.7 Import/Export
| ID | Requirement | Priority | Verification |
|----|-------------|----------|--------------|
| FR-IE-01 | ReqIF export (SPEC-OBJECT, SPEC-RELATION) | Mandatory | Test: `test_export_reqif` |
| FR-IE-02 | CSV export/import (round-trip) | Mandatory | Test: `test_csv_import_export_roundtrip` |
| FR-IE-03 | XLSX export | Mandatory | Test: `test_export_xlsx` |
| FR-IE-04 | DOCX export (rich text formatting) | Mandatory | Test: `test_export_docx` |
| FR-IE-05 | Test result import (JUnit XML, Robot Framework) | Mandatory | Test: `test_test_results_import` |
| FR-IE-06 | CSV import with links (`Link: <config>` columns) | Mandatory | Test: `test_csv_import_with_links` |
| FR-IE-07 | CSV import update mode | Mandatory | Test: `test_csv_import_update_mode` |

### 2.8 Templates
| ID | Requirement | Priority | Verification |
|----|-------------|----------|--------------|
| FR-TP-01 | Built-in RBC_Template (EN 50126/50128/50129) | Mandatory | Test: `test_rbc_template` |
| FR-TP-02 | Save project as template | Mandatory | Test: `test_save_project_as_template` |
| FR-TP-03 | Import template file | Mandatory | Test: `test_import_template` |
| FR-TP-04 | Module templates with heading skeletons | Mandatory | Test: `test_module_templates` |
| FR-TP-04 | Apply chapter template to existing module | Mandatory | Test: `test_apply_skeleton` |

### 2.9 Server Edition (Multi-user)
| ID | Requirement | Priority | Verification |
|----|-------------|----------|--------------|
| FR-SE-01 | OIDC authentication (GitLab, GitHub, generic) | Mandatory | Test: `test_server_login` |
| FR-SE-02 | Role-based access (viewer, reviewer, editor, project-admin) | Mandatory | Test: `test_permissions` |
| FR-SE-02 | Module edit locks (checkout/break) | Mandatory | Test: `test_module_locks` |
| FR-SE-03 | Audit log (append-only JSONL) | Mandatory | Test: `test_audit_log` |
| FR-SE-04 | Repository permission sync (GitLab/GitHub) | Mandatory | Test: `test_repo_access` |

---

## 3. Non-Functional Requirements

### 3.1 Data Integrity (SIL4-critical)
| ID | Requirement | Verification |
|----|-------------|--------------|
| NF-DI-01 | Atomic writes (temp file + os.replace + fsync/F_FULLFSYNC) | Code review + test |
| NF-DI-02 | All-or-nothing multi-file operations (staging dir + os.rename) | Test: `test_template_creation_atomic` |
| NF-DI-03 | Format versioning with in-memory upgrades only | Code review |
| NF-DI-04 | ID allocation never reuses counters | Test: `test_purge_does_not_reuse_ids` |
| NF-DI-05 | Cross-project link cascade cleanup | Test: `test_remove_requirement_cleans_cross_project_links` |

### 3.2 Determinism (T2 requirement)
| ID | Requirement | Verification |
|----|-------------|--------------|
| NF-DT-01 | Report outputs deterministic (same input → same output) | Test: `test_report_determinism` |
| NF-DT-02 | Baseline immutability (git tag + fast-forward branch) | Test: `test_baseline_immutability` |
| NF-DT-03 | Cross-project links deterministic (project-qualified IDs) | Code review |
| NF-DT-04 | Export determinism (stable field order) | Code review |

### 3.3 Performance
| ID | Requirement | Target |
|----|-------------|--------|
| NF-PF-01 | Mutation latency (git commit) | < 100ms |
| NF-PF-02 | Report generation (10k requirements) | < 5s |
| NF-PF-04 | ReqIF export (10k requirements) | < 30s |

### 3.4 Security
| ID | Requirement | Verification |
|----|-------------|--------------|
| NF-SE-01 | TLS 1.2+ for production server edition | Config check |
| NF-SE-02 | CSRF protection (synchronizer token) | Code review |
| NF-SE-03 | Path traversal prevention (`_is_project_id`) | Code review |
| NF-SE-05 | SQL injection N/A (no database) | N/A |

### 3.5 Usability
| ID | Requirement |
|----|-------------|
| NF-US-01 | Single-page React app with live sync (WebSocket) |
| NF-US-02 | Keyboard shortcuts for common actions |
| NF-US-03 | Collapsible/resizable side panels |
| NF-US-04 | Dark/light mode |

---

## 4. Interface Requirements

### 4.1 REST API
- Base path: `/projects/{id}/...`
- JSON request/response
- Standard HTTP status codes (200, 201, 400, 403, 404, 409, 423)
- Error format: `{"detail": "message"}`

### 4.2 WebSocket JSON-RPC 2.0
- Endpoint: `/ws`
- Method names mirror REST (`project.create`, `requirement.add`, etc.)
- `MUTATING_METHODS` broadcast `changed` events
- Identical error mapping to REST

### 4.3 Export Formats
| Format | Standard | Validation |
|--------|----------|------------|
| ReqIF | ISO 26262 / IEC 61360 | `reqif` Python package |
| CSV | RFC 4180 | Round-trip test |
| XLSX | OOXML (ISO 29500) | `openpyxl` |
| DOCX | OOXML | `python-docx` |

### 4.4 Import Formats
| Format | Parser | Safety |
|--------|--------|--------|
| CSV | Custom (RFC 4180) | Size limit 10 MiB |
| JUnit XML | stdlib `expat` | No external entities |
| Robot Framework XML | stdlib `expat` | Size limit 64 MiB |

---

## 5. Operational Constraints

| Constraint | Description |
|------------|-------------|
| OC-01 | Single active writer per data directory (last-write-wins) |
| OC-02 | Data directory on local filesystem (not NFS/SMB) |
| OC-03 | git 2.30+ in PATH (or `REQMAN_GIT_EXECUTABLE`) |
| OC-04 | Server edition required for multi-user |
| OC-05 | TLS certificates required for production server edition |
| OC-06 | Backups of data directory are operator responsibility |

---

## 5. Traceability Matrix

| Req ID | EN 50128 Clause | Test Case | Status |
|--------|-----------------|-----------|--------|
| FR-PM-01 | §6.2.2 | `test_create_project_seeds_builtin_attributes` | ✅ |
| FR-RM-01 | §6.2.2 | `test_add_requirement_requires_title` | ✅ |
| FR-RM-02 | §6.2.4 | `test_add_requirement_with_parent` | ✅ |
| FR-AT-01 | §6.2.3 | `test_add_attribute_definition` | ✅ |
| FR-TL-01 | §6.2.4 | `test_create_link_configuration` | ✅ |
| FR-TL-02 | §6.2.4 | `test_link_requirements_wrong_direction_raises` | ✅ |
| FR-BH-01 | §6.6 | `test_git_history` | ✅ |
| FR-BH-02 | §6.6 | `test_baseline_creation` | ✅ |
| FR-RP-01 | §6.5 | `test_traceability_matrix` | ✅ |
| FR-RP-02 | §6.5 | `test_verification_report` | ✅ |
| FR-IE-01 | §6.5 | `test_export_reqif` | ✅ |
| FR-IE-02 | §6.2.2 | `test_csv_import_export_roundtrip` | ✅ |
| FR-TP-01 | §6.2.2 | `test_rbc_template` | ✅ |

---

## 6. Acceptance Criteria

The tool is accepted for T2 qualification when:
1. ✅ All Mandatory requirements have passing test cases
2. ✅ All test suites pass (379+ tests)
3. ✅ Code coverage ≥ 70% (target: 90%)
4. ✅ No critical static analysis findings
5. ☐ Tool Requirements Specification approved
6. ☐ Tool V&V Plan approved
7. ☐ Tool Qualification Kit assembled
8. ☐ Independent assessor sign-off

---

*End of Tool Requirements Specification*