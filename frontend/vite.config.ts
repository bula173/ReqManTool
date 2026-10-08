import react from '@vitejs/plugin-react'
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import type { IncomingMessage } from 'node:http'
import { fileURLToPath } from 'node:url'
import { defineConfig, type ProxyOptions } from 'vite'

// Backend the dev/preview server proxies API and WebSocket calls to. Lets the
// app run behind a single public origin (e.g. a Cloudflare Tunnel) when built
// with an empty VITE_API_BASE_URL; see docs/CLOUDFLARE.md.
const apiTarget = process.env.REQMAN_API_TARGET ?? 'http://localhost:8899'

// `/projects/...` is both a backend route and a client-side route. A browser
// page load of a client route (Accept: text/html) gets the SPA instead.
// Mirrors the client routes in src/App.tsx and SPA_ROUTE in backend/reqman/app.py
// (the backend serves the built frontend itself in a release package).
const SPA_ROUTE =
  /^\/projects\/[^/]+(\/modules\/[^/]+|\/baselines\/[^/]+|\/commits(\/[^/]+(\/browse)?)?|\/reports\/(traceability|verification\/[^/]+|execution\/[^/]+))?\/?$/

function spaBypass(req: IncomingMessage): string | undefined {
  const path = (req.url ?? '').split('?')[0]
  const wantsHtml = req.headers.accept?.includes('text/html') ?? false
  return wantsHtml && SPA_ROUTE.test(path) ? '/index.html' : undefined
}

const apiProxy: Record<string, ProxyOptions> = {
  '/projects': { target: apiTarget, bypass: spaBypass },
  '/config': { target: apiTarget },
  '/fs': { target: apiTarget },
  '/version': { target: apiTarget },
  '/templates': { target: apiTarget },
  '/module-templates': { target: apiTarget },
  '/prompts': { target: apiTarget },
  '/providers': { target: apiTarget },
  '/docs': { target: apiTarget },
  '/openapi.json': { target: apiTarget },
  '/ws': { target: apiTarget.replace(/^http/, 'ws'), ws: true },
}

// --- version / build id (shown in the app footer) ----------------------
// The tool version has ONE source: `__version__` in backend/reqman/__init__.py.
// The build id is "<short commit> (<build date>)" for `vite build`, "dev" for
// the dev server or when git is unavailable; REQMAN_BUILD overrides it (e.g.
// a release tag in CI). The backend reports its own pair at GET /version.
const BACKEND_INIT = fileURLToPath(new URL('../backend/reqman/__init__.py', import.meta.url))
const DEV_BUILD = 'dev'

function readToolVersion(): string {
  try {
    const match = /^__version__\s*=\s*["']([^"']+)["']\s*$/m.exec(readFileSync(BACKEND_INIT, 'utf8'))
    if (match) return match[1]
  } catch {
    // fall through: building without the backend checkout next to us
  }
  console.warn(`[reqman] could not read __version__ from ${BACKEND_INIT}; using "unknown"`)
  return 'unknown'
}

function localDate(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function readBuildId(isBuild: boolean): string {
  const override = process.env.REQMAN_BUILD?.trim()
  if (override) return override
  if (!isBuild) return DEV_BUILD
  try {
    const commit = execFileSync('git', ['rev-parse', '--short', 'HEAD'], {
      cwd: fileURLToPath(new URL('.', import.meta.url)),
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
      timeout: 2000,
    }).trim()
    return commit ? `${commit} (${localDate(new Date())})` : DEV_BUILD
  } catch {
    return DEV_BUILD
  }
}

// https://vite.dev/config/
export default defineConfig(({ command }) => ({
  plugins: [react()],
  define: {
    __REQMAN_VERSION__: JSON.stringify(readToolVersion()),
    __REQMAN_BUILD__: JSON.stringify(readBuildId(command === 'build')),
  },
  server: { proxy: apiProxy },
  preview: { proxy: apiProxy },
}))
