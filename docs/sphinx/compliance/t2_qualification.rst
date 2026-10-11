T2 Tool Qualification (EN 50128)
================================

Overview
--------

ReqManTool is qualified as a **T2 tool** per EN 50128 §6.7.2 for use in safety-related
development up to SIL4.

.. list-table::
   :header-rows: 1
   :widths: 20 40 40

   * - Classification
     - Criterion
     - Evidence
   * - **T1**
     - Tool output not verified; failure could silently corrupt safety argument
     | **NO** — All exports (ReqIF, CSV, DOCX, reports) are human-reviewed; traceability matrices and verification reports are cross-checked against source requirements
   * - **T2**
     - Tool output independently verified; tool failure detectable by verification
     | **YES** — Traceability matrices show coverage gaps; verification reports flag unverified requirements; impact analysis is deterministic and reproducible
   * - **T3**
     - Tool used for code generation / auto-test
     | **NO** — No code generation; test result import creates execution records but requires human matching

Tool Qualification Kit
----------------------

The following documents constitute the Tool Qualification Kit per EN 50128 §6.7:

.. list-table::
   :header-rows: 1
   :widths: 30 30 40

   * - Document
     - Status
     - Location
   * - Tool Requirements Specification
     | ✅ Complete | :doc:`../../quality/requirements/TOOL_REQUIREMENTS`
   * - Tool V&V Plan
     | ⏳ In Progress | :doc:`../../quality/test_specs/TEST_SPECIFICATION`
   * - Tool V&V Report
     | ⏳ Template Ready | :doc:`../../quality/test_reports/TEST_REPORT_TEMPLATE`
   * - Tool Operating Constraints
     | ✅ Complete | :doc:`../../quality/requirements/TOOL_REQUIREMENTS#tool-operating-constraints`
   * - Tool Configuration Management Plan
     | ⏳ In Progress | ``docs/compliance/CM_PLAN.md``
   * - Deviation Log
     | ✅ Complete | :doc:`../../quality/requirements/TOOL_REQUIREMENTS#known-limitations`
   * - Tool Qualification Report
     | ⏳ Template | ``docs/compliance/QUALIFICATION_REPORT.md``

Tool Classification Justification
---------------------------------

**T2 Classification** is appropriate because:

1. **All tool outputs are independently verified** — Users must review traceability matrices, verification reports, and exported documents before including them in the safety case.

2. **Tool failures are detectable** — Traceability matrices highlight coverage gaps; verification reports flag unverified requirements; impact analysis results are deterministic and can be independently recalculated.

3. **No automatic code generation** — ReqManTool does not generate source code; it manages requirements, traceability, and documentation.

4. **Data integrity guarantees** — Atomic writes, git-backed history, deterministic exports, and format versioning ensure data consistency.

Qualification Scope
-------------------

| Aspect | Coverage |
|--------|----------|
| **Supported SIL** | Up to SIL4 (tool supports SIL4 projects; tool itself not SIL-rated) |
| **Lifecycle Phases** | Requirements specification (§6.2), Architecture (§6.3), Verification (§6.5), Configuration Management (§6.6), Change Management (§6.6) |
| **Tool Functions** | Requirements capture, traceability, verification reporting, baseline management, impact analysis, test result import |

Operating Constraints
---------------------

| Constraint | Mitigation |
|------------|------------|
| Single active writer per data directory | Documented; server edition adds module edit locks |
| Local edition: no authentication | Server edition required for multi-user |
| No full-text search | Not safety-critical |
| Rich text: images not in ReqIF | Documented; export omits images |
| Data directory on local filesystem | Not NFS/SMB (git atomicity) |

Known Limitations (Deviations)
------------------------------

.. list-table::
   :header-rows: 1

   * - Deviation ID
     - Requirement
     - Deviation
     - Justification
   * - DEV-001
     - Concurrent editing
     - Single writer (last-write-wins)
     | Local edition design; server edition has module edit locks
   * - DEV-002
     - ReqIF exchange
     - Import reads a subset of ReqIF
     | A link configuration with no links leaves no trace in the file, and a
       tab in a value cannot round trip (XML attribute-value normalization);
       both are documented in the tool and in the import dialog
   * - DEV-003
     - Local edition: no authentication
     - No built-in auth
     | Server edition required for multi-user

Traceability to EN 50128
------------------------

.. list-table::
   :header-rows: 1

   * - EN 50128 Clause
     - Tool Function
     - Verification Method
   * - §6.2.2 Requirements specification
     - Requirement create/edit, attributes, templates
     - Manual review of exported ReqIF
   * - §6.2.4 Traceability
     - Link configurations, traceability matrix
     - Matrix coverage % + unverified items
   * - §6.5 Verification
     - Verification/execution reports
     - Independent review
   * - §6.6 Configuration management
     - Baselines, git history, impact analysis
     - `git verify-tag`; compare baseline vs current
   * - §6.6 Change management
     - Impact analysis, git history
     - Independent rerun

Qualification Evidence
----------------------

.. list-table::
   :header-rows: 1

   * - Evidence
     - Status
     - Location
   * - Tool Requirements Specification
     | ✅ | `quality/requirements/TOOL_REQUIREMENTS.md`
   * - Tool V&V Plan
     | ⏳ | `quality/test_specs/TEST_SPECIFICATION.md`
   * - Test Report Template
     | ✅ | `quality/test_reports/TEST_REPORT_TEMPLATE.md`
   * - Unit/Integration Tests (379 tests)
     | ✅ | `backend/tests/`
   * - Mutation Testing (service.py)
     | ✅ | `mutmut` in CI
   * - Static Analysis (mypy, ruff)
     | ⚠️ Warnings | CI pipeline
   * - Coverage (70% threshold)
     | ⚠️ In Progress | CI pipeline
   * - Deviation Log
     | ✅ | Requirements Spec §5.3

Qualification Process
---------------------

1. **Prepare Tool Qualification Kit** — Assemble all documents above
2. **Independent Review** — Qualified assessor reviews kit
3. **Address Findings** — Remediate assessor findings
4. **Qualification Sign-off** — Formal acceptance for target SIL

Maintenance
-----------

- Re-qualification required on major version changes
- Regression test suite run on every commit (CI)
- Mutation testing run nightly
- Dependency scanning on every PR