# Server edition: administrator setup

This guide is for the person who installs and runs the ReqManTool server
edition for a team. In the server edition every user must log in. Users
log in with an account they already have (GitHub, GitLab or a company
single sign-on). ReqManTool never stores passwords.

The local edition (`./etc/start.sh --local`, the desktop app) needs none of
this.

## 1. What you configure

Three places are involved:

| Where | What |
|---|---|
| GitHub / GitLab / your SSO | An **OAuth application** that lets ReqManTool sign users in. |
| `config.json` of the server | `mode`, the public address, admins, and the login providers with their **client ID**. |
| An environment file or the server's secret store | The provider's **client secret**. Never put it in `config.json` or in git. |

The config file is `.reqman/config.json` in a git checkout. For an
installed server, it is the file passed with `--config` or set in
`REQMAN_CONFIG_FILE`.

The **callback URL** you register with each provider is always:

```
<base_url>/auth/callback/<provider id>
```

`base_url` is the address users open in the browser. For example,
`https://reqman.example.com` gives `https://reqman.example.com/auth/callback/github`.

## 2. GitHub

1. Go to GitHub → **Settings → Developer settings → OAuth Apps → New OAuth App**
   (https://github.com/settings/applications/new). For a company, create it
   under the **organisation** instead (Organisation → Settings → Developer
   settings), so it does not depend on one person's account.
2. Fill in:
   - **Application name:** `ReqManTool`
   - **Homepage URL:** your `base_url`
   - **Authorization callback URL:** `<base_url>/auth/callback/github`
3. Click **Register application** and copy the **Client ID**.
4. Click **Generate a new client secret** and store the secret as
   `REQMAN_AUTH_GITHUB_CLIENT_SECRET`.

The scopes are `read:user user:email`, the default. ReqManTool requests
them; you set nothing on GitHub.

**Private repositories.** A GitHub OAuth App can read a user's *private*
repositories (their permission on them, project discovery, cloning) only
with the broad `repo` scope, which also grants write access to them. Add it
to the provider's `scopes` if your projects live in private repositories:

```json
{"id": "github", "type": "github", "url": "https://github.com", "client_id": "...",
 "scopes": ["read:user", "user:email", "repo"]}
```

Users are asked to consent again at their next login. ReqManTool only reads
and clones with a user's login token; it never pushes with it. A GitHub App
(fine-grained, installed per repository) is planned as the narrower
alternative; see `docs/BACKLOG.md`.

## 3. GitLab (gitlab.com or self-hosted)

1. Create the application:
   - **Self-hosted, instance-wide** (recommended): Admin Area → **Applications** → New application.
   - **gitlab.com or per group:** Group → Settings → Applications.
2. Fill in:
   - **Name:** `ReqManTool`
   - **Redirect URI:** `<base_url>/auth/callback/gitlab`
   - **Confidential:** yes
   - **Scopes:** `openid`, `profile`, `email` and **`read_api`**.
     `read_api` lets ReqManTool read each user's permission on a project's
     repository and turn it into their role, find the ReqManTool projects in
     their repositories (project discovery) and clone them.
3. Save, then copy the **Application ID** (this is the client ID) and the
   **Secret**. Store the secret as `REQMAN_AUTH_GITLAB_CLIENT_SECRET`.

## 4. Other single sign-on (OIDC)

Keycloak, Microsoft Entra ID, Okta, Authentik and Google Workspace all work
as `"type": "oidc"`:

- Register a web application with the callback URL `<base_url>/auth/callback/<id>`.
- Give it the scopes `openid profile email`, and a `groups` claim if you want group-based roles.
- Set `issuer` to the provider's issuer URL. ReqManTool reads
  `<issuer>/.well-known/openid-configuration` itself.

LDAP / Active Directory and SAML are planned. See `docs/BACKLOG.md`,
"more identity providers".

## 5. `config.json`

```json
{
  "data_dir": "/srv/reqman/data",
  "mode": "server",
  "server": {
    "base_url": "https://reqman.example.com",
    "cookie_secure": true,
    "admins": ["alice@example.com"],
    "auth_providers": [
      {"id": "github", "type": "github", "name": "GitHub",
       "url": "https://github.com", "client_id": "<GitHub client ID>"},
      {"id": "gitlab", "type": "gitlab", "name": "GitLab",
       "url": "https://gitlab.example.com", "client_id": "<GitLab application ID>",
       "git_hosts": ["ssh.gitlab.example.com"]}
    ]
  }
}
```

- `admins`: emails (as the provider reports them) that become server
  admins at login. Server admins manage users, memberships and
  server-wide settings.
- `git_hosts`: other host names of the same GitLab, such as an ssh host,
  so that project remotes on them are recognised for repository-based
  roles.
- Several providers can be enabled side by side. The login page shows one
  button per provider.

**Secrets.** `REQMAN_AUTH_<ID>_CLIENT_SECRET` is read from the environment,
where `<ID>` is the provider id in upper case. `REQMAN_AUTH_<ID>_CLIENT_ID`
can likewise override the client ID. With `./etc/start.sh`, put them in
`.reqman/server.env` (git-ignored) as `KEY=value` lines. On a real server,
use your service manager's environment file or a secret store.

**Local test on your own machine.** Use `"base_url": "http://localhost:5173"`,
`"frontend_url": "http://localhost:5173"` and `"cookie_secure": false`.
Register a separate OAuth application for that callback.

## 6. Start

```sh
./etc/start.sh --server            # git checkout
reqman serve --config /etc/reqman/config.json   # installed package (REQMAN_MODE=server or "mode": "server")
```

The server **refuses to start** in server mode when no login provider is
configured. It never runs without login by accident.

Check that it is up:

- `GET <base_url>/edition` returns `{"mode": "server", "providers": [...]}`.
- `GET <base_url>/projects` returns 401 until you log in.

## 7. Who may do what

A user's role in a project is one of viewer, reviewer, editor or
project-admin.

- **Projects whose git remote is on the same GitHub/GitLab the user logged
  in with:** the role follows the user's permission on that repository.

  | GitLab | GitHub | ReqManTool |
  |---|---|---|
  | Guest / Reporter | Read | viewer |
  | — | Triage | reviewer |
  | Developer | Write | editor |
  | Maintainer / Owner | Maintain / Admin | project-admin |

  Change access in GitLab/GitHub and ReqManTool follows. Roles are cached
  for a few minutes; server admins can force a refresh with
  "Sync access now".
- **Other projects:** roles come from memberships assigned on the admin
  pages, or from `group_roles` rules (IdP group → role).

## 8. Projects in your users' GitHub / GitLab repositories (discovery)

Users who log in with GitHub or GitLab see, in the **Workspace** tab, a
section "Available on GitHub" (or GitLab) listing the ReqManTool projects
in the repositories they can access, and open one with **Open**. No
provider connection or personal access token is needed: the server uses
the user's login token, server-side only. The token never reaches the
browser, a log, the repository's configuration or a remote URL.

- **What is found.** Repositories tagged with the topic `reqmantool`
  ("Publish project" adds it), and repositories whose `project.json` at the
  root of the `current` branch is a ReqManTool project (a background scan).
  GitHub: the user's own, collaborator and organisation repositories.
  GitLab: the projects the user is a member of.
- **Opening** clones the repository once into the server's data folder,
  shared by every user. Opening it again later fetches and fast-forwards
  it. Opening needs at least GitHub Read / GitLab Guest on the repository.
  What a user may do in the opened project follows their repository
  permission (section 7), so discovery needs `repo_roles` enabled (the
  default).
- **Scopes.** GitLab: `read_api` (section 3). GitHub OAuth App: private
  repositories need the `repo` scope (section 2); without it only public
  repositories are found and cloned.
- **Settings** (`server.discovery`, all optional):

  ```json
  "discovery": {"enabled": true, "cache_minutes": 15, "max_repos": 200}
  ```

  The scan is cached per user for `cache_minutes`; the refresh button scans
  again. At most `max_repos` repositories are looked at per scan, to stay
  within the providers' API rate limits; the list says when some were left
  out. Provider errors (for example a rate limit) are shown as the
  provider words them.

## 9. Checklist

- [ ] OAuth application created; callback URL is exactly `<base_url>/auth/callback/<id>`.
- [ ] Client ID in `config.json`; client secret only in the environment.
- [ ] `base_url` is the address users actually open; `cookie_secure: true` behind https.
- [ ] At least one email in `admins`.
- [ ] `/edition` shows the provider, and `/projects` returns 401 before login.
- [ ] For GitLab repository roles and discovery: the `read_api` scope is granted.
- [ ] For private GitHub repositories (OAuth App): `repo` added to the provider's `scopes`.
