# ReqManTool — Test Report (T2 Qualification)

**Version:** <VERSION>  
**Date:** <DATE>  
**Test Engineer:** <NAME>  
**Environment:** Python <PYTHON_VERSION>, OS <OS>  
**Tool Version:** <TOOL_VERSION>  
**Git Commit:** <COMMIT_HASH>  

---

## 1. Summary

| Metric | Value | Threshold | Status |
|--------|-------|-----------|--------|
| Total Tests Executed | <N> | — | — |
| Tests Passed | <N> | 100% | ☐ Pass / ☐ Fail |
| Tests Failed | <N> | 0 | ☐ Pass / ☐ Fail |
| Line Coverage | <X>% | ≥ 70% | ☐ Pass / ☐ Fail |
| Branch Coverage | <X>% | ≥ 60% | ☐ Pass / ☐ Fail |
| Mutation Score (service.py) | <X>% | ≥ 80% | ☐ Pass / ☐ Fail |
| Static Analysis (mypy/ruff) | <N> errors | 0 | ☐ Pass / ☐ Fail |

**Overall Verdict:** ☐ **PASS** / ☐ **FAIL**

---

## 2. Test Execution Details

### 2.1 Test Execution Environment
| Parameter | Value |
|-----------|-------|
| Python Version | <VERSION> |
| OS | <OS> |
| pytest Version | <VERSION> |
| Git Commit | <HASH> |
| Test Duration | <TIME> |

### 2.2 Test Suite Results
| Test Suite | Tests | Passed | Failed | Duration |
|------------|-------|--------|--------|----------|
| test_service.py | <N> | <N> | <N> | <TIME> |
| test_api.py | <N> | <N> | <N> | <TIME> |
| test_templates.py | <N> | <N> | <N> | <TIME> |
| test_reports.py | <N> | <N> | <N> | <TIME> |
| test_rich_text.py | <N> | <N> | <N> | <TIME> |
| test_git_history.py | <N> | <N> | <N> | <TIME> |
| test_git_history_edge_cases.py | <N> | <N> | <N> | <TIME> |
| Other | <N> | <N> | <N> | <TIME> |
| **Total** | **<N>** | **<N>** | **0** | **<TIME>** |

---

## 3. Coverage Report

### 3.1 Line Coverage by Module
| Module | Statements | Missing | Coverage |
|--------|------------|---------|----------|
| reqman/service.py | <N> | <N> | <X>% |
| reqman/store.py | <N> | <N> | <X>% |
| reqman/git_repo.py | <N> | <N> | <X>% |
| reqman/history.py | <N> | <N> | <X>% |
| reqman/models.py | <N> | <N> | <X>% |
| reqman/richtext.py | <N> | <N> | <X>% |
| reqman/templates.py | <N> | <N> | <X>% |
| reqman/csv_import.py | <N> | <N> | <X>% |
| reqman/test_results_import.py | <N> | <N> | <X>% |
| reqman/*_export.py | <N> | <N> | <X>% |
| **Overall** | **<N>** | **<N>** | **<X>%** |

### 3.2 Branch Coverage by Module
| Module | Branches | Missing | Coverage |
|--------|----------|---------|----------|
| reqman/service.py | <N> | <N> | <X>% |
| reqman/store.py | <N> | <N> | <X>% |
| reqman/git_repo.py | <N> | <N> | <X>% |
| **Overall** | **<N>** | **<N>** | **<X>%** |

---

## 4. Mutation Testing Results

### 4.1 Summary by Module
| Module | Mutants Killed | Surviving | Timeout | Error | Score |
|--------|----------------|-----------|---------|-------|-------|
| reqman/service.py | <N> | <N> | <N> | <N> | <X>% |
| reqman/store.py | <N> | <N> | <N> | <N> | <X>% |
| reqman/git_repo.py | <N> | <N> | <N> | <N> | <X>% |
| reqman/history.py | <N> | <N> | <N> | <N> | <X>% |
| **Total** | **<N>** | **<N>** | **<N>** | **<N>** | **<X>%** |

### 4.2 Surviving Mutants Analysis
| Mutant ID | Module | Location | Mutation | Reason for Survival |
|-----------|--------|----------|---------|---------------------|
| <ID> | <MODULE> | <FILE:LINE> | <DESCRIPTION> | <REASON> |

---

## 5. Static Analysis Results

### 5.1 mypy
| Severity | Count | Details |
|----------|-------|---------|
| Error | <N> | <DETAILS> |
| Warning | <N> | <DETAILS> |

### 5.2 ruff
| Rule | Count | Details |
|------|-------|---------|
| <RULE> | <N> | <DETAILS> |

### 5.3 pip-audit
| Package | Vulnerability | Severity | Fixed Version |
|---------|---------------|----------|---------------|

---

## 8. Deviations & Waivers

| Deviation ID | Requirement | Deviation Description | Justification | Status |
|--------------|-------------|----------------------|---------------|--------|
| DEV-001 | Single writer per data directory | No concurrent editing | Local edition design; server edition has locks | Accepted |
| DEV-002 | No ReqIF import | One-way exchange | Export verified by independent tool | Accepted |
| DEV-003 | Local edition: no authentication | No built-in auth | Server edition required for multi-user | Accepted |
| DEV-004 | <ID> | <DESCRIPTION> | <JUSTIFICATION> | <STATUS> |

---

## 9. Open Issues & Action Items

| ID | Description | Priority | Owner | Due Date |
|----|-------------|----------|-------|----------|
| ACT-001 | Achieve 90% line coverage | High | <OWNER> | <DATE> |
| ACT-002 | Achieve 80% branch coverage | High | <OWNER> | <DATE> |
| ACT-003 | Achieve 80% mutation score | High | <OWNER> | <DATE> |
| ACT-004 | Fix mypy errors (enable strict) | Medium | <OWNER> | <DATE> |
| ACT-005 | Fix ruff warnings | Medium | <OWNER> | <DATE> |
| ACT-006 | Complete Tool Requirements Spec | Critical | <OWNER> | <DATE> |
| ACT-007 | Complete Tool V&V Plan | Critical | <OWNER> | <DATE> |
| ACT-008 | Assemble Tool Qualification Kit | Critical | <OWNER> | <DATE> |

---

## 10. Sign-off

| Role | Name | Signature | Date |
|------|------|-----------|------|
| Test Engineer | | | |
| Safety Engineer | | | |
| Quality Manager | | | |
| Project Manager | | | |

---

## Appendix A: Test Execution Log

```
<PASTE FULL pytest OUTPUT HERE>
```

---

## Appendix B: Mutation Testing Details

```
<PASTE mutmut results --print-all OUTPUT HERE>
```

---

## Appendix C: Coverage Details

```
<PASTE coverage xml or terminal output HERE>
```

---

*End of Test Report*