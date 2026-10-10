Contributing
============

Development Setup
-----------------

.. code-block:: bash

   git clone https://github.com/bula173/ReqManTool
   cd ReqManTool
   ./etc/start.sh

This creates a Python venv (``backend/.venv``), installs dependencies, and starts both
backend and frontend dev servers.

Code Style
----------

Backend (Python)
~~~~~~~~~~~~~~~~

.. code-block:: bash

   # Format
   ruff check --fix reqman/
   ruff format reqman/

   # Type check
   mypy --ignore-missing-imports reqman/

   # Test
   pytest -q

Frontend (TypeScript/React)
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   cd frontend
   npm run lint
   npm run build

Commit Messages
---------------

Follow conventional commits:

.. code-block:: text

   <type>(<scope>): <subject>

   <body>

   <footer>

Types: ``feat``, ``fix``, ``docs``, ``style``, ``refactor``, ``test``, ``chore``

Example:

.. code-block:: text

   feat(export): add ReqIF export for operational scenarios

   Adds support for exporting operational_scenarios modules to ReqIF format
   including SPEC-RELATIONS for cross-module links.

   Closes #123

Testing
-------

Run the full test suite:

.. code-block:: bash

   cd backend
   pytest -q

Run with coverage:

.. code-block:: bash

   pytest -q --cov=reqman --cov-report=term-missing

Mutation Testing
~~~~~~~~~~~~~~~~

.. code-block:: bash

   mutmut run --source-dirs=reqman/service.py --runner="python -m pytest -x -q" --max-runtime=300

Static Analysis
~~~~~~~~~~~~~~~

.. code-block:: bash

   mypy --ignore-missing-imports reqman/
   ruff check reqman/

Pull Request Process
--------------------

1. Fork the repository
2. Create a feature branch (``feature/your-feature``)
3. Make changes with tests
4. Run full test suite (``pytest -q``)
5. Run static analysis (``mypy``, ``ruff``)
6. Ensure all tests pass
6. Open a Pull Request with:
   - Clear description of changes
   - Link to related issue
   - Screenshots for UI changes

Code Review Guidelines
----------------------

- All code must pass CI (tests, linting, type checking)
- New features require tests
- Bug fixes require regression tests
- Documentation updated for user-facing changes
- Breaking changes require migration guide

Release Process
---------------

1. Update version in ``backend/reqman/__init__.py``
2. Update ``CHANGELOG.md``
3. Create and push tag: ``git tag vX.Y.Z && git push origin vX.Y.Z``
3. GitHub Actions builds wheel, creates release, publishes to PyPI
4. Frontend built and embedded in wheel

Code of Conduct
---------------

This project follows the `Contributor Covenant <https://www.contributor-covenant.org/>`_.

License
-------

Dual-licensed: AGPL-3.0-only (community) or commercial license.
See ``LICENSE`` and ``LICENSING.md``.