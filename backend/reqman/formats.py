"""On-disk format versions and the migrations between them.

Two kinds of file carry an integer ``format_version`` as their first key:

- ``project.json`` (``PROJECT_FORMAT``). A project's module, requirement and
  link-pair files carry no version of their own: they are governed by their
  project's version.
- the app config file, ``config.json`` (``CONFIG_FORMAT``; location: see
  ``config.config_file()``).
- the provider connections file, ``connections.json`` beside it
  (``CONNECTIONS_FORMAT``).

Project templates keep their own, independent ``format_version`` (see
``templates.py``); it is not handled here.

Rules:

- A file without the key is version 1, the format written before versions
  existed, and keeps loading unchanged.
- Loading never rewrites a file. ``upgrade`` brings an older file's data to
  the current version *in memory*, one explicit step at a time
  (``FileFormat.migrations[n]`` turns version ``n`` into ``n + 1``); the
  current version is written by the next real save (``with_format_version``).
- A file whose version is newer than the running tool supports raises
  ``NewerFormatError`` on read, and every write path checks the version on
  disk first, so such a file is never misread or overwritten.
- Bump a ``current`` version for a change an older reader would
  misinterpret, and add the migration step in the same change. Also bump it
  for any new persisted key that must survive a round-trip through an older
  tool: ``Project.from_dict``/``to_dict`` drop unknown keys, so a versioned
  tool that loads and saves a project would silently delete a key added
  later without a bump. ``Project.module_order`` was added without a bump
  only because the tools it could be lost through predate versioning and
  cannot be protected anyway; from this release on, new keys bump the
  version (module folders did: project format 7).
- A project migration that changes module, requirement or link-pair files
  must upgrade those files as well before the project is saved at the new
  version, because they are read with their project's version.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field

FORMAT_VERSION_KEY = "format_version"

# The version of files written before ``format_version`` existed.
UNVERSIONED = 1

Migration = Callable[[dict], dict]


@dataclass(frozen=True)
class FileFormat:
    """A versioned file kind: its name in messages, current version and upgrade steps."""

    name: str
    current: int
    # ``migrations[n]`` returns version ``n`` data upgraded to ``n + 1``.
    migrations: dict[int, Migration] = field(default_factory=dict)


class NewerFormatError(ValueError):
    """A file was written by a newer ReqManTool than this one (REST 409).

    The file is neither interpreted nor overwritten.
    """


def _project_v1_to_v2(data: dict) -> dict:
    """Project format 1 -> 2: attribute definitions gained ``modules``.

    Version 2 adds ``AttributeDefinition.modules`` (the module ids an
    attribute applies to; absent or empty = every module). Version 1 had no
    such scope, so every attribute applied to every module; a stray
    ``modules`` key in a hand-edited version 1 file was ignored then and is
    dropped here so it keeps meaning nothing. Module, requirement and
    link-pair files are unchanged.
    """
    attributes = data.get("attributes")
    if isinstance(attributes, list):
        for attr in attributes:
            if isinstance(attr, dict):
                attr.pop("modules", None)
    return data


def _project_v2_to_v3(data: dict) -> dict:
    """Project format 2 -> 3: projects gained ``row_styles``.

    Version 3 adds ``Project.row_styles`` (row font/background color and bold
    per enum attribute value; absent or empty = no styling). Version 2 had no
    styling, so a migrated project has no rules: existing projects are not
    given the defaults new template projects get. A stray ``row_styles`` key
    in a hand-edited version 2 file was ignored then and is dropped here so
    it keeps meaning nothing. Module, requirement and link-pair files are
    unchanged.
    """
    data.pop("row_styles", None)
    return data


def _project_v3_to_v4(data: dict) -> dict:
    """Project format 3 -> 4: ``text`` attribute values may be rich text.

    Version 4 lets the values of ``text`` attributes (requirement files)
    hold rich text: a sanitised XHTML subset string starting with
    ``richtext.RICH_TEXT_PREFIX`` (see ``richtext.py``). Every version 3
    value is a valid version 4 value with the same meaning, so nothing is
    rewritten: plain text stays plain text, ``<`` and markup-like text
    included. The one theoretical exception, a version 3 plain value
    starting with that exact ``<div xmlns="http://www.w3.org/1999/xhtml">``
    prefix, reads as rich text from now on (and is displayed sanitised).
    The bump exists so that an older ReqManTool, which would show rich
    values as raw markup, refuses version 4 projects instead.
    """
    return data


def _project_v4_to_v5(data: dict) -> dict:
    """Project format 4 -> 5: module types, type-scoped attributes and ``date`` attributes.

    Version 5 adds ``Module.type`` (``module.json``: ``requirements``,
    ``test_cases`` or ``test_execution``, later also
    ``operational_scenarios`` (format 9); absent = ``requirements``),
    ``AttributeDefinition.module_types`` (the module types an attribute also
    applies to; absent or empty = none) and the ``date`` attribute type
    (ISO ``YYYY-MM-DD`` strings). Version 4 had none of them: every module
    held requirements, and a stray ``module_types`` key in a hand-edited
    version 4 file was ignored then and is dropped here so it keeps meaning
    nothing. Module files are not rewritten: a version 4 ``module.json``
    has no ``type`` key (the one theoretical exception, a hand-added one,
    is read as the module's type from now on). Requirement and link-pair
    files are unchanged. The bump exists so that an older ReqManTool, which
    would drop module types and type scopes on save and not validate date
    values, refuses version 5 projects instead.
    """
    attributes = data.get("attributes")
    if isinstance(attributes, list):
        for attr in attributes:
            if isinstance(attr, dict):
                attr.pop("module_types", None)
    return data


def _project_v5_to_v6(data: dict) -> dict:
    """Project format 5 -> 6: project references and cross-project link configurations.

    Version 6 adds ``Project.references`` (ids of other projects whose
    modules link configurations may target; absent or empty = none besides
    the implicit parent) and ``LinkConfiguration.target_project_id`` (the
    project of a cross-project configuration's target module; absent = the
    configuration's own project). Version 5 had neither: stray keys of
    those names in a hand-edited version 5 file were ignored then and are
    dropped here so they keep meaning nothing. Module, requirement and
    link-pair files are unchanged (the pairs of a cross-project
    configuration hold plain requirement ids; the configuration names the
    target project). The bump exists so that an older ReqManTool, which
    would drop both keys on save and turn cross-project configurations
    into configurations to a local module of the same id, refuses version
    6 projects instead.
    """
    data.pop("references", None)
    configs = data.get("link_configurations")
    if isinstance(configs, list):
        for config in configs:
            if isinstance(config, dict):
                config.pop("target_project_id", None)
    return data


def _project_v6_to_v7(data: dict) -> dict:
    """Project format 6 -> 7: module folders.

    Version 7 turns ``Project.module_order`` from a flat list of module id
    strings into ordered entries that are either a module ``{"module": id}``
    or a folder ``{"folder": id, "name": ..., "children": [...]}`` (nested,
    see ``models.ModuleFolder``). The step is lossless: every id string
    becomes a module entry in the same position. A version 6 reader ignored
    any non-string entry, so such entries in a hand-edited version 6 file
    are dropped here and keep meaning nothing; a ``module_order`` that is
    not a list (ignored by version 6) is dropped too. Module, requirement
    and link-pair files are unchanged. The bump exists so that an older
    ReqManTool, which would drop folders (and every module inside one) from
    the order on save, refuses version 7 projects instead.
    """
    order = data.get("module_order")
    if isinstance(order, list):
        data["module_order"] = [{"module": entry} for entry in order if isinstance(entry, str)]
    else:
        data.pop("module_order", None)
    return data


def _project_v7_to_v8(data: dict) -> dict:
    """Project format 7 -> 8: project lock.

    Version 8 adds ``Project.locked`` (who locked the project, when and why;
    see ``models.ProjectLock``). Version 7 had no lock: a stray ``locked``
    key in a hand-edited version 7 file was ignored then and is dropped here
    so it keeps meaning nothing. Module, requirement and link-pair files are
    unchanged. The bump exists so that an older ReqManTool, which would drop
    the key on save and so silently unlock the project, refuses version 8
    projects instead.
    """
    data.pop("locked", None)
    return data


def _project_v8_to_v9(data: dict) -> dict:
    """Project format 8 -> 9: the ``operational_scenarios`` module type.

    Version 9 adds ``operational_scenarios`` to ``MODULE_TYPES``
    (``module.json`` ``type`` and ``AttributeDefinition.module_types``).
    Version 8 read an unknown module type as ``requirements``, so no module
    of a version 8 project had this type and an ``operational_scenarios``
    entry in an attribute's ``module_types`` matched nothing: such entries
    are dropped here so they keep meaning nothing. Module files are not
    rewritten (the one theoretical exception, a hand-added
    ``"type": "operational_scenarios"``, is read as the module's type from
    now on). Requirement and link-pair files are unchanged. The bump exists
    so that an older ReqManTool, which would read operational scenario
    modules as requirements modules and drop the type and type scopes on
    save, refuses version 9 projects instead.
    """
    attributes = data.get("attributes")
    if isinstance(attributes, list):
        for attr in attributes:
            if isinstance(attr, dict) and isinstance(attr.get("module_types"), list):
                kept = [t for t in attr["module_types"] if t != "operational_scenarios"]
                if kept:
                    attr["module_types"] = kept
                else:
                    attr.pop("module_types")
    return data


def _project_v9_to_v10(data: dict) -> dict:
    """Project format 9 -> 10: the template a project was created from.

    Version 10 adds the optional ``Project.template_id`` (the project
    template's id, informational: "Apply chapter template…" pre-selects it).
    Version 9 had no such key, so a stray one in a hand-edited version 9 file
    was ignored then and is dropped here so it keeps meaning nothing — an
    older project simply has no remembered origin. Module, requirement and
    link-pair files are unchanged. The bump exists so that an older
    ReqManTool, whose ``Project.from_dict``/``to_dict`` would drop the key on
    save, refuses version 10 projects instead of forgetting the template.
    """
    data.pop("template_id", None)
    return data


PROJECT_FORMAT = FileFormat(
    name="project",
    current=10,
    migrations={
        1: _project_v1_to_v2,
        2: _project_v2_to_v3,
        3: _project_v3_to_v4,
        4: _project_v4_to_v5,
        5: _project_v5_to_v6,
        6: _project_v6_to_v7,
        7: _project_v7_to_v8,
        8: _project_v8_to_v9,
        9: _project_v9_to_v10,
    },
)
CONFIG_FORMAT = FileFormat(name="config", current=1)
# GitLab/GitHub provider connections (``connections.json`` next to the config
# file, see ``providers.py``): metadata only, tokens live in the OS keychain.
CONNECTIONS_FORMAT = FileFormat(name="provider connections", current=1)


def stored_format_version(data: dict, what: str) -> int:
    """The version recorded in ``data`` (``UNVERSIONED`` if missing).

    Raises ``ValueError`` for a value that is not a positive integer.
    ``what`` names the file in the message.
    """
    if FORMAT_VERSION_KEY not in data:
        return UNVERSIONED
    version = data[FORMAT_VERSION_KEY]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError(f"{what} has an invalid format_version {version!r}: expected a positive integer")
    return version


def check_format_version(data: dict, fmt: FileFormat, what: str) -> int:
    """Return ``data``'s version, or raise ``NewerFormatError`` if this tool cannot read it."""
    version = stored_format_version(data, what)
    if version > fmt.current:
        raise NewerFormatError(
            f"{what} was written by a newer ReqManTool (format_version {version}; this "
            f"ReqManTool supports {fmt.name} format_version {fmt.current} or older). Update "
            "ReqManTool to use it; the file was not changed."
        )
    return version


def upgrade(data: dict, fmt: FileFormat, what: str) -> dict:
    """``data`` brought to ``fmt.current`` in memory, without the ``format_version`` key.

    The input is not modified. Raises ``NewerFormatError`` for a newer file
    and ``ValueError`` for an invalid version or a missing migration step.
    """
    version = check_format_version(data, fmt, what)
    result = {k: v for k, v in data.items() if k != FORMAT_VERSION_KEY}
    if version < fmt.current:
        result = copy.deepcopy(result)
    while version < fmt.current:
        step = fmt.migrations.get(version)
        if step is None:
            raise ValueError(
                f"{what} has format_version {version}, but this ReqManTool has no migration "
                f"from {fmt.name} format_version {version} to {version + 1}"
            )
        result = step(result)
        result.pop(FORMAT_VERSION_KEY, None)
        version += 1
    return result


def with_format_version(data: dict, fmt: FileFormat) -> dict:
    """``data`` with ``format_version`` set to the current version, as its first key."""
    return {FORMAT_VERSION_KEY: fmt.current, **{k: v for k, v in data.items() if k != FORMAT_VERSION_KEY}}

