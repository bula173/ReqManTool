"""Importing a ReqIF document into a project (``reqif_import.py``).

The inverse of the ReqIF export: what a ReqManTool export writes must read
back with its values, formatting, hierarchy and modules intact, and a
foreign tool's document must import without crashing the project.
"""

import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from reqman.git_repo import GitHistory, GitSettings
from reqman.reqif_import import (
    import_reqif_from_file,
    import_reqif_from_string,
    xhtml_to_richtext,
)
from reqman.richtext import RICH_TEXT_PREFIX as P
from reqman.richtext import is_rich, to_plain
from reqman.service import ProjectService
from reqman.store import ProjectStore


def rich(body: str) -> str:
    return P + body + "</div>"


@pytest.fixture
def service(tmp_path):
    return ProjectService(ProjectStore(tmp_path))


@pytest.fixture
def git_service(tmp_path, monkeypatch):
    """A service with git history on (the ``service`` fixture runs with it off)."""
    monkeypatch.setenv("REQMAN_GIT", "on")
    monkeypatch.setenv("REQMAN_GIT_SQUASH_MINUTES", "0")
    return ProjectService(
        ProjectStore(tmp_path), history=GitHistory(GitSettings.from_env(os.environ))
    )


@pytest.fixture
def module(service):
    project = service.create_project("P")
    service.add_attribute_definition(project.id, "note", "Note", "text")
    service.add_attribute_definition(project.id, "title", "Title", "string")
    service.add_attribute_definition(project.id, "rank", "Rank", "integer")
    service.add_attribute_definition(project.id, "done", "Done", "boolean")
    service.add_attribute_definition(project.id, "due", "Due", "date")
    return project.id, service.create_module(project.id, "M", "M").id


def _export(service, project_id):
    return service.export_reqif(project_id)


# --- the module on its own (no service) ----------------------------------------


def test_import_reads_project_header():
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<REQ-IF xmlns="http://www.omg.org/spec/ReqIF/20110401/reqif.xsd">'
        '<THE-HEADER><REQ-IF-HEADER IDENTIFIER="p1"><TITLE>My project</TITLE></REQ-IF-HEADER></THE-HEADER>'
        "<CORE-CONTENT><REQ-IF-CONTENT><DATATYPES/><SPEC-TYPES/><SPEC-OBJECTS/><SPECIFICATIONS/>"
        "</REQ-IF-CONTENT></CORE-CONTENT></REQ-IF>"
    )
    project = import_reqif_from_string(xml)
    assert (project.id, project.name) == ("p1", "My project")
    assert project.description == ""
    assert project.attributes == []
    assert project.modules == []


def test_import_reads_the_project_description_from_the_spec_object_type(service):
    """``reqif_export`` writes the description as the SPEC-OBJECT-TYPE's DESC."""
    project = service.create_project("P", "The project description")
    imported = import_reqif_from_string(service.export_reqif(project.id))
    assert imported.description == "The project description"
    assert imported.name == "P"


def test_import_rejects_malformed_input():
    with pytest.raises(ValueError):
        import_reqif_from_string("not xml at all")
    with pytest.raises(ValueError):
        import_reqif_from_string("")
    with pytest.raises(ValueError):
        import_reqif_from_string("<REQ-IF><unclosed>")


def test_import_from_file(tmp_path):
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<REQ-IF xmlns="http://www.omg.org/spec/ReqIF/20110401/reqif.xsd">'
        "<THE-HEADER><REQ-IF-HEADER IDENTIFIER=\"p1\"><TITLE>From file</TITLE></REQ-IF-HEADER></THE-HEADER>"
        "<CORE-CONTENT><REQ-IF-CONTENT><DATATYPES/><SPEC-TYPES/><SPEC-OBJECTS/><SPECIFICATIONS/>"
        "</REQ-IF-CONTENT></CORE-CONTENT></REQ-IF>"
    )
    path = tmp_path / "in.reqif"
    path.write_text(xml, encoding="utf-8")
    assert import_reqif_from_file(path).name == "From file"
    with pytest.raises(ValueError):
        import_reqif_from_file(tmp_path / "missing.reqif")


# --- XHTML -> rich text ------------------------------------------------------------


def test_xhtml_div_keeps_its_formatting():
    value = xhtml_to_richtext(
        "<xhtml:div><xhtml:p>a <xhtml:strong>b</xhtml:em></xhtml:p><xhtml:ul><xhtml:li>one</xhtml:li></xhtml:ul></xhtml:div>"
    )
    assert is_rich(value)
    assert to_plain(value) == "a b\n- one"


def test_xhtml_plain_text_stays_plain():
    # Unformatted paragraphs come back as plain text, as a value never
    # formatted in the editor is: two lines, no markup.
    assert xhtml_to_richtext("<xhtml:div><xhtml:p>one</xhtml:p><xhtml:p>two</xhtml:p></xhtml:div>") == "one\ntwo"


def test_xhtml_visible_spacing_decodes_back_to_spaces():
    # The export writes leading spaces as &#160; so XHTML does not collapse them.
    value = xhtml_to_richtext("<xhtml:div>  a  b  </xhtml:div>")
    assert value == "  a  b  "


def test_xhtml_entities_stay_escaped_in_the_source_but_read_as_text():
    assert xhtml_to_richtext("<xhtml:div>a &lt;b&gt; &amp; c</xhtml:div>") == "a <b> & c"


def test_xhtml_non_div_value_is_read_as_text():
    assert xhtml_to_richtext("just text") == "just text"


def test_xhtml_hostile_markup_is_sanitised_not_rejected():
    value = xhtml_to_richtext(
        '<xhtml:div><script>alert(1)</script><xhtml:p onclick="x()">safe</xhtml:p></xhtml:div>'
    )
    assert "alert" not in value and "onclick" not in value
    assert to_plain(value) == "safe"


# --- round trip through the service ------------------------------------------------


def test_export_import_round_trip_keeps_every_value_shape(service, module):
    project_id, module_id = module
    root = service.add_requirement(
        project_id,
        module_id,
        {
            "object_text": rich("<p>Formatted <strong>bold</strong> and <em>italic</em></p><ul><li>one</li></ul>"),
            "note": "plain note",
            "title": "T",
            "rank": 7,
            "done": True,
            "due": "2026-03-01",
        },
    )
    service.add_requirement(project_id, module_id, {"object_text": "child", "rank": 0, "done": False}, parent_id=root.id)

    imported = import_reqif_from_string(_export(service, project_id))
    attributes = {a.id: a.type for a in imported.attributes}
    assert attributes == {
        "object_text": "text",
        "note": "text",
        "title": "string",
        "rank": "integer",
        "done": "boolean",
        "due": "date",
    }
    (module1,) = imported.modules
    assert module1.name == "M"
    (first, second) = module1.requirements
    assert first.attributes["note"] == "plain note"
    assert first.attributes["rank"] == 7
    assert first.attributes["done"] is True
    assert first.attributes["due"] == "2026-03-01"
    assert is_rich(first.attributes["object_text"])
    assert to_plain(first.attributes["object_text"]) == "Formatted bold and italic\n- one"
    assert second.parent_id == first.id
    assert second.attributes["rank"] == 0
    assert second.attributes["done"] is False


def test_round_trip_is_stable(service, module):
    """Exporting the imported project writes the same values as the first export."""
    from reqman.reqif_import import import_reqif_from_string as read

    project_id, module_id = module
    service.add_requirement(
        project_id, module_id, {"object_text": rich('<p>a <strong>b</strong></p><ol><li>x</li></ol>'), "title": "T"}
    )
    first = read(_export(service, project_id)).modules[0].requirements[0].attributes

    target = service.create_project("Target")
    service.import_reqif(target.id, _export(service, project_id))
    imported_module = service.list_modules(target.id)[0]
    second = service.list_requirements(target.id, imported_module.id)[0].attributes
    for attribute_id, value in first.items():
        assert second[attribute_id] == value


def test_import_creates_a_module_per_specification(service, module):
    project_id, module_id = module
    service.add_requirement(project_id, module_id, {"object_text": "in M"})
    other = service.create_module(project_id, "Other", "O")
    service.add_requirement(project_id, other.id, {"object_text": "in Other"})

    target = service.create_project("Target")
    result = service.import_reqif(target.id, _export(service, project_id))
    assert result["modules_created"] == 2
    assert result["requirements_created"] == 2
    assert {m.name for m in service.list_modules(target.id)} == {"M", "Other"}


def test_import_fills_an_existing_module_instead_of_creating_a_second(service, module):
    project_id, module_id = module
    service.add_requirement(project_id, module_id, {"object_text": "imported"})

    target = service.create_project("Target")
    existing = service.create_module(target.id, "M", "EX")
    service.add_requirement(target.id, existing.id, {"object_text": "already here"})

    result = service.import_reqif(target.id, _export(service, project_id))
    assert result["modules_created"] == 0
    assert result["requirements_created"] == 1
    requirements = service.list_requirements(target.id, existing.id)
    assert [r.attributes["object_text"] for r in requirements] == ["already here", "imported"]


def test_import_allocates_ids_and_keeps_the_hierarchy(service, module):
    project_id, module_id = module
    root = service.add_requirement(project_id, module_id, {"object_text": "root"})
    service.add_requirement(project_id, module_id, {"object_text": "child"}, parent_id=root.id)

    target = service.create_project("Target")
    service.import_reqif(target.id, _export(service, project_id))
    imported_module = service.list_modules(target.id)[0]
    parent, child = service.list_requirements(target.id, imported_module.id)
    assert parent.parent_id is None
    assert child.parent_id == parent.id
    assert parent.id != child.id


def test_import_preserves_the_order_within_each_level(service, module):
    project_id, module_id = module
    for i in range(3):
        service.add_requirement(project_id, module_id, {"object_text": f"n{i}"})

    target = service.create_project("Target")
    service.import_reqif(target.id, _export(service, project_id))
    imported_module = service.list_modules(target.id)[0]
    assert [r.attributes["object_text"] for r in service.list_requirements(target.id, imported_module.id)] == [
        "n0",
        "n1",
        "n2",
    ]


def test_import_reuses_an_existing_attribute_by_id_or_name(service, module):
    """A matching attribute is reused, not duplicated; its values land in it."""
    project_id, module_id = module
    service.add_requirement(project_id, module_id, {"object_text": "x", "title": "T"})

    target = service.create_project("Target")
    # Same name, a different id: the import maps the file's "title" onto this one.
    service.add_attribute_definition(target.id, "own-title", "Title", "string")
    result = service.import_reqif(target.id, _export(service, project_id))
    # The file has 6 attributes; object_text exists in every project and
    # "Title" was reused, so the other 4 are added.
    assert result["attributes_created"] == 4
    attributes = {a.id: a.name for a in service.store.load_project(target.id).attributes}
    assert attributes["own-title"] == "Title"
    assert "title" not in attributes

    imported_module = service.list_modules(target.id)[0]
    (requirement,) = service.list_requirements(target.id, imported_module.id)
    assert requirement.attributes["own-title"] == "T"


def test_import_reuses_an_existing_attribute_by_id(service):
    """The same identifier is a match whatever the name."""
    project_id = service.create_project("P").id
    service.add_attribute_definition(project_id, "note", "Note", "text")
    module_id = service.create_module(project_id, "M", "M").id
    service.add_requirement(project_id, module_id, {"object_text": "x", "note": "N"})

    target = service.create_project("Target").id
    # Same id "note", renamed: the import keeps the existing definition.
    service.add_attribute_definition(target, "note", "Renamed note", "text")
    result = service.import_reqif(target, service.export_reqif(project_id))
    # The file has 2 attributes, and the target already has both (object_text
    # in every project, note by id), so nothing is added.
    assert result["attributes_created"] == 0
    names = {a.id: a.name for a in service.store.load_project(target).attributes}
    assert names["note"] == "Renamed note"

    imported_module = service.list_modules(target)[0]
    (requirement,) = service.list_requirements(target, imported_module.id)
    assert requirement.attributes["note"] == "N"


def test_import_adds_attributes_the_project_lacks(service, module):
    project_id, module_id = module
    service.add_requirement(project_id, module_id, {"object_text": "x", "rank": 3})

    target = service.create_project("Target")
    result = service.import_reqif(target.id, _export(service, project_id))
    # Every project has object_text, so the file's other 5 are added.
    assert result["attributes_created"] == 5
    names = {a.name for a in service.store.load_project(target.id).attributes}
    assert {"ObjectText", "Note", "Title", "Rank", "Due", "Done"} == names


def test_import_rejects_a_document_whose_attribute_type_conflicts(service, module):
    project_id, module_id = module
    service.add_requirement(project_id, module_id, {"object_text": "x", "rank": 1})

    target = service.create_project("Target")
    # Rank already exists in the target, as text rather than integer.
    service.add_attribute_definition(target.id, "rank", "Rank", "string")
    with pytest.raises(ValueError, match="integer"):
        service.import_reqif(target.id, _export(service, project_id))


def test_import_is_all_or_nothing(service, module):
    project_id, module_id = module
    service.add_requirement(project_id, module_id, {"object_text": "x"})

    target = service.create_project("Target")
    before = service.list_modules(target.id)
    with pytest.raises(ValueError):
        # A required attribute the target lacks: ObjectText is required in every
        # project, so dropping it from the file is impossible; use a malformed
        # document instead.
        service.import_reqif(target.id, "<REQ-IF>")
    assert service.list_modules(target.id) == before


def test_import_rejects_a_required_attribute_left_empty(service, module):
    project_id, module_id = module
    service.add_requirement(project_id, module_id, {"object_text": "x"})
    # The same document with the ObjectText value emptied: the required
    # attribute would be blank, which add requirement also rejects.
    xml = _export(service, project_id).replace("<xhtml:div>x</xhtml:div>", "<xhtml:div></xhtml:div>")

    target = service.create_project("Target")
    with pytest.raises(ValueError, match="ObjectText"):
        service.import_reqif(target.id, xml)
    assert service.list_modules(target.id) == []


def test_import_records_history(git_service):
    """The import is one auto-committed history entry, like every other mutation."""
    project_id = git_service.create_project("P").id
    module_id = git_service.create_module(project_id, "M", "M").id
    git_service.add_requirement(project_id, module_id, {"object_text": "x"})
    xml = git_service.export_reqif(project_id)

    target = git_service.create_project("Target").id
    before = len(git_service.git_log(target)["entries"])
    git_service.import_reqif(target, xml)
    after = git_service.git_log(target)
    assert after["available"] is True
    assert len(after["entries"]) == before + 1


def test_import_accepts_a_utf8_bom(service, module):
    project_id, module_id = module
    service.add_requirement(project_id, module_id, {"object_text": "x"})
    target = service.create_project("Target")
    result = service.import_reqif(target.id, "﻿" + _export(service, project_id))
    assert result["requirements_created"] == 1


def test_import_rejects_bytes_that_are_not_utf8(service, module):
    target = service.create_project("Target")
    with pytest.raises(ValueError):
        service.import_reqif(target.id, b"\xff\xfe\x00bad")


def test_import_of_a_document_with_no_specification_creates_nothing(service):
    project = service.create_project("Empty")
    result = service.import_reqif(project.id, _export(service, project.id))
    assert result["requirements_created"] == 0


def test_import_ignores_a_spec_object_no_hierarchy_reaches(service, module):
    """A SPEC-OBJECT no SPEC-HIERARCHY refers to has no place to go, so it is skipped."""
    project_id, module_id = module
    service.add_requirement(project_id, module_id, {"object_text": "reachable"})
    # Give the file a SPEC-OBJECT the hierarchy does not mention.
    xml = _export(service, project_id).replace(
        "</SPEC-OBJECTS>",
        '<SPEC-OBJECT IDENTIFIER="ORPHAN"><VALUES>'
        '<ATTRIBUTE-VALUE-XHTML><DEFINITION><ATTRIBUTE-DEFINITION-XHTML-REF>object_text'
        "</ATTRIBUTE-DEFINITION-XHTML-REF></DEFINITION>"
        "<THE-VALUE><xhtml:div>orphaned</xhtml:div></THE-VALUE>"
        "</ATTRIBUTE-VALUE-XHTML></VALUES>"
        "<TYPE><SPEC-OBJECT-TYPE-REF>SPEC-OBJECT-TYPE-REQUIREMENT</SPEC-OBJECT-TYPE-REF></TYPE>"
        "</SPEC-OBJECT></SPEC-OBJECTS>",
    )
    (module1,) = import_reqif_from_string(xml).modules
    assert [r.attributes["object_text"] for r in module1.requirements] == ["reachable"]

    target = service.create_project("Target").id
    result = service.import_reqif(target, xml)
    assert result["requirements_created"] == 1


def test_import_reads_a_spec_object_reached_twice_once(service, module):
    """The same SPEC-OBJECT under two SPEC-HIERARCHY items is imported once."""
    project_id, module_id = module
    req = service.add_requirement(project_id, module_id, {"object_text": "once"})
    xml = _export(service, project_id).replace(
        "</CHILDREN>",
        '<SPEC-HIERARCHY IDENTIFIER="SECOND"><OBJECT>'
        f"<SPEC-OBJECT-REF>{req.id}</SPEC-OBJECT-REF></OBJECT></SPEC-HIERARCHY></CHILDREN>",
    )
    (module1,) = import_reqif_from_string(xml).modules
    assert [r.attributes["object_text"] for r in module1.requirements] == ["once"]


def test_import_applies_the_target_default_when_the_file_omits_the_attribute(service):
    """A requirement with no value for an attribute gets the target's default, like add requirement."""
    project_id = service.create_project("P").id
    module_id = service.create_module(project_id, "M", "M").id
    service.add_requirement(project_id, module_id, {"object_text": "x"})
    # Added after the requirement, so none of them has a value for it.
    service.add_attribute_definition(project_id, "owner", "Owner", "string", default="unassigned")

    target = service.create_project("Target").id
    service.add_attribute_definition(target, "owner", "Owner", "string", default="nobody")
    service.import_reqif(target, service.export_reqif(project_id))

    imported_module = service.list_modules(target)[0]
    (requirement,) = service.list_requirements(target, imported_module.id)
    assert requirement.attributes["owner"] == "nobody"


def test_import_keeps_the_files_value_over_the_target_default(service):
    """A value the file carries wins; the default only fills a gap."""
    project_id = service.create_project("P").id
    service.add_attribute_definition(project_id, "owner", "Owner", "string", default="unassigned")
    module_id = service.create_module(project_id, "M", "M").id
    service.add_requirement(project_id, module_id, {"object_text": "x", "owner": "rita"})

    target = service.create_project("Target").id
    service.add_attribute_definition(target, "owner", "Owner", "string", default="nobody")
    service.import_reqif(target, service.export_reqif(project_id))

    imported_module = service.list_modules(target)[0]
    (requirement,) = service.list_requirements(target, imported_module.id)
    assert requirement.attributes["owner"] == "rita"


def test_import_writes_rich_text_that_needs_sanitising(service, module):
    """Markup outside the allowed subset is stripped, as on every other write path."""
    project_id, module_id = module
    service.add_requirement(
        project_id,
        module_id,
        {"object_text": "x", "note": rich('<p>keep <strong>this</strong></p><script>drop()</script>')},
    )

    target = service.create_project("Target").id
    service.import_reqif(target, service.export_reqif(project_id))
    imported_module = service.list_modules(target)[0]
    (requirement,) = service.list_requirements(target, imported_module.id)
    note = requirement.attributes["note"]
    assert is_rich(note)
    assert "drop()" not in note
    assert to_plain(note) == "keep this"


def test_import_returns_the_created_requirements(service, module):
    project_id, module_id = module
    root = service.add_requirement(project_id, module_id, {"object_text": "root"})
    service.add_requirement(project_id, module_id, {"object_text": "child"}, parent_id=root.id)

    target = service.create_project("Target")
    result = service.import_reqif(target.id, _export(service, project_id))
    rows = result["requirements"]
    assert len(rows) == 2
    assert rows[0]["parent_id"] is None
    assert rows[1]["parent_id"] == rows[0]["id"]
    assert all(row["module_id"] == rows[0]["module_id"] for row in rows)


# --- REST ------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REQMAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(tmp_path / "config.json"))
    import reqman.app as app_module

    importlib.reload(app_module)
    return TestClient(app_module.app)


def _client_project(client, name="P"):
    project_id = client.post("/projects", json={"name": name}).json()["id"]
    module_id = client.post(f"/projects/{project_id}/modules", json={"name": "M", "prefix": "M"}).json()["id"]
    return project_id, module_id


def test_rest_import_endpoint(client):
    source_id, source_module = _client_project(client, "Source")
    client.post(
        f"/projects/{source_id}/modules/{source_module}/requirements",
        json={"attributes": {"object_text": "imported over REST"}},
    )
    reqif = client.get(f"/projects/{source_id}/export/reqif").text

    target_id, _ = _client_project(client, "Target")
    response = client.post(
        f"/projects/{target_id}/import/reqif", content=reqif, headers={"Content-Type": "application/xml"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["requirements_created"] == 1

    modules = client.get(f"/projects/{target_id}/modules").json()
    requirements = client.get(f"/projects/{target_id}/modules/{modules[0]['id']}/requirements").json()
    assert [r["attributes"]["object_text"] for r in requirements] == ["imported over REST"]


def test_rest_import_endpoint_rejects_a_bad_document(client):
    target_id, _ = _client_project(client, "Target")
    response = client.post(
        f"/projects/{target_id}/import/reqif", content="<REQ-IF>oops", headers={"Content-Type": "application/xml"}
    )
    assert response.status_code == 400


def test_rest_import_endpoint_requires_an_xml_content_type(client):
    """The body is the document itself; a form upload is refused (415), as for every import."""
    target_id, _ = _client_project(client, "Target")
    response = client.post(
        f"/projects/{target_id}/import/reqif", content="{}", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 415


def test_rest_import_endpoint_rejects_non_utf8(client):
    target_id, _ = _client_project(client, "Target")
    response = client.post(
        f"/projects/{target_id}/import/reqif", content=b"\xff\xfe\x00bad", headers={"Content-Type": "application/xml"}
    )
    assert response.status_code == 400


def test_rest_import_and_export_have_websocket_parity(client):
    """Both transports import the same document into an equivalent project the same way."""
    source_id, source_module = _client_project(client, "Source")
    client.post(
        f"/projects/{source_id}/modules/{source_module}/requirements",
        json={"attributes": {"object_text": rich("<p>via <strong>REST</strong></p>")}},
    )
    reqif = client.get(f"/projects/{source_id}/export/reqif").text

    rest_target = client.post("/projects", json={"name": "TargetRest"}).json()["id"]
    rest = client.post(
        f"/projects/{rest_target}/import/reqif", content=reqif, headers={"Content-Type": "application/xml"}
    )
    assert rest.status_code == 200, rest.text

    ws_target = client.post("/projects", json={"name": "TargetWs"}).json()["id"]
    with client.websocket_connect("/ws") as socket:
        socket.send_json(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "project.import_reqif",
                "params": {"project_id": ws_target, "reqif": reqif},
            }
        )
        reply = socket.receive_json()
    assert "error" not in reply, reply

    rest_body, ws_body = rest.json(), reply["result"]
    for key in ("modules_created", "requirements_created", "attributes_created"):
        assert ws_body[key] == rest_body[key]
    assert [r["parent_id"] for r in ws_body["requirements"]] == [
        r["parent_id"] for r in rest_body["requirements"]
    ] == [None]

    def imported(project_id):
        modules = client.get(f"/projects/{project_id}/modules").json()
        requirements = client.get(f"/projects/{project_id}/modules/{modules[0]['id']}/requirements").json()
        return [r["attributes"]["object_text"] for r in requirements]

    assert imported(rest_target) == imported(ws_target)
    # The formatting survives the round trip as rich text, in both.
    (value,) = imported(rest_target)
    assert is_rich(value)
    assert to_plain(value) == "via REST"
