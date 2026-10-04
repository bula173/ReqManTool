import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importlib
import re
import subprocess

import pytest
from fastapi.testclient import TestClient

import reqman
from reqman import version as version_module


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REQMAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(tmp_path / "config.json"))
    import reqman.app as app_module

    importlib.reload(app_module)
    return TestClient(app_module.app)


@pytest.fixture
def fresh_git_cache():
    version_module._git_build.cache_clear()
    yield
    version_module._git_build.cache_clear()


def test_version_is_a_single_line_string_literal():
    # frontend/vite.config.ts parses this exact line out of __init__.py.
    init_text = (Path(reqman.__file__)).read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']\s*$', init_text, re.MULTILINE)
    assert match is not None
    assert match.group(1) == reqman.__version__


def test_get_version_returns_version_and_build(client, monkeypatch):
    monkeypatch.delenv("REQMAN_BUILD", raising=False)
    resp = client.get("/version")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"version", "build"}
    assert body["version"] == reqman.__version__
    assert body["build"] == "dev" or re.fullmatch(r"[0-9a-f]{4,40} \(\d{4}-\d{2}-\d{2}\)", body["build"])


def test_build_env_var_overrides_git(client, monkeypatch):
    monkeypatch.setenv("REQMAN_BUILD", "  v0.1.0-rc1  ")
    assert client.get("/version").json() == {"version": reqman.__version__, "build": "v0.1.0-rc1"}


def test_blank_build_env_var_is_ignored(monkeypatch, fresh_git_cache):
    monkeypatch.setenv("REQMAN_BUILD", "   ")
    monkeypatch.setattr(
        version_module.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="abc1234 2026-10-04\n", stderr=""),
    )
    assert version_module.build_id() == "abc1234 (2026-10-04)"


def test_build_falls_back_to_dev_without_git(monkeypatch, fresh_git_cache):
    monkeypatch.delenv("REQMAN_BUILD", raising=False)

    def no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(version_module.subprocess, "run", no_git)
    assert version_module.build_id() == "dev"


def test_build_falls_back_to_dev_outside_a_checkout(monkeypatch, fresh_git_cache):
    monkeypatch.delenv("REQMAN_BUILD", raising=False)
    monkeypatch.setattr(
        version_module.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 128, stdout="", stderr="fatal: not a git repository"),
    )
    assert version_module.build_id() == "dev"


def test_ws_version_get_matches_rest_and_does_not_broadcast(client, monkeypatch):
    monkeypatch.setenv("REQMAN_BUILD", "test-build")
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "version.get"})
        response = ws.receive_json()
        assert response == {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"version": reqman.__version__, "build": "test-build"},
        }
        # A follow-up request gets its own response next: no "changed" event
        # was queued in between.
        ws.send_json({"jsonrpc": "2.0", "id": 2, "method": "project.list"})
        assert ws.receive_json() == {"jsonrpc": "2.0", "id": 2, "result": []}
    assert client.get("/version").json() == response["result"]


def _fake_git(stdout, returncode=0, calls=None):
    def run(*args, **kwargs):
        if calls is not None:
            calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")

    return run


def test_build_falls_back_to_dev_when_git_times_out(monkeypatch, fresh_git_cache):
    monkeypatch.delenv("REQMAN_BUILD", raising=False)

    def hanging_git(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0] if args else "git", timeout=kwargs.get("timeout", 2))

    monkeypatch.setattr(version_module.subprocess, "run", hanging_git)
    assert version_module.build_id() == "dev"
    assert version_module.version_info() == {"version": reqman.__version__, "build": "dev"}


def test_git_lookup_is_bounded_by_a_timeout(monkeypatch, fresh_git_cache):
    # A hung git (e.g. waiting on a lock or credential prompt) must not block
    # the /version endpoint indefinitely.
    monkeypatch.delenv("REQMAN_BUILD", raising=False)
    calls = []
    monkeypatch.setattr(version_module.subprocess, "run", _fake_git("abc1234 2026-10-04\n", calls=calls))
    version_module.build_id()
    assert len(calls) == 1
    timeout = calls[0][1].get("timeout")
    assert timeout is not None and 0 < timeout <= 5


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "\n",
        "abc1234\n",
        "abc1234 2026-10-04 extra\n",
        "abc1234 2026-10-04 12:00:00 +0200\n",
    ],
    ids=["empty", "blank-line", "one-token", "three-tokens", "many-tokens"],
)
def test_build_falls_back_to_dev_on_malformed_git_output(monkeypatch, fresh_git_cache, stdout):
    monkeypatch.delenv("REQMAN_BUILD", raising=False)
    monkeypatch.setattr(version_module.subprocess, "run", _fake_git(stdout))
    assert version_module.build_id() == "dev"


def test_git_build_is_computed_once_per_process(monkeypatch, fresh_git_cache):
    monkeypatch.delenv("REQMAN_BUILD", raising=False)
    calls = []
    monkeypatch.setattr(version_module.subprocess, "run", _fake_git("abc1234 2026-10-04\n", calls=calls))

    assert version_module.build_id() == "abc1234 (2026-10-04)"
    assert version_module.build_id() == "abc1234 (2026-10-04)"
    assert version_module.version_info()["build"] == "abc1234 (2026-10-04)"

    # A later commit in the checkout does not change the running build id.
    monkeypatch.setattr(version_module.subprocess, "run", _fake_git("def5678 2026-10-05\n", calls=calls))
    assert version_module.build_id() == "abc1234 (2026-10-04)"
    assert len(calls) == 1


def test_git_failure_is_also_computed_once_per_process(monkeypatch, fresh_git_cache):
    monkeypatch.delenv("REQMAN_BUILD", raising=False)
    calls = []
    monkeypatch.setattr(version_module.subprocess, "run", _fake_git("", returncode=128, calls=calls))
    assert version_module.build_id() == "dev"
    assert version_module.build_id() == "dev"
    assert len(calls) == 1


def test_rest_and_ws_share_the_cached_git_build(client, monkeypatch, fresh_git_cache):
    monkeypatch.delenv("REQMAN_BUILD", raising=False)
    calls = []
    monkeypatch.setattr(version_module.subprocess, "run", _fake_git("abc1234 2026-10-04\n", calls=calls))

    expected = {"version": reqman.__version__, "build": "abc1234 (2026-10-04)"}
    assert client.get("/version").json() == expected
    assert client.get("/version").json() == expected
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "version.get"})
        assert ws.receive_json()["result"] == expected
    assert len(calls) == 1


def test_build_env_var_is_read_on_every_call(monkeypatch, fresh_git_cache):
    calls = []
    monkeypatch.setattr(version_module.subprocess, "run", _fake_git("abc1234 2026-10-04\n", calls=calls))

    monkeypatch.setenv("REQMAN_BUILD", "first")
    assert version_module.build_id() == "first"
    monkeypatch.setenv("REQMAN_BUILD", "second")
    assert version_module.build_id() == "second"
    monkeypatch.delenv("REQMAN_BUILD")
    assert version_module.build_id() == "abc1234 (2026-10-04)"
    # Setting the override after git was cached still takes effect.
    monkeypatch.setenv("REQMAN_BUILD", "third")
    assert version_module.build_id() == "third"
    monkeypatch.setenv("REQMAN_BUILD", "")
    assert version_module.build_id() == "abc1234 (2026-10-04)"


def test_build_env_var_change_is_visible_over_rest(client, monkeypatch):
    monkeypatch.setenv("REQMAN_BUILD", "first")
    assert client.get("/version").json()["build"] == "first"
    monkeypatch.setenv("REQMAN_BUILD", "second")
    assert client.get("/version").json()["build"] == "second"


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"params": None},
        {"params": {}},
        {"params": []},
        {"params": {"project_id": "does-not-exist", "id": "nope", "unexpected": 1}},
        {"params": ["positional", 1]},
    ],
    ids=["omitted", "null", "empty-object", "empty-list", "unrelated-keys", "positional-list"],
)
def test_ws_version_get_ignores_params_and_needs_no_project(client, monkeypatch, extra):
    monkeypatch.setenv("REQMAN_BUILD", "test-build")
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"jsonrpc": "2.0", "id": 7, "method": "version.get", **extra})
        assert ws.receive_json() == {
            "jsonrpc": "2.0",
            "id": 7,
            "result": {"version": reqman.__version__, "build": "test-build"},
        }
    # Nothing was created as a side effect.
    assert client.get("/projects").json() == []


def test_ws_version_get_does_not_broadcast_to_other_clients(client, monkeypatch):
    # The sender is always excluded from "changed" broadcasts, so check a
    # second, passive client too.
    monkeypatch.setenv("REQMAN_BUILD", "test-build")
    with client.websocket_connect("/ws") as caller, client.websocket_connect("/ws") as observer:
        caller.send_json({"jsonrpc": "2.0", "id": 1, "method": "version.get"})
        assert "result" in caller.receive_json()
        observer.send_json({"jsonrpc": "2.0", "id": 2, "method": "project.list"})
        assert observer.receive_json() == {"jsonrpc": "2.0", "id": 2, "result": []}


def test_get_version_does_not_broadcast_to_ws_clients(client, monkeypatch):
    monkeypatch.setenv("REQMAN_BUILD", "test-build")
    with client.websocket_connect("/ws") as ws:
        assert client.get("/version").status_code == 200
        ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "project.list"})
        assert ws.receive_json() == {"jsonrpc": "2.0", "id": 1, "result": []}


def test_version_survives_data_dir_switch(client, tmp_path, monkeypatch):
    monkeypatch.setenv("REQMAN_BUILD", "test-build")
    expected = {"version": reqman.__version__, "build": "test-build"}
    assert client.get("/version").json() == expected

    new_dir = tmp_path / "other-root"
    with client.websocket_connect("/ws") as ws:
        resp = client.post("/config", json={"data_dir": str(new_dir)})
        assert resp.status_code == 200, resp.text
        assert ws.receive_json() == {"event": "root_changed"}

        ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "version.get"})
        assert ws.receive_json() == {"jsonrpc": "2.0", "id": 1, "result": expected}

    assert client.get("/config").json()["data_dir"] == str(new_dir.resolve())
    assert client.get("/version").json() == expected
    # Reading the version writes nothing into either data directory.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.json", "other-root"]
    assert list(new_dir.iterdir()) == []


def test_version_response_key_order_is_version_then_build(client, monkeypatch):
    monkeypatch.setenv("REQMAN_BUILD", "test-build")
    resp = client.get("/version")
    assert resp.headers["content-type"].startswith("application/json")
    assert list(resp.json()) == ["version", "build"]
    assert resp.text.index('"version"') < resp.text.index('"build"')
    assert list(version_module.version_info()) == ["version", "build"]

    with client.websocket_connect("/ws") as ws:
        ws.send_text('{"jsonrpc": "2.0", "id": 1, "method": "version.get"}')
        raw = ws.receive_text()
    result_text = raw[raw.index('"result"'):]
    assert result_text.index('"version"') < result_text.index('"build"')


def test_version_is_exported_from_package_and_served_by_api(client, monkeypatch):
    monkeypatch.setenv("REQMAN_BUILD", "test-build")
    assert "__version__" in reqman.__all__
    from reqman import __version__

    assert isinstance(__version__, str) and __version__.strip() == __version__ != ""
    assert re.fullmatch(r"\d+\.\d+\.\d+([.+-][0-9A-Za-z.+-]+)?", __version__)
    assert version_module.version_info()["version"] == __version__
    assert client.get("/version").json()["version"] == __version__


def test_version_is_assigned_before_submodule_imports():
    # version.py does `from . import __version__`; if any submodule imported
    # by __init__.py ever imports `.version`, `__version__` must already be
    # bound or `import reqman` fails on a partially initialized module.
    lines = Path(reqman.__file__).read_text(encoding="utf-8").splitlines()
    version_line = next(i for i, line in enumerate(lines) if line.startswith("__version__"))
    first_import = next(i for i, line in enumerate(lines) if line.startswith(("from ", "import ")))
    assert version_line < first_import


@pytest.mark.parametrize(
    "stdout",
    ["abc1234 %cs\n", "abc1234 2026-1-4\n", "abc1234 yesterday\n"],
    ids=["old-git-literal-placeholder", "unpadded-date", "word"],
)
def test_build_falls_back_to_dev_when_date_token_is_not_a_date(monkeypatch, fresh_git_cache, stdout):
    # git < 2.21 does not know %cs and prints it literally.
    monkeypatch.delenv("REQMAN_BUILD", raising=False)
    monkeypatch.setattr(version_module.subprocess, "run", _fake_git(stdout))
    assert version_module.build_id() == "dev"


def test_git_lookup_uses_a_format_supported_by_old_git(monkeypatch, fresh_git_cache):
    monkeypatch.delenv("REQMAN_BUILD", raising=False)
    calls = []
    monkeypatch.setattr(version_module.subprocess, "run", _fake_git("abc1234 2026-10-04\n", calls=calls))
    assert version_module.build_id() == "abc1234 (2026-10-04)"
    argv = calls[0][0][0]
    assert "--date=short" in argv
    assert not any("%cs" in arg for arg in argv)


def test_git_lookup_ignores_log_show_signature_config(monkeypatch, fresh_git_cache):
    # With `log.showSignature=true` in the user's git config, a signed commit
    # adds gpg lines to `git log` stdout unless --no-show-signature is passed.
    monkeypatch.delenv("REQMAN_BUILD", raising=False)
    calls = []

    def git_with_show_signature(*args, **kwargs):
        calls.append((args, kwargs))
        argv = args[0]
        stdout = "abc1234 2026-10-04\n"
        if "--no-show-signature" not in argv:
            stdout = 'gpg: Signature made Sun Oct  4 12:00:00 2026\ngpg: Good signature from "Dev"\n' + stdout
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(version_module.subprocess, "run", git_with_show_signature)
    assert version_module.build_id() == "abc1234 (2026-10-04)"
    argv = calls[0][0][0]
    assert argv[:2] == ["git", "log"]
    assert "--no-show-signature" in argv
    assert "--format=%h %cd" in argv


def test_app_startup_prewarms_git_build(tmp_path, monkeypatch, fresh_git_cache):
    # WS methods run synchronously on the event loop, so the git subprocess
    # must not first run inside a `version.get` call.
    monkeypatch.setenv("REQMAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.delenv("REQMAN_BUILD", raising=False)
    import reqman.app as app_module

    importlib.reload(app_module)
    version_module._git_build.cache_clear()
    calls = []
    monkeypatch.setattr(version_module.subprocess, "run", _fake_git("abc1234 2026-10-04\n", calls=calls))
    with TestClient(app_module.app) as started:
        assert len(calls) == 1
        with started.websocket_connect("/ws") as ws:
            ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "version.get"})
            assert ws.receive_json()["result"]["build"] == "abc1234 (2026-10-04)"
    assert len(calls) == 1
