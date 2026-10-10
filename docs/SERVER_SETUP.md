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
| GitHub / GitLab / your SSO | An **OAuth application** (on GitHub also a **GitHub App**) that lets ReqManTool sign users in. |
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
and clones with a user's login token; it never pushes with it.

### GitHub App — the narrower alternative to the `repo` scope

A GitHub App grants fine-grained, read-only repository permissions instead
of the broad `repo` scope and is installed per organisation or per
repository. Configure it **beside** (or instead of) the OAuth App above —
login, callback, repository roles and discovery work the same way:

1. Create it under **Settings → Developer settings → GitHub Apps → New
   GitHub App**; for a company, create it under the **organisation**
   (Organisation → Settings → Developer settings → GitHub Apps). Name it,
   e.g. `ReqManTool`, and set the **Homepage URL** to your `base_url`.
2. **Repository permissions** — the minimal set, all read-only (it covers
   sign-in, repository roles, project discovery and cloning):
   - **Metadata:** read-only — names, visibility, topics (the
     `reqmantool` topic) and the `permissions` object the roles are read
     from.
   - **Contents:** read-only — reads `project.json` and clones.
3. **User authorization:** check **Request user authorization (OAuth)
   during installation** — without it, installing the App creates no
   sign-in for ReqManTool — and leave **Expire user tokens after** at its
   default: GitHub Apps hand out expiring user-to-server tokens with a
   refresh token, which ReqManTool renews server-side on its own (nothing
   to rotate by hand). Set the **User authorization callback URL** to the
   usual callback, `<base_url>/auth/callback/<provider id>`.
4. **Install the app** (below the form, or later: Settings → Developer
   settings → GitHub Apps → your app → Install App) on the organisation(s)
   or repositories that hold ReqManTool projects. Users who sign in
   through the app must be allowed on those repositories; discovery shows
   exactly the repositories of the installations they can access.
5. Copy the **Client ID** and generate a **client secret** (under
   *Client secrets and Webhook*); store the secret as
   `REQMAN_AUTH_<ID>_CLIENT_SECRET`, where `<ID>` is the provider id in
   upper case (section 5).
6. Add the provider to `config.json` — `type` stays `github`, and
   `github_app: true` is the one difference; discovery then reads the
   App's installations instead of the user's repository list:

   ```json
   {"id": "github-app", "type": "github", "name": "GitHub App",
    "url": "https://github.com", "client_id": "<App client ID>",
    "github_app": true}
   ```

   With both providers configured, the login page shows one button per
   provider, so users can migrate at their own pace.

With the minimal read-only permissions above, "Publish project" (which
creates a repository) needs more; grant the App the extra permissions only
if you use it, or keep an OAuth App with the `repo` scope for publishing.

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

## 3b. Gerrit (self-hosted)

Gerrit supports two authentication modes for ReqManTool:

**Option A: Gerrit's built-in OAuth plugin (recommended)**
1. Install and enable the Gerrit OAuth plugin.
2. Create an OAuth client in Gerrit:
   - **Name:** `ReqManTool`
   - **Redirect URI:** `<base_url>/auth/callback/gerrit`
   - **Confidential:** yes
   - **Scopes:** `openid`, `profile`, `email`
3. Save, then copy the **Client ID** and **Secret**. Store the secret as
   `REQMAN_AUTH_GERRIT_CLIENT_SECRET`.

**Option B: Company OIDC/OAuth that fronts Gerrit (e.g., Keycloak, Entra ID)**
1. Register a web application in your OIDC provider with callback URL
   `<base_url>/auth/callback/gerrit`.
2. Give it the scopes `openid profile email`, and a `groups` claim if you
   want group-based roles.
3. Set `issuer` to the provider's issuer URL. ReqManTool reads
   `<issuer>/.well-known/openid-configuration` itself.
4. Use the OIDC provider's client ID and secret. Store the secret as
   `REQMAN_AUTH_GERRIT_CLIENT_SECRET`.

**Configuring Gerrit credentials for push (HTTPS/SSH)**
For each user who will push to Gerrit:
1. In ReqManTool, go to **User Menu → Gerrit Credentials**.
2. Enter your **Gerrit HTTP username** and **HTTP password** (from Gerrit's
   HTTP Password settings), or upload your **SSH private key**.
3. These credentials are encrypted and stored server-side. They are used
   only for the one git process during push/pull.

**Push for review**
When pushing to a Gerrit remote, you can choose **"Push for review"**
(in the Git panel) to push to `refs/for/current` instead of `current`.
This creates a Gerrit change for review. The change number and URL are
shown in the Git panel.

**Config example:**
```json
{
  "id": "gerrit",
  "type": "gerrit",
  "name": "Company Gerrit",
  "url": "https://gerrit.example.com",
  "client_id": "<client-id>",
  "client_secret_env": "REQMAN_AUTH_GERRIT_CLIENT_SECRET",
  "git_hosts": ["ssh.gerrit.example.com"]
}
```

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
browser, a log, the repository's configuration or a remote URL. A GitHub
App's user-to-server tokens expire; the server renews them with the
session's refresh token, so users notice nothing.

- **What is found.** Repositories tagged with the topic `reqmantool`
  ("Publish project" adds it), and repositories whose `project.json` at the
  root of the `current` branch is a ReqManTool project (a background scan).
  GitHub OAuth App: the user's own, collaborator and organisation
  repositories. GitHub App: the repositories of the installations the user
  can access. GitLab: the projects the user is a member of.
- **Opening** clones the repository once into the server's data folder,
  shared by every user. Opening it again later fetches and fast-forwards
  it. Opening needs at least GitHub Read / GitLab Guest on the repository.
  What a user may do in the opened project follows their repository
  permission (section 7), so discovery needs `repo_roles` enabled (the
  default).
- **Scopes.** GitLab: `read_api` (section 3). GitHub OAuth App: private
  repositories need the `repo` scope (section 2); without it only public
  repositories are found and cloned. GitHub App: no scope — the App's
  Metadata + Contents read permissions cover it (section 2).
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
- [ ] For a GitHub App provider: `github_app: true`, Metadata + Contents
      read, installed on the repositories, callback URL and "Request user
      authorization (OAuth) during installation" set (section 2).
