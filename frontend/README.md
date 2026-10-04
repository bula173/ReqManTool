# ReqManTool frontend

React + TypeScript + Vite, Mantine components, TanStack Query for server
state. See [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) for the
overall system design, including the frontend's file structure and its
tree-flattening approach to the requirements table.

## Development

```sh
npm install
npm run dev      # http://localhost:5173, expects the backend on :8899
npm run build     # tsc -b && vite build
npm run lint      # oxlint
```

Configure the backend URL via a `.env` (copy `.env.example`):
`VITE_API_BASE_URL` (default `http://localhost:8899`) and `VITE_WS_URL`
(default `ws://localhost:8899/ws`).

From the repo root, `../etc/start.sh` starts both the backend and this dev
server together.
