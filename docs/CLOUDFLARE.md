# Publishing ReqManTool with Cloudflare Tunnel + Access

This guide puts a ReqManTool instance running on your own machine at
`https://reqman.mksystemdesign.org`. The instance is protected by a
Cloudflare login, and it needs no open router ports and no public IP.

```
browser ──HTTPS──▶ Cloudflare edge ──(Access login)──▶ Tunnel ──▶ cloudflared on your machine
                                                                      │
                                         localhost:4173  vite preview (built frontend)
                                                                      │  proxies /projects, /config, /fs, /ws
                                         localhost:8899  FastAPI backend ──▶ data directory (JSON + git)
```

- **Cloudflare Tunnel** gives you the public HTTPS hostname. WebSockets (live sync) work through it.
- **Cloudflare Access** puts a login in front of the hostname. ReqManTool has no authentication of its own, so Access is mandatory. Without it, anyone with the URL can edit your data and browse your folders through the data-folder picker.

Both are free for small teams; the Zero Trust Free plan covers up to 50 users.

Dashboard menu names change from time to time. The paths below were correct when this guide was written.

## 0. Prerequisites

- `mksystemdesign.org` is already a Cloudflare zone. Its nameservers are `decker.ns.cloudflare.com` and `meera.ns.cloudflare.com`.
- The ReqManTool repository is checked out with the backend venv and frontend `node_modules`. `etc/start-backend.sh` and `etc/start-frontend.sh` create them on first run.
- macOS with Homebrew. On Linux, install `cloudflared` from Cloudflare's package repository instead.

## 1. Create a Zero Trust organization (one-time)

1. In the Cloudflare dashboard, open **Zero Trust**.
2. Pick a team name. It becomes `<team>.cloudflareaccess.com`, which is the domain of the login page.
3. Choose the **Free** plan. Cloudflare may ask for a payment method even for the free plan.

## 2. Create the Access application first

Do this before the tunnel exists, so the hostname is never reachable without a login.

1. Go to **Zero Trust → Access → Applications → Add an application → Self-hosted**.
2. Fill in the application:
   - **Application name**: `ReqManTool`
   - **Session duration**: for example, `24 hours`
   - **Application domain**: subdomain `reqman`, domain `mksystemdesign.org`
3. **Identity providers**: keep **One-time PIN**, which sends a login code by email. Add Google or GitHub later if you like.
4. Add a policy:
   - **Policy name**: `Allowed people`
   - **Action**: `Allow`
   - **Include**: `Emails`, followed by your email address and those of the colleagues who should get in.
5. Save.

## 3. Install cloudflared and create the tunnel

```sh
brew install cloudflared
```

1. Go to **Zero Trust → Networks → Tunnels** (older dashboards: **Access → Tunnels**) **→ Create a tunnel → Cloudflared**, and name it `reqman`.
2. The dashboard shows an install command containing a token. On macOS, run:
   ```sh
   sudo cloudflared service install <TOKEN>
   ```
   This registers `cloudflared` as a launchd service, so it starts at boot. Treat the token like a password: anyone who has it can run your tunnel.
3. Click **Next → Public hostname**:
   - **Subdomain**: `reqman`, **Domain**: `mksystemdesign.org`, **Path**: leave empty
   - **Service**: `HTTP` and `localhost:4173`
   - Expand **Additional application settings → HTTP Settings**, and set **HTTP Host Header** to `localhost`.

     This setting is required. The Vite preview server answers **403** to unknown host names. That check is a protection against DNS rebinding, and it is better to keep it on.
4. Save. Cloudflare creates the proxied DNS record `reqman.mksystemdesign.org` for you.

## 4. Run ReqManTool for the tunnel

```sh
etc/start-public.sh reqman.mksystemdesign.org --dir ~/ReqManData
```

The script:
- starts the backend on `localhost:8899`; the arguments after the host name are passed to the backend, so `--dir` selects your data directory;
- builds the frontend so that API calls stay on the page's own address (an empty `VITE_API_BASE_URL`) and live sync uses `wss://reqman.mksystemdesign.org/ws`;
- serves the build on `localhost:4173`, with `/projects`, `/config`, `/fs` and `/ws` proxied to the backend (see `frontend/vite.config.ts`).

Opening or refreshing the address of a project or module page in the browser still loads the app, because the proxy only passes non-HTML requests on those paths to the backend.

Keep both services bound to `localhost`, which is the default. Do not start the backend with `--host 0.0.0.0`. The tunnel is the only way in.

The script runs in the foreground and stops when you close the terminal. A per-user background service (`reqman service install`) is planned in the "Distribution package + user-space install" backlog item.

## 5. Verify

1. In a private browser window, open `https://reqman.mksystemdesign.org`. You should see the Cloudflare Access login page, not the app.
2. Enter an allowed email address and the PIN you receive. The app loads.
3. Open a project, then edit a requirement in a second tab. The first tab updates on its own, which confirms that the WebSocket works through Access and the tunnel.
4. Check the unauthenticated response:
   ```sh
   curl -sI https://reqman.mksystemdesign.org/projects | head -3
   ```
   It should be a `302` redirect to `<team>.cloudflareaccess.com`, and never JSON.

## Security notes

- Everyone allowed by the Access policy has full control: they can edit and purge data, switch the data directory, and browse folders on your machine through the folder picker. Only allow people you trust with that machine.
- Removing someone from the policy revokes access the next time their session is checked. To force it immediately, revoke their session in **Zero Trust → My Team → Users**.
- Your requirements data never leaves your machine. Cloudflare only proxies the traffic.

## Stop or remove

- **Pause publishing**: stop `etc/start-public.sh`. Cloudflare then shows a 502 error after the login.
- **Remove the tunnel**: delete the public hostname or the tunnel in the dashboard, then run `sudo cloudflared service uninstall`.
- **Remove the login**: delete the Access application, but only after the hostname has been removed.
