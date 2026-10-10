Architecture
============

System Overview
---------------

ReqManTool follows a clean layered architecture with clear separation of concerns.

.. code-block:: text

   ┌─────────────────┐     REST + WebSocket      ┌────────────────────┐
   │   Frontend      │ ◀──────────────────────▶ │   Backend          │
   │   (React/Vite)  │   JSON-RPC 2.0 + REST    │   (FastAPI)        │
   └─────────────────┘                            └────────┬───────────┘
                                                          │
                                                 ┌────────▼────────┐
                                                 │ Data Directory  │
                                                 │ (JSON + Git)    │
                                                 └─────────────────┘

Layer Responsibilities
----------------------

.. list-table::
   :header-rows: 1
   :widths: 20 30 50

   * - Layer
     - Module
     - Responsibility
   * - **Data Model**
     | ``models.py`` | Dataclasses with ``to_dict``/``from_dict``; stable field order for git
   * - **Persistence**
     | ``store.py`` | Atomic JSON I/O (temp file + os.replace + fsync); format versioning
   * - **Business Logic**
     | ``service.py`` | Single source of truth; id allocation, validation, cascades, git hooks
   * - **Transport**
     | ``app.py`` | Thin FastAPI adapters; REST + WebSocket JSON-RPC parity
   * - **Git History**
     | ``git_repo.py`` | Auto-commit, baselines, history, remotes
   * - **Export/Import** | ``*_export.py``, ``csv_import.py``, ``reqif_import.py`` | Duck-typed; ReqIF, CSV, XLSX, DOCX

Key Design Decisions
--------------------

Dual Transport Parity
~~~~~~~~~~~~~~~~~~~~~

Both REST and WebSocket JSON-RPC call the exact same ``ProjectService`` methods.
Identical error mapping ensures identical behavior.

Atomic Persistence
~~~~~~~~~~~~~~~~~~

Every write uses a temporary file + ``fsync``/``F_FULLFSYNC`` + atomic ``os.replace``.
Multi-file operations use a staging directory (``.reqman-staging/<random>/``) and
a single ``os.rename``.

Git-Backed History
~~~~~~~~~~~~~~~~~~

Each project is a git repository. Every mutation auto-commits with squashing
(10-min window). Baselines are annotated tags + fast-forward ``baseline`` branch.

Deterministic Outputs
~~~~~~~~~~~~~~~~~~~~~

Reports, exports, and ID allocation are fully deterministic. Same input → same
output, enabling independent verification (T2 requirement).

Data Model Overview
-------------------

.. mermaid::

   classDiagram
     Project "1" -- "*" Module
     Module "1" -- "*" Requirement
     Requirement "*" -- "*" Requirement : parent/child
     Project "1" -- "*" AttributeDefinition
     Project "1" -- "*" LinkConfiguration
     LinkConfiguration "1" -- "*" LinkPair
     Project "1" -- "*" View
     Project "1" -- "*" RowStyle
     Project "1" -- "0..1" ProjectLock
     Project "0..1" -- "1" Project : parent

Key Files
---------

.. list-table::
   :header-rows: 1

   * - File
     - Purpose
   * - ``models.py`` | Dataclasses: Project, Module, Requirement, AttributeDefinition, LinkConfiguration, LinkPair, View, RowStyle, ProjectLock
   * - ``store.py`` | ``ProjectStore`` — atomic JSON I/O, format versioning, staging
   * - ``service.py`` | ``ProjectService`` — all business logic, validation, cascades
   * - ``app.py`` | FastAPI app, REST routes, WebSocket JSON-RPC, middleware
   * - ``git_repo.py`` | ``GitHistory`` — auto-commit, baselines, history, remotes
   * - ``history.py`` | Commit messages, snapshots, blame, comparison
   * - ``templates.py`` | Project/module templates, validation, import/export
   * - ``*_export.py`` | ReqIF, CSV, XLSX, DOCX export (duck-typed)
   * - ``csv_import.py`` | CSV import with link support
   * - ``reqif_import.py`` | ReqIF import; XHTML values become rich text again
   * - ``test_results_import.py`` | JUnit/Robot Framework import

Extension Points
----------------

- **Server edition** — Loaded via ``extensions.py`` when ``mode=server``
- **Custom auth** — Implement ``IdentityProvider`` protocol
- **Custom permissions** — Implement ``PermissionGuard`` protocol
- **Custom exports** — Add to ``app.py`` REST routes