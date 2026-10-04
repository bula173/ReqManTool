"""Edge cases for importing a template file (File ▸ Load template ▸ Import):
size limits (UTF-8 bytes over REST and in the service), content types,
WebSocket parameter validation, name and file-name collisions, the written
file's format, REST/WebSocket parity, and the CSV import after the shared
``_media_type`` / ``_read_limited_body`` refactor."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import functools
import importlib
import json

import pytest
from fastapi.testclient import TestClient

from reqman.csv_import import MAX_CSV_IMPORT_BYTES
from reqman.service import ConflictError, ProjectService
from reqman.store import ProjectStore
from reqman.templates import MAX_TEMPLATE_IMPORT_BYTES, MAX_TEMPLATE_JSON_DEPTH

TOO_LARGE = "The template file is too large: the limit is 1 MiB"
# JSON text (plain ASCII) whose description decodes to a lone surrogate.
SURROGATE_TEXT = '{"format_version":1,"name":"Sur","description":"x\\ud800y","modules":[]}'
SURROGATE_ERROR = (
    "The template file is invalid: contains a character that cannot be encoded as UTF-8 "
    "(unpaired surrogate \\ud800)"
)
DEEP_TEXT = "[" * 500000 + "]" * 500000
DEEP_REASON = "nested too deeply (more than 100 levels)"
DEEP_ERROR = f"The template file is invalid: {DEEP_REASON}"
# Valid JSON the parser accepts, one level deeper than a template may nest.
OVER_DEPTH_TEXT = (
    '{"format_version":1,"name":"Over depth","junk":'
    + "[" * (MAX_TEMPLATE_JSON_DEPTH - 1) + "{}" + "]" * (MAX_TEMPLATE_JSON_DEPTH - 1) + "}"
)
WRONG_TYPE = "The template file must be sent with Content-Type: application/json"


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
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _padded(size: int, unit: str = "x", **overrides) -> str:
    """A valid template whose description is padded with ``unit`` so that the
    text is ``size`` characters (single-byte ``unit``) or about ``size`` bytes."""
    base = _text(_template(description="", **overrides))
    per_unit = len(unit.encode("utf-8"))
    count = (size - len(base.encode("utf-8"))) // per_unit
    return _text(_template(description=unit * count, **overrides))


def _files(directory: Path) -> dict[str, bytes]:
    if not directory.is_dir():
        return {}
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir()) if p.is_file()}


@pytest.fixture
def service(tmp_path):
    return ProjectService(ProjectStore(tmp_path / "data"))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REQMAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(tmp_path / "config.json"))
    import reqman.app as app_module

    importlib.reload(app_module)
    return TestClient(app_module.app)


def _post(client, body, content_type="application/json"):
    content = body.encode("utf-8") if isinstance(body, str) else body
    headers = {} if content_type is None else {"Content-Type": content_type}
    return client.post("/templates/import", content=content, headers=headers)


def _rpc(ws, method, params, request_id=1):
    ws.send_text(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}))
    return json.loads(ws.receive_text())


def _assert_next_event_is_from_new_project(client, ws, name="Marker"):
    """Failed imports broadcast nothing: the next event is from this REST call."""
    project_id = client.post("/projects", json={"name": name}).json()["id"]
    assert json.loads(ws.receive_text()) == {"event": "changed", "project_id": project_id}


# --- service ------------------------------------------------------------------------


def test_service_size_limit_counts_utf8_bytes_like_rest(service):
    # The service compares the UTF-8 byte length with MAX_TEMPLATE_IMPORT_BYTES,
    # exactly like REST caps the request body, so WebSocket and REST agree: a
    # multibyte text under the character limit but over the byte limit is
    # rejected by both.
    text = _padded(MAX_TEMPLATE_IMPORT_BYTES + 2, unit="ż")
    assert len(text) <= MAX_TEMPLATE_IMPORT_BYTES < len(text.encode("utf-8"))
    with pytest.raises(ValueError, match="too large") as info:
        service.import_template(text)
    assert str(info.value) == TOO_LARGE

    at_limit = _padded(MAX_TEMPLATE_IMPORT_BYTES, name="At limit")
    assert len(at_limit.encode("utf-8")) == MAX_TEMPLATE_IMPORT_BYTES
    assert service.import_template(at_limit)["id"] == "user:at-limit"
    with pytest.raises(ValueError, match="too large"):
        service.import_template(_padded(MAX_TEMPLATE_IMPORT_BYTES + 1, name="Over"))
    # A leading BOM counts (3 bytes), as it does in a REST body.
    bom_over = "\ufeff" + _padded(MAX_TEMPLATE_IMPORT_BYTES - 2, name="Bom over")
    assert len(bom_over.encode("utf-8")) == MAX_TEMPLATE_IMPORT_BYTES + 1
    with pytest.raises(ValueError, match="too large"):
        service.import_template(bom_over)
    # A lone surrogate is counted, not crashed on (it is rejected later).
    with pytest.raises(ValueError, match="too large"):
        service.import_template("\ud800" * (MAX_TEMPLATE_IMPORT_BYTES // 3 + 1))
    assert sorted(_files(service.store.root / "_templates")) == ["at-limit.json"]


def test_import_writes_the_standard_template_form(service):
    source = {
        "format_version": 1,
        "id": "../../outside",
        "name": "  Spaced Name  ",
        "description": 'Zażółć "quoted" \\ back\nslash 🚀 <tag> &amp;',
        "future": {"nested": True},
        "attributes": [
            {"id": "status", "name": "Status", "type": "enum", "choices": ["Ä", "B"], "default": "Ä", "color": "red"},
            {"id": "note", "name": "Note", "system": False},
        ],
        "modules": [
            {"key": "a", "name": " Alpha ", "prefix": "A", "extra": 1},
            {"key": "b", "name": "Beta", "prefix": "B", "description": None},
        ],
        "link_configurations": [
            {"key": "ab", "name": "Refines", "source_module": "a", "target_module": "b", "x": [1]},
        ],
        "views": [{"name": " Main ", "attribute_ids": ["object_text", "status", "link:ab"], "sort": "id"}],
    }
    # BOM, CRLF line endings and compact JSON: none of it survives.
    text = "﻿" + json.dumps(source, ensure_ascii=False, separators=(",", ":")).replace(",", ",\r\n")

    summary = service.import_template(text)

    assert summary == {
        "id": "user:spaced-name",
        "source": "user",
        "name": "Spaced Name",
        "description": source["description"],
        "modules": [{"key": "a", "name": "Alpha", "prefix": "A"}, {"key": "b", "name": "Beta", "prefix": "B"}],
        "error": None,
    }
    templates_dir = service.store.root / "_templates"
    assert sorted(_files(templates_dir)) == ["spaced-name.json"]
    assert not (service.store.root.parent / "outside.json").exists()

    expected = {
        "format_version": 1,
        "id": "spaced-name",
        "name": "Spaced Name",
        "description": source["description"],
        "attributes": [
            {"id": "status", "name": "Status", "type": "enum", "required": False, "default": "Ä", "choices": ["Ä", "B"]},
            {"id": "note", "name": "Note", "type": "string", "required": False, "default": None, "choices": None},
        ],
        "modules": [
            {"key": "a", "name": "Alpha", "prefix": "A", "description": ""},
            {"key": "b", "name": "Beta", "prefix": "B", "description": ""},
        ],
        "link_configurations": [{"key": "ab", "name": "Refines", "source_module": "a", "target_module": "b"}],
        "views": [{"name": "Main", "attribute_ids": ["object_text", "status", "link:ab"]}],
    }
    raw = (templates_dir / "spaced-name.json").read_bytes()
    assert raw == (json.dumps(expected, indent=2) + "\n").encode("utf-8")
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw

    # Reloaded through a fresh store, the template is listed unchanged.
    fresh = ProjectService(ProjectStore(service.store.root))
    assert next(t for t in fresh.list_templates() if t["id"] == "user:spaced-name") == summary


def test_imported_template_creates_project_keeping_module_order_links_and_views(service):
    data = _template(
        name="Ordered",
        modules=[
            {"key": "z", "name": "Zeta", "prefix": "ZT"},
            {"key": "a", "name": "Alpha", "prefix": "AL"},
            {"key": "m", "name": "Mid", "prefix": "MD"},
        ],
        link_configurations=[{"key": "za", "name": "Refines", "source_module": "z", "target_module": "a"}],
        views=[{"name": "Main", "attribute_ids": ["object_text", "status", "link:za"]}],
    )
    service.import_template(_text(data))

    fresh = ProjectService(ProjectStore(service.store.root))
    project = fresh.create_project("From import", template_id="user:ordered")
    modules = fresh.list_modules(project.id)
    assert [(m.name, m.prefix) for m in modules] == [("Zeta", "ZT"), ("Alpha", "AL"), ("Mid", "MD")]

    reloaded = fresh.store.load_project(project.id)
    (config,) = reloaded.link_configurations
    assert config.name == "Refines"
    by_name = {m.name: m.id for m in modules}
    assert (config.source_module_id, config.target_module_id) == (by_name["Zeta"], by_name["Alpha"])
    main = next(v for v in reloaded.views if v.name == "Main")
    assert main.attribute_ids == ["object_text", "status", config.id]
    status = next(a for a in reloaded.attributes if a.id == "status")
    assert status.type == "enum" and status.choices == ["A", "B"]
    req = fresh.add_requirement(project.id, by_name["Alpha"], {"object_text": "x"})
    assert req.id == "AL-0001" and req.attributes["status"] == "A"


@pytest.mark.parametrize("name", [" rbc_template ", "RBC_TEMPLATE", "\tRbc_Template\n"])
def test_import_name_clashing_with_builtin_after_trim_and_casefold_is_a_conflict(service, name):
    with pytest.raises(ConflictError, match=r"builtin:rbc_template"):
        service.import_template(_text(_template(name=name)))
    assert _files(service.store.root / "_templates") == {}


def test_import_name_clashing_with_an_invalid_templates_listed_name_is_a_conflict(service):
    templates_dir = service.store.root / "_templates"
    templates_dir.mkdir(parents=True)
    (templates_dir / "legacy.json").write_text(_text({"format_version": 99, "name": "Legacy Spec"}))
    (templates_dir / "garbage.json").write_text("{oops")
    before = _files(templates_dir)
    listed = {t["id"]: t for t in service.list_templates()}
    assert listed["user:legacy"]["name"] == "Legacy Spec" and listed["user:legacy"]["error"]
    assert listed["user:garbage"]["name"] == "garbage" and listed["user:garbage"]["error"]

    with pytest.raises(ConflictError, match=r"user:legacy"):
        service.import_template(_text(_template(name="legacy spec")))
    with pytest.raises(ConflictError, match=r"user:garbage"):
        service.import_template(_text(_template(name="GARBAGE")))
    assert _files(templates_dir) == before


def test_import_suffixes_past_every_taken_file_and_leaves_them_untouched(service):
    templates_dir = service.store.root / "_templates"
    templates_dir.mkdir(parents=True)
    (templates_dir / "imported.json").write_text(_text(_template(name="Something else")))
    (templates_dir / "imported-2.json").write_text("{not even json")
    before = _files(templates_dir)

    summary = service.import_template(_text(_template()))

    assert summary["id"] == "user:imported-3"
    after = _files(templates_dir)
    assert {k: after[k] for k in before} == before
    assert json.loads(after["imported-3.json"])["id"] == "imported-3"


def test_import_names_without_slug_characters_use_the_fallback_stem(service):
    assert service.import_template(_text(_template(name="???")))["id"] == "user:template"
    assert service.import_template(_text(_template(name="!!!")))["id"] == "user:template-2"
    assert sorted(_files(service.store.root / "_templates")) == ["template-2.json", "template.json"]


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"format_version": 0}, "Unsupported format_version 0"),
        ({"format_version": "1"}, "must be the integer"),
        ({"format_version": True}, "must be the integer"),
        ({"format_version": None}, "must be the integer"),
        ({"modules": [{"key": "a", "name": "A", "prefix": "A"}, {"key": "a", "name": "B", "prefix": "B"}]},
         "duplicate module key"),
        ({"link_configurations": [{"key": "x", "name": "X", "source_module": "a", "target_module": "nope"}]},
         "not a module key"),
    ],
)
def test_import_rejects_more_invalid_files_and_writes_nothing(service, overrides, message):
    with pytest.raises(ValueError, match=message) as info:
        service.import_template(_text(_template(**overrides)))
    assert not isinstance(info.value, ConflictError)
    assert str(info.value).startswith("The template file is invalid: ")
    assert _files(service.store.root / "_templates") == {}


# --- REST ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content_type", ["application/json; charset=utf-8", "Application/JSON", " application/json ;charset=UTF-8"]
)
def test_rest_accepts_application_json_with_parameters(client, content_type):
    resp = _post(client, _text(_template()), content_type=content_type)
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "user:imported"


@pytest.mark.parametrize(
    "content_type",
    [None, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x", "text/csv",
     "application/jsonx", "application/ld+json"],
)
def test_rest_requires_application_json(client, tmp_path, content_type):
    resp = _post(client, _text(_template()), content_type=content_type)
    assert resp.status_code == 415
    assert resp.json()["detail"] == WRONG_TYPE
    assert _files(tmp_path / "_templates") == {}


def test_rest_size_limit_counts_bytes_with_and_without_content_length(client, tmp_path):
    over = _padded(MAX_TEMPLATE_IMPORT_BYTES + 1, name="Over")
    assert len(over.encode("utf-8")) == MAX_TEMPLATE_IMPORT_BYTES + 1
    # Multibyte: fewer characters than the limit, more bytes than the limit.
    multibyte = _padded(MAX_TEMPLATE_IMPORT_BYTES + 2, unit="ż", name="Multibyte")
    assert len(multibyte) <= MAX_TEMPLATE_IMPORT_BYTES < len(multibyte.encode("utf-8"))

    def chunked(body: bytes):
        def chunks():
            yield body[: len(body) // 2]
            yield body[len(body) // 2:]
        return chunks()

    with client.websocket_connect("/ws") as ws:
        for body in (over.encode("utf-8"), multibyte.encode("utf-8")):
            resp = _post(client, body)
            assert (resp.status_code, resp.json()["detail"]) == (413, TOO_LARGE)
            # No Content-Length header (chunked): still capped while reading.
            resp = _post(client, chunked(body))
            assert (resp.status_code, resp.json()["detail"]) == (413, TOO_LARGE)
        assert _files(tmp_path / "_templates") == {}
        _assert_next_event_is_from_new_project(client, ws)

        at_limit = _padded(MAX_TEMPLATE_IMPORT_BYTES, name="At limit").encode("utf-8")
        assert len(at_limit) == MAX_TEMPLATE_IMPORT_BYTES
        resp = _post(client, chunked(at_limit))
        assert resp.status_code == 200, resp.text
        assert json.loads(ws.receive_text()) == {"event": "templates_changed"}
    assert sorted(_files(tmp_path / "_templates")) == ["at-limit.json"]


def test_rest_body_decoding_errors(client, tmp_path):
    resp = _post(client, b"")
    assert resp.status_code == 400 and resp.json()["detail"].startswith("The template file is invalid: not valid JSON")
    resp = _post(client, _text(_template(name="Zażółć")).encode("cp1250"))
    assert (resp.status_code, resp.json()["detail"]) == (400, "The template file must be UTF-8 encoded")
    resp = _post(client, _text(_template()).encode("utf-16"))
    assert (resp.status_code, resp.json()["detail"]) == (400, "The template file must be UTF-8 encoded")
    assert _files(tmp_path / "_templates") == {}


def test_rest_success_response_matches_the_template_listing(client):
    resp = _post(client, "﻿" + _text(_template(name="Zażółć 🚀", description="")))
    assert resp.status_code == 200, resp.text
    listed = next(t for t in client.get("/templates").json() if t["id"] == resp.json()["id"])
    assert resp.json() == listed
    assert listed["name"] == "Zażółć 🚀" and listed["description"] == "" and listed["error"] is None


def test_rest_builtin_name_with_whitespace_is_409_and_not_broadcast(client, tmp_path):
    with client.websocket_connect("/ws") as ws:
        resp = _post(client, _text(_template(name=" rbc_template ")))
        assert resp.status_code == 409
        assert "builtin:rbc_template" in resp.json()["detail"]
        resp = _post(client, _text(_template(format_version=2)))
        assert resp.status_code == 400 and "newer" in resp.json()["detail"]
        _assert_next_event_is_from_new_project(client, ws)
    assert _files(tmp_path / "_templates") == {}


def test_rest_imported_template_creates_project_with_module_order(client):
    modules = [{"key": "z", "name": "Zeta", "prefix": "ZT"}, {"key": "a", "name": "Alpha", "prefix": "AL"}]
    links = [{"key": "za", "name": "Refines", "source_module": "z", "target_module": "a"}]
    views = [{"name": "Main", "attribute_ids": ["object_text", "link:za"]}]
    template_id = _post(client, _text(_template(modules=modules, link_configurations=links, views=views))).json()["id"]

    resp = client.post("/projects", json={"name": "From import", "template_id": template_id})
    assert resp.status_code == 200, resp.text
    listed = client.get(f"/projects/{resp.json()['id']}/modules").json()
    assert [m["name"] for m in listed] == ["Zeta", "Alpha"]


# --- WebSocket ----------------------------------------------------------------------


@pytest.mark.parametrize("text", [5, 1.5, True, None, ["{}"], {"name": "x"}])
def test_ws_non_string_text_is_rejected_without_broadcast_or_write(client, tmp_path, text):
    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        response = _rpc(ws1, "template.import", {"text": text})
        assert response["error"]["code"] == -32602
        assert response["error"]["message"] == "The template file content must be a string"
        assert "result" not in response
        _assert_next_event_is_from_new_project(client, ws2)
    assert _files(tmp_path / "_templates") == {}


def test_ws_null_params_are_rejected(client, tmp_path):
    with client.websocket_connect("/ws") as ws:
        assert _rpc(ws, "template.import", None)["error"]["code"] == -32602
    assert _files(tmp_path / "_templates") == {}


# --- unencodable text and deep nesting ---------------------------------------------


def test_service_rejects_lone_surrogates_and_writes_nothing(service):
    assert "\\ud800" in SURROGATE_TEXT and SURROGATE_TEXT.isascii()
    with pytest.raises(ValueError) as info:
        service.import_template(SURROGATE_TEXT)
    assert str(info.value) == SURROGATE_ERROR
    # A raw lone surrogate character (possible over WebSocket) and one in a key.
    for text in (
        _text(_template(name="Raw")).replace("From a file", "x\ud800"),
        '{"format_version":1,"name":"Key","modules":[],"\\udc00":1}',
    ):
        with pytest.raises(ValueError, match="cannot be encoded as UTF-8"):
            service.import_template(text)
    assert _files(service.store.root / "_templates") == {}


def test_service_rejects_deeply_nested_json_and_writes_nothing(service):
    with pytest.raises(ValueError) as info:
        service.import_template(DEEP_TEXT)
    assert str(info.value) == DEEP_ERROR
    assert _files(service.store.root / "_templates") == {}


def test_rest_rejects_lone_surrogate_without_write_or_broadcast(client, tmp_path):
    with client.websocket_connect("/ws") as ws:
        resp = _post(client, SURROGATE_TEXT)
        assert (resp.status_code, resp.json()["detail"]) == (400, SURROGATE_ERROR)
        resp = _post(client, DEEP_TEXT)
        assert (resp.status_code, resp.json()["detail"]) == (400, DEEP_ERROR)
        _assert_next_event_is_from_new_project(client, ws)
    assert _files(tmp_path / "_templates") == {}
    assert client.get("/templates").status_code == 200


def test_ws_rejects_lone_surrogate_without_write_or_broadcast(client, tmp_path):
    raw_surrogate = _text(_template(name="Raw")).replace("From a file", "x\ud800")
    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        for request_id, (text, message) in enumerate(
            [(SURROGATE_TEXT, SURROGATE_ERROR), (DEEP_TEXT, DEEP_ERROR), (raw_surrogate, None)], start=1
        ):
            error = _rpc(ws1, "template.import", {"text": text}, request_id)["error"]
            assert error["code"] == -32602
            assert error["message"] == message if message else "cannot be encoded as UTF-8" in error["message"]
        _assert_next_event_is_from_new_project(client, ws2)
    assert _files(tmp_path / "_templates") == {}
    assert client.get("/templates").status_code == 200


def test_unencodable_or_deep_template_files_on_disk_are_listed_as_invalid(client, tmp_path):
    templates_dir = tmp_path / "_templates"
    templates_dir.mkdir()
    (templates_dir / "sur.json").write_text(SURROGATE_TEXT, encoding="utf-8")
    (templates_dir / "surname.json").write_text(
        '{"format_version":1,"name":"N\\ud800","modules":[]}', encoding="utf-8"
    )
    (templates_dir / "deep.json").write_text(DEEP_TEXT, encoding="utf-8")
    (templates_dir / "good.json").write_text(_text(_template(name="Good")), encoding="utf-8")

    resp = client.get("/templates")

    assert resp.status_code == 200, resp.text
    listed = {t["id"]: t for t in resp.json()}
    assert listed["user:good"]["error"] is None
    assert listed["user:sur"]["name"] == "Sur"
    assert listed["user:sur"]["error"] == SURROGATE_ERROR.removeprefix("The template file is invalid: ")
    # An unencodable name falls back to the file name stem.
    assert listed["user:surname"]["name"] == "surname"
    assert "cannot be encoded as UTF-8" in listed["user:surname"]["error"]
    assert listed["user:deep"]["name"] == "deep"
    assert listed["user:deep"]["error"] == DEEP_REASON
    for template_id in ("user:sur", "user:surname", "user:deep"):
        assert listed[template_id]["modules"] == []
        resp = client.post("/projects", json={"name": f"From {template_id}", "template_id": template_id})
        assert resp.status_code == 400, resp.text


def test_nesting_up_to_the_depth_limit_is_accepted_and_one_deeper_is_rejected(service):
    at_limit = OVER_DEPTH_TEXT.replace("[", "", 1).replace("]", "", 1).replace("Over depth", "At depth")
    assert service.import_template(at_limit)["id"] == "user:at-depth"
    # Unknown keys are not kept, however deep.
    assert "junk" not in json.loads((service.store.root / "_templates" / "at-depth.json").read_text())
    with pytest.raises(ValueError) as info:
        service.import_template(OVER_DEPTH_TEXT)
    assert str(info.value) == DEEP_ERROR
    assert sorted(_files(service.store.root / "_templates")) == ["at-depth.json"]


def test_rest_and_ws_reject_over_depth_json_without_write_or_broadcast(client, tmp_path):
    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        resp = _post(client, OVER_DEPTH_TEXT)
        assert (resp.status_code, resp.json()["detail"]) == (400, DEEP_ERROR)
        error = _rpc(ws1, "template.import", {"text": OVER_DEPTH_TEXT})["error"]
        assert (error["code"], error["message"]) == (-32602, DEEP_ERROR)
        _assert_next_event_is_from_new_project(client, ws2)
    assert _files(tmp_path / "_templates") == {}
    (tmp_path / "_templates").mkdir()
    (tmp_path / "_templates" / "over.json").write_text(OVER_DEPTH_TEXT, encoding="utf-8")
    resp = client.get("/templates")
    assert resp.status_code == 200, resp.text
    over = next(t for t in resp.json() if t["id"] == "user:over")
    assert (over["name"], over["error"]) == ("Over depth", DEEP_REASON)


@functools.lru_cache(maxsize=None)
def _deepest_loadable_nesting() -> int:
    """The deepest ``[[…]]`` nesting ``json.loads`` accepts here, found by bisection.

    Since Python 3.12 the JSON parser is limited by the C stack, not by
    ``sys.getrecursionlimit()`` (~116k levels on a main thread), so the
    limit cannot be lowered for a test; it is measured instead. Capped by
    what fits the 1 MiB import limit.
    """

    def loads(depth: int) -> bool:
        try:
            json.loads("[" * depth + "]" * depth)
        except RecursionError:
            return False
        return True

    low, high = 1, MAX_TEMPLATE_IMPORT_BYTES // 2 - 1000
    if loads(high):
        return high
    while high - low > 1:
        middle = (low + high) // 2
        if loads(middle):
            low = middle
        else:
            high = middle
    return low


def _nested(depth: int) -> str:
    return "[" * depth + "]" * depth


def _near_limit_texts() -> dict[str, str]:
    """Templates ``json.loads`` accepts, with one value nested just below its limit
    (deeper than ``json.dumps`` or ``repr`` can go), in places later steps format or
    serialise: an unknown key, ``format_version``, an attribute's type and default,
    and a link configuration's module key."""
    depth = _deepest_loadable_nesting() * 99 // 100
    deep = _nested(depth)
    module = '{"key":"a","name":"A","prefix":"A"}'
    texts = {
        "unknown key": '{"format_version":1,"name":"Deep","junk":' + deep + "}",
        "format_version": '{"name":"Deep","format_version":' + deep + "}",
        "attribute type": '{"format_version":1,"name":"Deep","attributes":[{"id":"t","name":"T","type":'
        + deep + "}]}",
        "attribute default": '{"format_version":1,"name":"Deep","attributes":[{"id":"t","name":"T","default":'
        + deep + "}]}",
        "link module": '{"format_version":1,"name":"Deep","modules":[' + module
        + '],"link_configurations":[{"key":"l","name":"L","source_module":' + deep + ',"target_module":"a"}]}',
    }
    for text in texts.values():
        assert len(text.encode("utf-8")) <= MAX_TEMPLATE_IMPORT_BYTES
        json.loads(text)  # accepted by the parser: the later steps are what is tested
    return texts


@pytest.mark.parametrize("where", ["unknown key", "format_version", "attribute type", "attribute default", "link module"])
def test_service_rejects_json_nested_just_below_the_parser_limit(service, where):
    # Regression: such text passed json.loads, then json.dumps (the UTF-8
    # check) or an error message's repr raised RecursionError → 500 / -32603.
    with pytest.raises(ValueError) as info:
        service.import_template(_near_limit_texts()[where])
    assert str(info.value) == DEEP_ERROR
    # Rejected by the MAX_TEMPLATE_JSON_DEPTH check, not by a json.loads
    # RecursionError (that path chains it: ValueError from RecursionError).
    cause = info.value.__cause__
    assert isinstance(cause, ValueError) and cause.__cause__ is None
    assert _files(service.store.root / "_templates") == {}


def test_template_file_nested_just_below_the_parser_limit_is_listed_as_invalid(service):
    templates_dir = service.store.root / "_templates"
    templates_dir.mkdir(parents=True)
    for index, text in enumerate(_near_limit_texts().values()):
        (templates_dir / f"deep-{index}.json").write_text(text, encoding="utf-8")
    (templates_dir / "good.json").write_text(_text(_template(name="Good")), encoding="utf-8")

    listed = {t["id"]: t for t in service.list_templates()}

    assert listed["user:good"]["error"] is None
    for index in range(5):
        entry = listed[f"user:deep-{index}"]
        assert (entry["name"], entry["error"], entry["modules"]) == ("Deep", DEEP_REASON, [])
        with pytest.raises(ValueError, match="nested too deeply"):
            service.create_project(f"From deep {index}", template_id=f"user:deep-{index}")


# --- REST / WebSocket parity --------------------------------------------------------


@pytest.mark.parametrize(
    "text, status",
    [
        ("{bad", 400),
        ("", 400),
        ("[]", 400),
        (_text(_template(format_version=2)), 400),
        (_text(_template(name="  ")), 400),
        (_text(_template(views=[{"name": "V", "attribute_ids": ["missing"]}])), 400),
        (_text(_template(name=" RBC_Template ")), 409),
        (" " * (MAX_TEMPLATE_IMPORT_BYTES + 1), None),  # REST 413 vs. service ValueError
        (_padded(MAX_TEMPLATE_IMPORT_BYTES + 2, unit="ż"), None),  # multibyte: bytes in both
        (SURROGATE_TEXT, 400),
        (DEEP_TEXT, 400),
        (OVER_DEPTH_TEXT, 400),
    ],
    ids=[
        "bad-json",
        "empty",
        "not-object",
        "unsupported-version",
        "blank-name",
        "unknown-view-attribute",
        "builtin-name-clash",
        "too-large-ascii",
        "too-large-multibyte",
        "lone-surrogate",
        "too-deep-for-parser",
        "over-depth-limit",
    ],
)
def test_rest_and_ws_report_the_same_error(client, tmp_path, text, status):
    rest = _post(client, text)
    with client.websocket_connect("/ws") as ws:
        ws_error = _rpc(ws, "template.import", {"text": text})["error"]
    assert ws_error["code"] == -32602
    assert rest.status_code == (status or 413)
    assert rest.json()["detail"] == ws_error["message"]
    assert _files(tmp_path / "_templates") == {}


def test_rest_and_ws_write_equivalent_files(client, tmp_path):
    assert _post(client, _text(_template(name="Via Rest"))).status_code == 200
    with client.websocket_connect("/ws") as ws:
        assert _rpc(ws, "template.import", {"text": _text(_template(name="Via Ws"))})["result"]["id"] == "user:via-ws"
    files = _files(tmp_path / "_templates")
    rest, ws_data = json.loads(files["via-rest.json"]), json.loads(files["via-ws.json"])
    assert {k: v for k, v in rest.items() if k not in ("id", "name")} == {
        k: v for k, v in ws_data.items() if k not in ("id", "name")
    }
    assert list(rest) == list(ws_data)


# --- CSV import regression (shared body helpers) ------------------------------------


def test_csv_import_size_and_content_type_messages_are_unchanged(client):
    project_id = client.post("/projects", json={"name": "Project One"}).json()["id"]
    module_id = client.post(f"/projects/{project_id}/modules", json={"name": "Module One", "prefix": "REQ"}).json()["id"]
    url = f"/projects/{project_id}/modules/{module_id}/import/csv"
    expected = f"The CSV file is too large: the limit is {MAX_CSV_IMPORT_BYTES // (1024 * 1024)} MiB"
    body = b"ObjectText\n" + b"x" * MAX_CSV_IMPORT_BYTES

    resp = client.post(url, content=body, headers={"Content-Type": "text/csv"})
    assert (resp.status_code, resp.json()["detail"]) == (413, expected)

    def chunks():
        yield body

    resp = client.post(url, content=chunks(), headers={"Content-Type": "text/csv"})
    assert (resp.status_code, resp.json()["detail"]) == (413, expected)

    resp = client.post(url, content=b"ObjectText\nx\n", headers={"Content-Type": "application/json"})
    assert (resp.status_code, resp.json()["detail"]) == (415, "The CSV file must be sent with Content-Type: text/csv")

    # A template-sized (not CSV-sized) body is no problem for CSV, and vice versa
    # the CSV endpoint still strips a UTF-8 BOM.
    resp = client.post(url, content="﻿ObjectText\nx\n".encode("utf-8"), headers={"Content-Type": "text/csv"})
    assert resp.status_code == 200, resp.text
    assert client.get(f"/projects/{project_id}/modules/{module_id}/requirements").json()[0]["attributes"] == {
        "object_text": "x"
    }
