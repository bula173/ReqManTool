"""Smoke tests for importing a template JSON file as a user template
(File ▸ Load template ▸ Import template file)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importlib
import json

import pytest
from fastapi.testclient import TestClient

from reqman.service import ConflictError, ProjectService
from reqman.store import ProjectStore


def _template(**overrides) -> dict:
    data = {
        "format_version": 1,
        "id": "whatever",
        "name": "Imported",
        "description": "From a file",
        "attributes": [{"id": "status", "name": "Status", "type": "enum", "choices": ["A", "B"], "default": "A"}],
        "modules": [{"key": "a", "name": "Alpha", "prefix": "A"}, {"key": "b", "name": "Beta", "prefix": "B"}],
        "link_configurations": [{"key": "ab", "name": "Refines", "source_module": "a", "target_module": "b"}],
        "views": [{"name": "Main", "attribute_ids": ["object_text", "status", "link:ab"]}],
    }
    data.update(overrides)
    return data


def _text(data) -> str:
    return json.dumps(data, indent=2) + "\n"


@pytest.fixture
def service(tmp_path):
    return ProjectService(ProjectStore(tmp_path / "data"))


def _user_template_files(service) -> list[str]:
    directory = service.store.root / "_templates"
    return sorted(p.name for p in directory.glob("*.json")) if directory.is_dir() else []


# --- service ------------------------------------------------------------------------


def test_import_saves_canonical_user_template_and_is_usable(service):
    summary = service.import_template(_text(_template(extra_key="dropped")))
    assert summary["id"] == "user:imported"
    assert summary["source"] == "user"
    assert summary["error"] is None
    assert [m["key"] for m in summary["modules"]] == ["a", "b"]

    path = service.store.root / "_templates" / "imported.json"
    text = path.read_text(encoding="utf-8")
    assert text.endswith("}\n") and text.startswith('{\n  "format_version": 1,\n  "id": "imported",')
    data = json.loads(text)
    assert list(data) == [
        "format_version", "id", "name", "description", "attributes", "modules", "link_configurations", "views",
    ]
    assert "extra_key" not in data

    listed = next(t for t in service.list_templates() if t["id"] == "user:imported")
    assert listed["error"] is None
    project = service.create_project("From import", template_id="user:imported")
    assert [m.name for m in service.list_modules(project.id)] == ["Alpha", "Beta"]


def test_import_accepts_utf8_bom(service):
    summary = service.import_template("﻿" + _text(_template()))
    assert summary["id"] == "user:imported"


@pytest.mark.parametrize(
    "text, message",
    [
        ("{not json", "not valid JSON"),
        ("[]", "JSON object"),
        (_text(_template(format_version=2)), "newer"),
        (_text(_template(name="  ")), "'name'"),
        (_text(_template(views=[{"name": "V", "attribute_ids": ["missing"]}])), "missing"),
        (_text(_template(attributes=[{"id": "s", "name": "S", "type": "enum"}])), "choices"),
    ],
    ids=["bad-json", "not-object", "newer-version", "blank-name", "unknown-view-attribute", "enum-without-choices"],
)
def test_import_rejects_invalid_files_and_writes_nothing(service, text, message):
    with pytest.raises(ValueError, match=message) as info:
        service.import_template(text)
    assert not isinstance(info.value, ConflictError)
    assert "template file is invalid" in str(info.value)
    assert _user_template_files(service) == []


def test_import_rejects_non_string_and_oversized(service):
    with pytest.raises(ValueError, match="string"):
        service.import_template({"name": "x"})
    with pytest.raises(ValueError, match="too large"):
        service.import_template(" " * (1024 * 1024 + 1))
    assert _user_template_files(service) == []


def test_import_duplicate_name_is_a_conflict_and_never_overwrites(service):
    service.import_template(_text(_template()))
    before = (service.store.root / "_templates" / "imported.json").read_text()
    with pytest.raises(ConflictError, match="already exists"):
        service.import_template(_text(_template(name="IMPORTED", description="other")))
    # A built-in template's name is taken too.
    with pytest.raises(ConflictError):
        service.import_template(_text(_template(name="rbc_template")))
    assert _user_template_files(service) == ["imported.json"]
    assert (service.store.root / "_templates" / "imported.json").read_text() == before


def test_import_suffixes_the_file_stem_when_the_slug_is_taken(service):
    # A hand-written file already uses the slug but under a different name.
    directory = service.store.root / "_templates"
    directory.mkdir(parents=True)
    (directory / "imported.json").write_text(_text(_template(name="Something else")))
    summary = service.import_template(_text(_template()))
    assert summary["id"] == "user:imported-2"


# --- REST / WebSocket ---------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REQMAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(tmp_path / "config.json"))
    import reqman.app as app_module

    importlib.reload(app_module)
    return TestClient(app_module.app)


def _post(client, body, content_type="application/json"):
    content = body.encode("utf-8") if isinstance(body, str) else body
    return client.post("/templates/import", content=content, headers={"Content-Type": content_type})


def _rpc(ws, method, params, request_id=1):
    ws.send_text(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}))
    return json.loads(ws.receive_text())


def test_rest_import_template(client, tmp_path):
    resp = _post(client, "﻿" + _text(_template()))
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "user:imported"
    assert any(t["id"] == "user:imported" for t in client.get("/templates").json())

    assert _post(client, _text(_template(name="imported"))).status_code == 409
    assert _post(client, "{not json").status_code == 400
    assert _post(client, _text(_template(format_version=7))).status_code == 400
    assert _post(client, b"\xff\xfe{}").status_code == 400
    assert _post(client, _text(_template(name="Other")), content_type="text/plain").status_code == 415
    assert _post(client, " " * (1024 * 1024 + 1)).status_code == 413
    assert sorted(p.name for p in (tmp_path / "_templates").glob("*.json")) == ["imported.json"]


def test_websocket_import_parity_and_broadcasts(client):
    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        response = _rpc(ws1, "template.import", {"text": _text(_template())})
        assert response["result"]["id"] == "user:imported"
        assert json.loads(ws2.receive_text()) == {"event": "templates_changed"}

        # Errors: -32602, and no broadcast.
        assert _rpc(ws1, "template.import", {"text": "{bad"}, 2)["error"]["code"] == -32602
        assert _rpc(ws1, "template.import", {"text": _text(_template(name="IMPORTED"))}, 3)["error"]["code"] == -32602
        assert _rpc(ws1, "template.import", {}, 4)["error"]["code"] == -32602
        assert _post(client, "{bad").status_code == 400

        # The next event ws2 sees is the one from this successful REST import.
        assert _post(client, _text(_template(name="Second"))).status_code == 200
        assert json.loads(ws1.receive_text()) == {"event": "templates_changed"}
        assert json.loads(ws2.receive_text()) == {"event": "templates_changed"}
