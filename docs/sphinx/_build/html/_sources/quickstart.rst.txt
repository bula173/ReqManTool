Quickstart
==========

Install from Release Package
----------------------------

.. code-block:: bash

   # Install from GitHub release (wheel contains backend + frontend)
   pipx install https://github.com/bula173/ReqManTool/releases/download/v0.1.0/reqmantool-0.1.0-py3-none-any.whl

   # Or with uv
   uv tool install https://github.com/bula173/ReqManTool/releases/download/v0.1.0/reqmantool-0.1.0-py3-none-any.whl

   # Initialize config and data directory
   reqman init

   # Start the server
   reqman serve

Open http://localhost:8899 in your browser.

Quickstart from Source
----------------------

.. code-block:: bash

   git clone https://github.com/bula173/ReqManTool
   cd ReqManTool
   ./etc/start.sh

This starts the backend on http://localhost:8899 and the frontend dev server on http://localhost:5173.

Edition Selection
-----------------

.. code-block:: bash

   # Local edition (default, no login)
   ./etc/start.sh --local

   # Server edition (requires login provider config)
   ./etc/start.sh --server

First Steps
-----------

1. **Create a project** — Click "New project" in the sidebar
2. **Add modules** — Right-click project → "New module" (e.g., StRS, SyRS, SwRS)
3. **Define attributes** — Project page → "Attributes" → Add custom attributes
4. **Create requirements** — In a module, click "New requirement" or use Ctrl+N
5. **Create link configurations** — Project page → "Link configurations" → Define link types
6. **Link requirements** — Drag & drop between modules, or use context menu
7. **Run reports** — Reports menu → Traceability matrix, Verification report, etc.
8. **Create baselines** — Git panel → "Create baseline" (requires git history enabled)
8. **Export** — File → Export → ReqIF / CSV / XLSX / DOCX

Keyboard Shortcuts
------------------

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Shortcut
     - Action
   * - ``Ctrl+N``
     - New requirement
   * - ``Ctrl+Enter``
     - Commit inline edit
   * - ``Escape``
     - Cancel inline edit / close dialog
   * - ``Ctrl+C`` / ``Ctrl+X`` / ``Ctrl+V``
     - Copy / Cut / Paste requirements
   * - ``Alt+Up`` / ``Alt+Down``
     - Reorder requirement
   * - ``Ctrl+B``
     - Toggle sidebar
   * - ``Ctrl+Alt+B``
     - Toggle detail panel
   * - ``?``
     - Show shortcuts help

Next Steps
----------

- :doc:`installation` — Detailed installation options
- :doc:`configuration` — Configuration file and environment variables
- :doc:`projects` — Project and module management