Installation
============

Release Package (Recommended)
-----------------------------

Each GitHub release includes a Python wheel containing both the backend and the
built frontend. This is the simplest way to install ReqManTool.

.. code-block:: bash

   # With pipx (recommended - isolated environment)
   pipx install https://github.com/bula173/ReqManTool/releases/download/v0.1.0/reqmantool-0.1.0-py3-none-any.whl

   # Or with uv (faster)
   uv tool install https://github.com/bula173/ReqManTool/releases/download/v0.1.0/reqmantool-0.1.0-py3-none-any.whl

   # Initialize
   reqman init

   # Start
   reqman serve

The wheel contains:
- Backend Python package (FastAPI + uvicorn)
- Built frontend (React + Vite) served from the same origin
- All dependencies bundled

From Source (Development)
-------------------------

Prerequisites
~~~~~~~~~~~~~

- Python 3.11+
- Node.js 24+
- git 2.30+

.. code-block:: bash

   git clone https://github.com/bula173/ReqManTool
   cd ReqManTool
   ./etc/start.sh

This script:
1. Creates a Python venv in ``backend/.venv``
2. Installs Python dependencies from ``backend/requirements.txt``
3. Installs Node dependencies in ``frontend/``
4. Starts backend on ``http://localhost:8899``
4. Starts Vite dev server on ``http://localhost:5173``

Edition Selection
~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # Local edition (default, no login)
   ./etc/start.sh --local

   # Server edition (requires login provider config)
   ./etc/start.sh --server

   # Or via environment variable
   REQMAN_MODE=server ./etc/start.sh

Docker (Experimental)
---------------------

.. code-block:: bash

   docker build -t reqmantool .
   docker run -p 8899:8899 -v /path/to/data:/data reqmantool

See ``Dockerfile`` for build details. The image runs as non-root user.

System Requirements
-------------------

   * - Component
     - Minimum Version
     - Notes
   * - Python
     - 3.11+
     - Tested on 3.11, 3.14
| Python | 3.11+ | Tested on 3.11, 3.14 |
   * - Node.js
     - 24+
     - Frontend build only
   * - git
     - 2.30+
     - Required for history/baselines
   * - OS
     - Linux/macOS/Windows
     - Tested on Ubuntu, macOS
   * - Browser
     - Modern (ES2022)
     - Chrome, Firefox, Safari, Edge

Python Dependencies
-------------------

Core runtime (``backend/requirements.txt``):

- ``fastapi>=0.115`` — Web framework
- ``uvicorn>=0.30`` — ASGI server
- ``reqif>=0.0.60`` — ReqIF export
- ``openpyxl>=3.1`` — XLSX export
- ``python-docx>=1.1`` — DOCX export
- ``keyring>=25.0`` — OS keychain for tokens

Development dependencies (``backend/requirements-dev.txt``):

- ``pytest>=8.0`` — Testing
- ``httpx>=0.27`` — API testing
- ``pytest-cov>=4.1`` — Coverage
- ``mypy>=1.10`` — Static typing
- ``ruff>=0.6`` — Linting
- ``mutmut>=2.4`` — Mutation testing

Node.js Dependencies
--------------------

Frontend (``frontend/package.json``):

- ``react>=19`` — UI framework
- ``@mantine/core>=9.6`` — Component library
- ``@tanstack/react-query>=5`` — Server state
- ``@tiptap/*>=3.31`` — Rich text editor
- ``oxlint`` — Linting
- ``typescript`` — Type checking
- ``vite`` — Build tool

Configuration File
------------------

``reqman init`` creates a config file at:

| Platform | Location |
|----------|----------|
| Linux | ``~/.config/reqman/config.json`` |
| macOS | ``~/Library/Application Support/ReqManTool/config.json`` |
| Windows | ``%APPDATA%\ReqManTool\config.json`` |

Example:

.. code-block:: json

   {
     "$schema": "https://opencode.ai/config.json",
     "data_dir": "/var/lib/reqman/data",
     "host": "0.0.0.0",
     "port": 8443,
     "cors_origins": ["https://reqman.example.com"],
     "tls_cert_file": "/etc/ssl/certs/reqman.pem",
     "tls_key_file": "/etc/ssl/private/reqman.key",
     "tls_ca_file": "/etc/ssl/certs/ca.pem",
     "mode": "server",
     "server": {
       "auth_providers": [...]
     }
   }

Environment Variables
---------------------

All settings can be overridden via environment variables (prefix ``REQMAN_``):

.. list-table::
   :header-rows: 1

   * - Setting
     - Env Var
     - Default
   * - Data directory
     - ``REQMAN_DATA_DIR``
     - Platform default
   * - Host
     - ``REQMAN_HOST``
     - ``localhost``
   * - Port
     - ``REQMAN_PORT``
     - 8899
   * - CORS origins
     - ``REQMAN_CORS_ORIGINS``
     - ``http://localhost:5173``
   * - TLS cert file
     - ``REQMAN_TLS_CERT_FILE``
     - (none)
   * - TLS key file
     - ``REQMAN_TLS_KEY_FILE``
     - (none)
   * - TLS CA file
     - ``REQMAN_TLS_CA_FILE``
     - (none)
   * - Mode (local/server)
     - ``REQMAN_MODE``
     - ``local``

Upgrading
---------

.. code-block:: bash

   # From release package
   pipx upgrade reqmantool
   # or
   uv tool upgrade reqmantool

   # From source
   git pull
   ./etc/start.sh

The data directory is preserved across upgrades. Run ``reqman config`` to verify settings.

Troubleshooting
---------------

Port Already in Use
~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # Change port
   reqman serve --port 8443
   # or
   REQMAN_PORT=8443 reqman serve

TLS Certificate Issues
~~~~~~~~~~~~~~~~~~~~~~

For development, generate a self-signed certificate:

.. code-block:: bash

   openssl req -x509 -newkey rsa:2048 -keyout server.key -out server.pem \
     -days 365 -nodes -subj "/CN=localhost" \
     -addext "subjectAltName=DNS:localhost"

   reqman serve --tls-cert-file server.pem --tls-key-file server.key

Git Not Found
~~~~~~~~~~~~~

.. code-block:: bash

   # Install git
   sudo apt install git  # Ubuntu/Debian
   brew install git      # macOS

   # Or set custom path
   REQMAN_GIT_EXECUTABLE=/usr/local/bin/git reqman serve

Data Directory Permissions
~~~~~~~~~~~~~~~~~~~~~~~~~~

Ensure the user running ReqManTool owns the data directory:

.. code-block:: bash

   sudo chown -R $USER:$USER /var/lib/reqman/data