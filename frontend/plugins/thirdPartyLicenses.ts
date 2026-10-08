// Third-party licence list of the frontend bundle, generated at build time.
//
// Walks the production dependency graph (dependencies, optionalDependencies
// and peerDependencies, resolved the way Node does: nearest node_modules
// upward) from frontend/package.json, and reads each package's
// package.json `license` field and its LICENSE/LICENCE/COPYING/NOTICE file.
// devDependencies (build tools) are not part of the bundle and are skipped.
// The list is a superset of what ends up in the bundle (tree-shaking is not
// taken into account). No new npm dependency: plain node:fs.
//
// `vite build` emits it as `assets/third-party-licenses.json` (served by the
// backend's /assets mount in a release package); the dev server answers the
// same path from memory. A failure never breaks the build: it is logged and
// an empty/partial list is emitted.
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import type { Plugin } from 'vite'

export const THIRD_PARTY_LICENSES_FILE = 'assets/third-party-licenses.json'

export interface ThirdPartyPackage {
  name: string
  version: string
  license: string
  homepage: string | null
  license_text: string | null
}

interface PackageJson {
  name?: string
  version?: string
  license?: unknown
  licenses?: unknown
  homepage?: unknown
  repository?: unknown
  dependencies?: Record<string, string>
  optionalDependencies?: Record<string, string>
  peerDependencies?: Record<string, string>
}

const LICENSE_FILE = /^(licen[cs]e|copying|notice)(\.|-|$)/i

function readJson(path: string): PackageJson | null {
  try {
    return JSON.parse(readFileSync(path, 'utf8')) as PackageJson
  } catch {
    return null
  }
}

/** The directory of `name` as Node would resolve it from `fromDir`, or null if not installed. */
function resolvePackageDir(name: string, fromDir: string): string | null {
  let dir = fromDir
  for (;;) {
    const candidate = join(dir, 'node_modules', name)
    if (existsSync(join(candidate, 'package.json'))) return candidate
    const parent = dirname(dir)
    if (parent === dir) return null
    dir = parent
  }
}

function licenseName(pkg: PackageJson): string {
  const { license, licenses } = pkg
  if (typeof license === 'string' && license.trim()) return license.trim()
  if (license && typeof license === 'object' && 'type' in license && typeof license.type === 'string') return license.type
  if (Array.isArray(licenses)) {
    const names = licenses
      .map((item: unknown) => (item && typeof item === 'object' && 'type' in item ? String(item.type) : String(item)))
      .filter(Boolean)
    if (names.length) return names.join(' OR ')
  }
  return 'UNKNOWN'
}

function homepage(pkg: PackageJson): string | null {
  if (typeof pkg.homepage === 'string' && pkg.homepage) return pkg.homepage
  const repo = pkg.repository
  const url = typeof repo === 'string' ? repo : repo && typeof repo === 'object' && 'url' in repo ? repo.url : null
  if (typeof url !== 'string' || !url) return null
  return url.replace(/^git\+/, '').replace(/\.git$/, '').replace(/^git:\/\//, 'https://')
}

function licenseText(dir: string): string | null {
  try {
    const texts = readdirSync(dir)
      .filter((file) => LICENSE_FILE.test(file))
      .sort()
      .map((file) => {
        try {
          return readFileSync(join(dir, file), 'utf8').trim()
        } catch {
          return ''
        }
      })
      .filter(Boolean)
    return texts.length ? texts.join('\n\n') : null
  } catch {
    return null
  }
}

/** Production dependencies of the package at `rootDir`, with their licences, sorted by name. */
export function collectThirdPartyLicenses(rootDir: string): ThirdPartyPackage[] {
  const root = readJson(join(rootDir, 'package.json'))
  if (!root) return []
  const found = new Map<string, ThirdPartyPackage>()
  const visitedDirs = new Set<string>()
  const pending: Array<[string, string]> = []
  const enqueue = (pkg: PackageJson, fromDir: string) => {
    for (const deps of [pkg.dependencies, pkg.optionalDependencies, pkg.peerDependencies]) {
      for (const name of Object.keys(deps ?? {})) pending.push([name, fromDir])
    }
  }
  enqueue(root, rootDir)
  while (pending.length) {
    const [name, fromDir] = pending.pop()!
    const dir = resolvePackageDir(name, fromDir)
    if (!dir || visitedDirs.has(dir)) continue
    visitedDirs.add(dir)
    const pkg = readJson(join(dir, 'package.json'))
    if (!pkg) continue
    const version = pkg.version ?? 'unknown'
    const key = `${pkg.name ?? name}@${version}`
    if (!found.has(key)) {
      found.set(key, {
        name: pkg.name ?? name,
        version,
        license: licenseName(pkg),
        homepage: homepage(pkg),
        license_text: licenseText(dir),
      })
    }
    enqueue(pkg, dir)
  }
  return [...found.values()].sort((a, b) => a.name.localeCompare(b.name) || a.version.localeCompare(b.version))
}

function safeCollect(rootDir: string, warn: (message: string) => void): string {
  let packages: ThirdPartyPackage[] = []
  try {
    packages = collectThirdPartyLicenses(rootDir)
  } catch (error) {
    warn(`[reqman] could not collect third-party licences: ${String(error)}`)
  }
  return `${JSON.stringify({ packages }, null, 2)}\n`
}

/** Vite plugin: emits THIRD_PARTY_LICENSES_FILE in `vite build`, serves it in `vite` (dev). */
export function thirdPartyLicenses(rootDir: string): Plugin {
  return {
    name: 'reqman-third-party-licenses',
    generateBundle() {
      this.emitFile({
        type: 'asset',
        fileName: THIRD_PARTY_LICENSES_FILE,
        source: safeCollect(rootDir, (message) => this.warn(message)),
      })
    },
    configureServer(server) {
      server.middlewares.use(`/${THIRD_PARTY_LICENSES_FILE}`, (_req, res) => {
        res.setHeader('Content-Type', 'application/json')
        res.end(safeCollect(rootDir, (message) => server.config.logger.warn(message)))
      })
    },
  }
}
