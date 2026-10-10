# ReqManTool — Test Specification (T2 Qualification)

**Version:** 1.0  
**Date:** 2026-10-10  
**Classification:** Internal — Test Specification for T2 Tool Qualification  
**Reference:** EN 50128 §6.7.3, Annex A  

---

## 1. Introduction

### 1.1 Purpose
This document specifies the test approach, test cases, and acceptance criteria for ReqManTool T2 tool qualification per EN 50128 §6.7.3.

### 1.2 Scope
Covers all functional requirements (Section 2 of Tool Requirements Spec) and non-functional requirements (Section 3).

### 1.3 Test Levels
| Level | Scope | Tools |
|------|-------|-------|
| **Unit** | Individual functions/methods | `pytest` |
| **Integration** | REST + WebSocket parity, dual-transport | `pytest` + `TestClient` |
| **System** | Full workflows (create → link → report → export) | `pytest` |
| **Mutation** | Fault injection in critical modules | `mutmut` |

---

## 2. Test Approach

### 2.1 Test Strategy
- **Black-box testing** via REST API and WebSocket JSON-RPC
- **White-box testing** for critical modules (`service.py`, `store.py`, `git_repo.py`)
- **Dual-transport parity**: every mutation tested via REST and WebSocket
- **Determinism verification**: same input → same output (reports, exports, IDs)
- **Fault injection**: mutation testing on SIL4-critical modules

### 2.2 Test Environment
| Component | Version |
|-----------|---------|
| Python | 3.11, 3.14 |
| pytest | ≥8.0 |
| httpx | ≥0.27 |
| pytest-cov | ≥4.1 |
| mutmut | ≥2.4 |

### 2.3 Test Data Management
- Isolated temporary directories per test (`tmp_path` fixture)
- Git isolated from user config (`GIT_CONFIG_GLOBAL=/dev/null`)
- Fixed author/committer identity for reproducible commits
- `REQMAN_GIT=off` for unit tests (git tests opt-in)

---

## 3. Test Cases

### 3.1 Unit Tests (Module Level)

| Test Module | Target | Key Test Cases |
|-------------|--------|----------------|
| `test_service.py` | `ProjectService` | 267 tests: id allocation, nesting, links, cascades, attributes, templates |
| `test_rich_text.py` | `richtext.py` | Sanitization, plain projection, exports |
| `test_templates.py` | `templates.py` | Template validation, create/save/import |
| `test_reports.py` | Report engine | Traceability, verification, execution, impact |
| `test_templates.py` | Templates | Create/save/import, module templates |

### 3.2 Integration Tests (API Level)

| Test Module | Scope | Key Test Cases |
|-------------|-------|----------------|
| `test_api.py` | REST + WS parity | Dual-transport mutations, live sync broadcast, error mapping |
| `test_login_publish_push.py` | Server edition auth | OIDC flow, git auth env, repo permissions |
| `test_git_history*.py` | Git integration | Auto-commit, baselines, commit browser, conflicts |
| `test_git_remotes.py` | Remote operations | Push, pull, clone, publish, review |

### 3.3 System Tests (End-to-End Workflows)

| Workflow | Steps | Verification |
|----------|-------|--------------|
| **Create → Link → Report → Export** | Create project → modules → requirements → link configs → links → traceability matrix → ReqIF export | Matrix coverage % > 0; ReqIF validates |
| **Template → Project → Baseline** | Create from RBC_Template → edit → baseline → compare | Baseline diff shows changes |
| **Cross-project Links** | Create two projects → reference → cross-project link → remove requirement → cascade cleanup | Links cleaned in both projects |
| **Test Result Import** | Create test_execution module → import JUnit XML → verify execution records | Records linked to test cases; reports show results |
| **Multi-user (Server)** | Login → create project → module checkout → edit → release | Lock prevents concurrent edits |

---

## 4. Test Case Specification Format

Each test case follows:

```markdown
### TC-<ID>: <Name>
**Requirement:** <Req ID from Tool Requirements Spec>
**Priority:** Mandatory/Recommended
**Level:** Unit/Integration/System
**Preconditions:** <setup>
**Steps:**
1. <action>
2. <action>
**Expected Result:** <observable outcome>
**Traceability:** <Req ID>
```

---

## 5. Key Test Cases (Representative)

### TC-PM-01: Create Project Seeds Built-in Attributes
| Field | Value |
|-------|-------|
| **Requirement** | FR-PM-01 |
| **Level** | Unit |
| **Test** | `test_create_project_seeds_builtin_attributes` |
| **Preconditions** | Clean data directory |
| **Steps** | 1. Call `service.create_project("Project One")` |
| **Expected** | Project created with `object_text` attribute only |
| **Traceability** | FR-PM-01 → TC-PM-01 |

### TC-RM-01: Requirement ID Allocation
| Field | Value |
|-------|-------|
| **Requirement** | FR-RM-06, FR-RM-07 |
| **Level** | Unit |
| **Test** | `test_add_requirement_allocates_prefixed_id`, `test_add_requirement_shares_counter_across_modules_with_same_prefix` |
| **Preconditions** | Project with two modules sharing prefix "REQ" |
| **Steps** | 1. Add requirement to module 1<br>2. Add requirement to module 2 |
| **Expected** | IDs: REQ-0001, REQ-0002 (shared counter) |
| **Traceability** | FR-RM-06, FR-RM-07 → TC-RM-01 |

### TC-AT-01: Attribute Type Validation
| Field | Value |
|-------|-------|
| **Requirement** | FR-AT-01, FR-AT-02 |
| **Level** | Unit |
| **Test** | `test_add_attribute_definition`, `test_add_attribute_definition_required_with_empty_default_rejected` |
| **Preconditions** | Project exists |
| **Steps** | 1. Add enum attribute with choices<br>2. Add required attribute with empty default |
| **Expected** | Enum accepted; required with empty default rejected (400) |
| **Traceability** | FR-AT-01, FR-AT-02 → TC-AT-01 |

### TC-TL-01: Link Direction Enforcement
| Field | Value |
|-------|-------|
| **Requirement** | FR-TL-02 |
| **Level** | Unit |
| **Test** | `test_link_requirements_wrong_direction_raises` |
| **Preconditions** | Project with link config A→B, requirement in B |
| **Steps** | 1. Attempt link from B to A |
| **Expected** | ValueError raised (wrong direction) |
| **Traceability** | FR-TL-02 → TC-TL-01 |

### TC-TL-02: Cross-Project Link Integrity
| Field | Value |
|-------|-------|
| **Requirement** | FR-TL-03 |
| **Level** | Integration |
| **Test** | `test_cross_project_links` |
| **Preconditions** | Two projects with reference |
| **Steps** | 1. Create cross-project link config<br>2. Link requirement from P1 to P2<br>3. Remove requirement in P2 |
| **Expected** | Link cleaned in P1; mirrors updated |
| **Traceability** | FR-TL-03 → TC-TL-02 |

### TC-BH-01: Git Auto-Commit on Mutation
| Field | Value |
|-------|-------|
| **Requirement** | FR-BH-01 |
| **Level** | Integration |
| **Test** | `test_git_history` |
| **Preconditions** | Project with git enabled |
| **Steps** | 1. Add requirement<br>2. Check git log |
| **Expected** | Auto-commit with message "REQ-0001 added" |
| **Traceability** | FR-BH-01 → TC-BH-01 |

### TC-BH-02: Baseline Immutability
| Field | Value |
|-------|-------|
| **Requirement** | FR-BH-03 |
| **Level** | Integration |
| **Test** | `test_baseline_immutability` |
| **Preconditions** | Project with baseline |
| **Steps** | 1. Create baseline<br>2. Modify requirement<br>3. Compare baseline vs current |
| **Expected** | Baseline shows original state; diff shows change |
| **Traceability** | FR-BH-03 → TC-BH-02 |

### TC-RP-01: Traceability Matrix Coverage
| Field | Value |
|-------|-------|
| **Requirement** | FR-RP-01 |
| **Level** | System |
| **Test** | `test_traceability_matrix` |
| **Preconditions** | Chain: Req → Test → Exec (3 link configs) |
| **Steps** | 1. Call traceability report with chain |
| **Expected** | Matrix shows coverage %, uncovered rows, orphans |
| **Traceability** | FR-RP-01 → TC-RP-01 |

### TC-IE-01: ReqIF Export Validation
| Field | Value |
|-------|-------|
| **Requirement** | FR-IE-01 |
| **Level** | System |
| **Test** | `test_export_reqif` + external validation |
| **Preconditions** | Project with requirements, links |
| **Steps** | 1. Export ReqIF<br>2. Parse with `reqif` Python package |
| **Expected** | Valid ReqIF; SPEC-OBJECTS, SPEC-RELATIONS present |
| **Traceability** | FR-IE-01 → TC-IE-01 |

### TC-IE-02: CSV Round-Trip
| Field | Value |
|-------|-------|
| **Requirement** | FR-IE-02 |
| **Level** | System |
| **Test** | `test_csv_import_export_roundtrip` |
| **Preconditions** | Project with requirements |
| **Steps** | 1. Export CSV<br>2. Import CSV to new project |
| **Expected** | Requirements identical (attributes, hierarchy, order) |
| **Traceability** | FR-IE-02 → TC-IE-02 |

### TC-TP-01: RBC Template Compliance
| Field | Value |
|-------|-------|
| **Requirement** | FR-TP-01 |
| **Level** | System |
| **Test** | `test_rbc_template` |
| **Preconditions** | Built-in template loaded |
| **Steps** | 1. Create project from RBC_Template |
| **Expected** | Modules: StRS, SyRS, SwRS, SysTest, SwTest, SysTestExec, SwTestExec, OpScen with correct types and skeletons |
| **Traceability** | FR-TP-01 → TC-TP-01 |

---

## 6. Mutation Testing Specification

### 6.1 Target Modules (SIL4-critical)
| Module | Functions Mutated | Rationale |
|--------|-------------------|-----------|
| `service.py` | All public methods | All business logic |
| `store.py` | `save_*`, `load_*`, `_allocate_requirement_id` | Data integrity |
| `git_repo.py` | `auto_commit`, `requirement_history`, `baseline_*` | History integrity |
| `history.py` | `compare_snapshots`, `describe_changes` | Report correctness |

### 6.2 Mutation Testing Configuration
```ini
[mutmut]
source_paths = reqman/service.py reqman/store.py reqman/git_repo.py reqman/history.py
backup = False
runner = python -m pytest -x -q --ignore=tests/test_server_discovery.py --ignore=tests/test_server_login_account.py --ignore=tests/test_git_remotes.py --ignore=tests/test_login_publish_push.py --ignore=tests/test_git_baselines.py --ignore=tests/test_git_baselines_edge_cases.py --ignore=tests/test_git_remotes_edge_cases.py --ignore=tests/test_git_history_conflicts_and_isolation.py --ignore=tests/test_git_history_safety.py
max_runtime = 300
```

### 6.2 Mutation Testing Acceptance Criteria
| Metric | Target |
|--------|--------|
| **Mutation Score** | ≥ 80% |
| **Surviving Mutants** | 0 (all killed or equivalent) |
| **Runtime** | < 5 min per module |

---

## 7. Test Execution & Reporting

### 7.1 CI Pipeline
```yaml
# .github/workflows/ci.yml
- name: Run pytest with coverage
  run: python -m pytest -q --cov=reqman --cov-fail-under=70 --cov-report=xml

- name: Run mutation testing (service.py)
  run: |
    python -m mutmut run --source-dirs=reqman/service.py --runner="python -m pytest -x -q" --max-runtime=300 || true
```

### 7.2 Test Report Artifacts
| Artifact | Format | Location |
|----------|--------|----------|
| Unit/Integration Test Results | JUnit XML | `backend/test-results.xml` |
| Coverage Report | Cobertura XML | `backend/coverage.xml` |
| Mutation Testing Results | JSON/Text | `backend/mutmut-results.json` |
| Coverage HTML | HTML | `backend/htmlcov/` |

### 7.3 Acceptance Criteria per Test Run
| Metric | Threshold | Action on Failure |
|--------|-----------|-------------------|
| **Test Pass Rate** | 100% | Block merge |
| **Line Coverage** | ≥ 70% (target 90%) | Block merge |
| **Branch Coverage** | ≥ 60% (target 80%) | Block merge |
| **Mutation Score** | ≥ 80% | Review surviving mutants |
| **Static Analysis** | 0 errors | Block merge |

---

## 7. Test Data & Fixtures

### 7.1 Fixtures (conftest.py)
```python
@pytest.fixture
def service(tmp_path):
    return ProjectService(ProjectStore(tmp_path))

def create_project(service, name="Project One"):
    return service.create_project(name)

def create_module(service, project_id, name="Module One", prefix="REQ"):
    return service.create_module(project_id, name, prefix)
```

### 7.2 Test Isolation
- Each test uses isolated `tmp_path`
- `REQMAN_GIT=off` for unit tests (git tests opt-in)
- `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`
- Fixed author: "Test Author" <author@example.com>

---

## 8. Test Completion Criteria

| Criterion | Target | Status |
|-----------|--------|--------|
| All Mandatory test cases pass | 100% | ✅ 379/379 |
| Line coverage | ≥ 70% | ⚠️ TBD |
| Branch coverage | ≥ 60% | ⚠️ TBD |
| Mutation score (service.py) | ≥ 80% | ⚠️ TBD |
| No critical static analysis findings | 0 | ⚠️ TBD |

---

## 9. Test Report Template

```markdown
# Test Report: ReqManTool T2 Qualification

**Version:** <version>
**Date:** <date>
**Test Engineer:** <name>
**Environment:** Python <version>, OS <os>

## Summary
| Metric | Value |
|--------|-------|
| Total Tests | <N> |
| Passed | <N> |
| Failed | <N> |
| Line Coverage | <X>% |
| Branch Coverage | <X>% |
| Mutation Score | <X>% |

## Failed Tests
| Test | Error |
|------|-------|

## Mutation Testing Summary
| Module | Mutants Killed | Surviving | Score |
|--------|---------------|-----------|-------|

## Deviations
| Deviation ID | Description | Justification |
|--------------|-------------|---------------|

## Sign-off
| Role | Name | Signature | Date |
|------|------|-----------|------|
| Test Engineer | | | |
| Safety Engineer | | | |
| Quality Manager | | | |
```

---

*End of Test Specification*