# Contributing to ReqManTool

Thank you for considering a contribution. Please read this page before you
open a pull request.

## Contributor licence agreement (CLA)

ReqManTool is dual-licensed: the community edition in this repository is
licensed under the GNU Affero General Public License v3.0 only
(`AGPL-3.0-only`), and the copyright holder also offers it under a
commercial licence (see [LICENSING.md](LICENSING.md)).

Contributions are therefore accepted only under a contributor licence
agreement that allows the copyright holder to distribute them under both
the AGPL and the commercial licence. A pull request can be merged only after
its author has agreed to the CLA. The agreement text is not part of this
repository; ask for it in your pull request or in an issue.

If you cannot agree to the CLA, please open an issue describing the problem
or idea instead of sending code.

## Before you start

- Read [`docs/BACKLOG.md`](docs/BACKLOG.md) (scope and planned work) and
  [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (how the system is built).
- For larger changes, open an issue first so the scope can be agreed.
- Third-party dependencies must have a licence compatible with
  AGPL-3.0 (MIT, BSD, Apache-2.0 and similar). Add new ones only when a
  feature cannot reasonably be built without them.

## Development

See the README's "Quickstart (from a git checkout)" section. In short:

```sh
./etc/start.sh
```

## Checks

Run these before opening a pull request; CI runs the same checks:

```sh
cd backend && .venv/bin/python -m pytest -q
cd frontend && npm run build && npm run lint
```

Add or update tests for the behaviour you change.
