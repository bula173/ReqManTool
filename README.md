# ReqManTool

A simple requirements management tool that stores requirements as JSON text files.

## Overview

ReqManTool is split into two parts:

- **backend/** — handles the core logic: reading/writing requirement JSON files, and adding, removing, updating, and linking requirements.
- **frontend/** — the user interface.

The backend and frontend communicate over a WebSocket connection using RPC (remote procedure calls).

## Features

- Add, remove, and update requirements
- Link requirements to one another (e.g. parent/child, trace links)
- Requirements are stored as plain JSON text files (easy to diff, version, and review in git)

## Project layout

```
ReqManTool/
├── backend/    # core logic + WebSocket/RPC server
├── frontend/   # user interface
└── docs/       # design notes, requirements schema, ADRs
```

## Status

Project scaffold — implementation in progress.
