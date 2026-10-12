# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_submodules

# Path to the backend source root
try:
    spec_dir = os.path.abspath(os.path.dirname(__file__))
    BACKEND_ROOT = os.getenv('BACKEND_ROOT') or os.path.abspath(os.path.join(spec_dir, '..'))
    BASE_DIR = BACKEND_ROOT
except NameError:
    BASE_DIR = os.getcwd()

# Data directories to include (templates, prompts, static) relative to backend
DATA_DIRS = [
    (os.path.join(BASE_DIR, 'reqman', 'templates'), 'templates'),
    (os.path.join(BASE_DIR, 'reqman', 'prompts'), 'prompts'),
    (os.path.join(BASE_DIR, 'reqman', 'static'), 'static'),
]

# Hidden imports required for FastAPI, Starlette and uvicorn internals
HIDDEN_IMPORTS = [
    'fastapi',
    'fastapi.routing',
    'fastapi.exceptions',
    'starlette',
    'starlette.applications',
    'starlette.routing',
    'starlette.responses',
    'starlette.requests',
    'starlette.middleware',
    'uvicorn',
    'uvicorn.main',
    'uvicorn.config',
    'uvicorn.lifespan',
    'uvicorn.logging',
    'uvicorn.protocols',
    # collect any submodules that FastAPI may lazy‑load
] + collect_submodules('fastapi')

block_cipher = None

a = Analysis(
    [os.path.abspath(os.path.join(spec_dir, '..', 'reqman_server', 'run_server.py'))],
    pathex=[BASE_DIR],
    binaries=[],
    datas=DATA_DIRS,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='reqman_backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name='reqman_backend',
)
