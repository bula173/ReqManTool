Configuration
=============

Configuration File
------------------

The configuration file is JSON with schema validation. Location:

.. code-block:: text

   # Linux
   ~/.config/reqman/config.json
   # macOS
   ~/Library/Application Support/ReqManTool/config.json
   # Windows
   %APPDATA%\ReqManTool\config.json

.. code-block:: json

   {
     "$schema": "https://opencode.ai/config.json",
     "data_dir": "/var/lib/reqman/data",
     "host": "0.0.0.0",
     "port": 8899,
     "cors_origins": ["https://reqman.example.com"],
     "source_url": "https://github.com/bula173/ReqManTool",
     "tls_cert_file": "/etc/ssl/certs/reqman.pem",
     "tls_key_file": "/etc/ssl/private/reqman.key",
     "tls_ca_file": "/etc/ssl/certs/ca.pem",
     "mode": "server"
   }

Settings Reference
------------------

.. list-table::
   :header-rows: 1
   :widths: 20 15 15 50

   * - Key
     - Env Var
     - Default
     - Description
   * - ``data_dir``
     - ``REQMAN_DATA_DIR``
     - Platform default
     - Data directory (git repositories, templates)
   * - ``host``
     - ``REQMAN_HOST``
     - ``localhost``
     - Bind address
   * - ``port``
     - ``REQMAN_PORT``
     - ``8899``
     - TCP port
   * - ``cors_origins``
     - ``REQMAN_CORS_ORIGINS``
     - ``["http://localhost:5173"]``
     - Allowed browser origins (comma-separated)
   * - ``source_url``
     - ``REQMAN_SOURCE_URL``
     - GitHub repo
     - Source code link (AGPL §13)
   * - ``tls_cert_file``
     - ``REQMAN_TLS_CERT_FILE``
     - ``null``
     - TLS certificate (PEM) for HTTPS
   * - ``tls_key_file``
     - ``REQMAN_TLS_KEY_FILE``
     - ``null``
     - TLS private key (PEM)
   * - ``tls_ca_file``
     - ``REQMAN_TLS_CA_FILE``
     - ``null``
     - CA cert for mTLS client verification
   * - ``mode``
     - ``REQMAN_MODE``
     - ``local``
     - ``local`` or ``server``

Server Edition Settings (``server`` section)
---------------------------------------------

.. code-block:: json

   {
     "server": {
       "auth_providers": [
         {
           "id": "gitlab",
           "type": "gitlab",
           "name": "GitLab",
           "url": "https://gitlab.example.com",
           "client_id": "xxx",
           "client_secret_env": "REQMAN_AUTH_GITLAB_CLIENT_SECRET",
           "scopes": ["openid", "profile", "email", "read_api"],
           "git_hosts": ["ssh.gitlab.example.com"]
         }
       ],
       "base_url": "https://reqman.example.com",
       "frontend_url": "https://reqman.example.com",
       "cookie_secure": true,
       "session_max_age_hours": 12,
       "session_idle_minutes": 120,
       "persist_sessions": true,
       "state_dir": "/var/lib/reqman/state",
       "admins": ["admin@example.com"],
       "admin_groups": ["reqman-admins"],
       "group_roles": [
         {"group": "engineers", "role": "editor", "projects": ["*"]}
       ],
       "project_creation": "everyone",
       "module_lock_idle_minutes": 60,
       "require_module_checkout": true,
       "repo_roles": {"enabled": true, "cache_minutes": 5, "grace_minutes": 60},
       "discovery": {"enabled": true, "cache_minutes": 15, "max_repos": 200}
     }
   }

.. list-table::
   :header-rows: 1
   :widths: 20 15 15 50

   * - Key
     - Env Var
     - Default
     - Description
   * - ``auth_providers``
     - —
     - **required**
     - List of OIDC/GitHub/Gerrit/SAML/LDAP/proxy providers
   * - ``base_url``
     - ``REQMAN_SERVER_BASE_URL``
     - Request origin
     - Public backend URL (OAuth redirect_uri)
   * - ``frontend_url``
     - ``REQMAN_SERVER_FRONTEND_URL``
     - ``base_url``
     - Frontend URL (post-login redirect)
   * - ``cookie_secure``
     - ``REQMAN_SERVER_COOKIE_SECURE``
     - ``true``
     - Secure cookie (false for http://localhost)
   * - ``session_max_age_hours``
     - —
     - 12
     - Absolute session lifetime
   * - ``session_idle_minutes``
     - —
     - 120
     - Idle timeout
   * - ``persist_sessions``
     - —
     - ``true``
     - Survive server restart
   * - ``state_dir``
     - ``REQMAN_SERVER_STATE_DIR``
     - Config dir
     - Sessions, audit, memberships, locks
   * - ``admins``
     - ``REQMAN_SERVER_ADMINS``
     - ``[]``
     - Server admin emails/usernames
   * - ``admin_groups``
     - —
     - ``[]``
     - IdP groups → server admin
   * - ``group_roles``
     - —
     - ``[]``
     - IdP group → role per project
   * - ``project_creation``
     - —
     - ``everyone``
     - ``everyone`` or ``admins``
   * - ``module_lock_idle_minutes``
     - —
     - 60
     - Auto-release lock after idle
   * - ``require_module_checkout``
     - —
     - ``true``
     - Require checkout before edit (``false`` = checkout optional)
   * - ``repo_roles``
     - —
     - ``true``
     - GitLab/GitHub repo permissions → roles
   * - ``discovery``
     - —
     - ``true``
     - Auto-discover projects from repos

Provider Configuration
-----------------------

Each provider in ``auth_providers``:

.. list-table::
   :header-rows: 1

   * - Field
     - Required
     - Description
   * - ``id``
     - Yes
     - Unique identifier (lowercase, alphanumeric, hyphen)
   * - ``type``
     - Yes
     - ``gitlab``, ``github``, ``oidc``, ``ldap``, ``saml``, ``proxy_header``, ``gerrit``
   * - ``name``
     - No
     - Display name (defaults to type)
   * - ``url`` / ``issuer``
     - Yes
     - GitLab/GitHub URL or OIDC issuer
   * - ``client_id``
     - Yes
     - OAuth client ID (or ``REQMAN_AUTH_<ID>_CLIENT_ID`` env)
   * - ``client_secret`` / ``client_secret_env``
     - Yes
     - Secret (or env var name)
   * - ``scopes``
     - No
     - OAuth scopes (default: openid profile email)
   * - ``allow_http``
     - No
     - Allow http:// for local dev
   * - ``git_hosts``
     - No
     - Additional git SSH hosts for repo roles

Secrets Management
-------------------

Never put secrets in config file. Use environment variables:

.. code-block:: bash

   # Server edition
   export REQMAN_AUTH_GITLAB_CLIENT_SECRET="xxx"
   export REQMAN_AUTH_GITHUB_CLIENT_SECRET="xxx"

   # Or in config file
   "client_secret_env": "REQMAN_AUTH_GITLAB_CLIENT_SECRET"

The secret is never logged, returned by API, or sent to browser.

CLI Overrides
-------------

.. code-block:: bash

   reqman serve --host 0.0.0.0 --port 8443 --tls-cert-file /etc/ssl/certs/server.pem --tls-key-file /etc/ssl/private/server.key

CLI flags > environment variables > config file > defaults.

Config Command
--------------

.. code-block:: bash

   reqman config          # Print effective settings with sources
   reqman config --data-dir /new/path   # Update data directory