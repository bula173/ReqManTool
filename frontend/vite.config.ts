import react from '@vitejs/plugin-react'
import type { IncomingMessage } from 'node:http'
import { defineConfig, type ProxyOptions } from 'vite'

// Backend the dev/preview server proxies API and WebSocket calls to. Lets the
// app run behind a single public origin (e.g. a Cloudflare Tunnel) when built
// with an empty VITE_API_BASE_URL; see docs/CLOUDFLARE.md.
const apiTarget = process.env.REQMAN_API_TARGET ?? 'http://localhost:8899'

// `/projects/...` is both a backend route and a client-side route. A browser
// page load of a client route (Accept: text/html) gets the SPA instead.
const SPA_ROUTE = /^\/projects\/[^/]+(\/modules\/[^/]+)?\/?$/

function spaBypass(req: IncomingMessage): string | undefined {
  const path = (req.url ?? '').split('?')[0]
  const wantsHtml = req.headers.accept?.includes('text/html') ?? false
  return wantsHtml && SPA_ROUTE.test(path) ? '/index.html' : undefined
}

const apiProxy: Record<string, ProxyOptions> = {
  '/projects': { target: apiTarget, bypass: spaBypass },
  '/config': { target: apiTarget },
  '/fs': { target: apiTarget },
  '/ws': { target: apiTarget.replace(/^http/, 'ws'), ws: true },
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: { proxy: apiProxy },
  preview: { proxy: apiProxy },
})
