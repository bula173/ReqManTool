"""AI prompt library: prompt templates the user copies into an external AI chat.

ReqManTool never calls an AI service. A prompt is a JSON file whose ``body``
holds ``{{placeholder}}`` markers; ``ProjectService.render_prompt`` fills them
server-side from the current context (project, target module, its exact CSV
import header, the selected requirements as CSV, import rules) and the user
copies the result into any AI chat, then pastes the CSV answer into the CSV
import ("Paste AI result").

Prompt ids are namespaced like templates, in their own namespace:

- ``builtin:<stem>`` — ``backend/reqman/prompts/<stem>.json``, read-only.
- ``user:<stem>`` — ``<data-directory>/_templates/prompts/<stem>.json``
  (a subfolder of ``_templates``, which is never listed as a project).

File format (key order is the written order)::

    {
      "kind": "prompt",
      "format_version": 1,
      "name": str,                       # unique among all prompts (case-insensitive)
      "description": str,
      "selection": "none" | "optional" | "required",
      "free_text": "none" | "optional" | "required",
      "free_text_label": str,            # label of the free-text input ("" = default)
      "import_mode": "create" | "update" | "none",
      "body": str                        # text with {{placeholders}}
    }

``import_mode`` tells the UI how the AI's CSV answer is imported (``none``:
the answer is a review, not CSV). Unknown placeholders make a prompt invalid.
Like the other ``reqman`` format modules this one has no dependency on
``service``/``models`` and no side effects besides reading built-in files.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROMPT_KIND = "prompt"
PROMPT_FORMAT_VERSION = 1

BUILTIN_SOURCE = "builtin"
USER_SOURCE = "user"
PROMPT_SOURCES = (BUILTIN_SOURCE, USER_SOURCE)

BUILTIN_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

SELECTION_MODES = ("none", "optional", "required")
FREE_TEXT_MODES = ("none", "optional", "required")
IMPORT_MODES = ("create", "update", "none")

MAX_PROMPT_NAME_LENGTH = 200
MAX_PROMPT_DESCRIPTION_LENGTH = 2000
MAX_PROMPT_BODY_LENGTH = 64 * 1024
# An imported prompt file (raw bytes) and the free text of one render.
MAX_PROMPT_IMPORT_BYTES = 256 * 1024
MAX_PROMPT_FREE_TEXT_LENGTH = 64 * 1024

PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")

# Every placeholder a prompt body may use, with a description for the editor.
PLACEHOLDERS: dict[str, str] = {
    "project_name": "Name of the project",
    "project_description": "Description of the project",
    "module_name": "Name of the target module (where the CSV answer is imported)",
    "module_description": "Description of the target module",
    "module_type": "Type of the target module (requirements, test_cases, test_execution, operational_scenarios)",
    "module_prefix": "Requirement id prefix of the target module",
    "csv_header": "The exact CSV import header of the target module (one line)",
    "attribute_guide": "One line per CSV column: type, required flag, enum choices, date format",
    "link_columns": "The optional link columns of the target module and what they accept",
    "import_rules": "The import rules (ids, Parent, one CSV code block, no prose; update mode rules)",
    "selection_module_name": "Name of the module the selected requirements come from",
    "selected_ids": "The selected requirement ids, comma separated",
    "selected_csv": "The selected requirements as CSV (Id, Parent and their attributes)",
    "free_text": "The free text typed in the Prompts dialog",
}

PROMPT_KEYS = (
    "kind",
    "format_version",
    "name",
    "description",
    "selection",
    "free_text",
    "free_text_label",
    "import_mode",
    "body",
)


@dataclass
class Prompt:
    id: str
    name: str
    description: str = ""
    selection: str = "none"
    free_text: str = "none"
    free_text_label: str = ""
    import_mode: str = "create"
    body: str = ""

    @property
    def source(self) -> str:
        return split_prompt_id(self.id)[0]

    def to_dict(self) -> dict[str, Any]:
        """The file content (stable key order; the id is the file name, not stored)."""
        return {
            "kind": PROMPT_KIND,
            "format_version": PROMPT_FORMAT_VERSION,
            "name": self.name,
            "description": self.description,
            "selection": self.selection,
            "free_text": self.free_text,
            "free_text_label": self.free_text_label,
            "import_mode": self.import_mode,
            "body": self.body,
        }

    def to_api(self) -> dict[str, Any]:
        """The prompt as the API returns it (id and source first, then the file fields)."""
        data = {"id": self.id, "source": self.source}
        data.update({k: v for k, v in self.to_dict().items() if k not in ("kind", "format_version")})
        data["placeholders"] = used_placeholders(self.body)
        return data


def make_prompt_id(source: str, local_id: str) -> str:
    return f"{source}:{local_id}"


def split_prompt_id(prompt_id: Any) -> tuple[str, str]:
    """``"user:abc"`` → ``("user", "abc")``; raises ``ValueError`` if not namespaced."""
    message = f"Invalid prompt id {prompt_id!r}: expected 'builtin:<id>' or 'user:<id>'"
    if not isinstance(prompt_id, str) or ":" not in prompt_id:
        raise ValueError(message)
    source, local_id = prompt_id.split(":", 1)
    if source not in PROMPT_SOURCES or not local_id:
        raise ValueError(message)
    return source, local_id


def list_builtin_prompt_ids(directory: Path = BUILTIN_PROMPTS_DIR) -> list[str]:
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.json") if p.is_file() and not p.name.startswith("."))


def read_builtin_prompt_text(local_id: str, directory: Path = BUILTIN_PROMPTS_DIR) -> str:
    # Looked up among the listed files, never by joining a raw id into a path.
    if local_id not in list_builtin_prompt_ids(directory):
        raise FileNotFoundError(f"Prompt '{make_prompt_id(BUILTIN_SOURCE, local_id)}' not found")
    return (directory / f"{local_id}.json").read_text(encoding="utf-8-sig")


def used_placeholders(body: str) -> list[str]:
    """The placeholder names ``body`` uses, in first-use order."""
    seen: list[str] = []
    for match in PLACEHOLDER_PATTERN.finditer(body):
        if match.group(1) not in seen:
            seen.append(match.group(1))
    return seen


def _check_text(value: Any, what: str, limit: int, required: bool = False) -> str:
    if value is None:
        if required:
            raise ValueError(f"'{what}' must not be empty")
        return ""
    if not isinstance(value, str):
        raise ValueError(f"'{what}' must be a string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"'{what}' contains a character that cannot be encoded as UTF-8") from exc
    if required and not value.strip():
        raise ValueError(f"'{what}' must not be empty")
    if len(value) > limit:
        raise ValueError(f"'{what}' is too long (at most {limit} characters)")
    return value


def _check_choice(value: Any, what: str, choices: tuple[str, ...], default: str) -> str:
    if value is None:
        return default
    if value not in choices:
        raise ValueError(f"'{what}' must be one of {', '.join(choices)}, got {value!r}")
    return value


def parse_prompt(data: Any, prompt_id: str) -> Prompt:
    """Check a prompt's shape and return it; unknown keys are ignored. Raises ``ValueError``."""
    split_prompt_id(prompt_id)
    if not isinstance(data, dict):
        raise ValueError("a prompt must be a JSON object")
    kind = data.get("kind", PROMPT_KIND)
    if kind != PROMPT_KIND:
        raise ValueError(f"'kind' must be {PROMPT_KIND!r}, got {kind!r}")
    version = data.get("format_version", PROMPT_FORMAT_VERSION)
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValueError(f"'format_version' must be the integer {PROMPT_FORMAT_VERSION}, got {version!r}")
    if version > PROMPT_FORMAT_VERSION:
        raise ValueError(
            f"format_version {version} is newer than this ReqManTool supports ({PROMPT_FORMAT_VERSION}); "
            "update ReqManTool to use this prompt"
        )
    if version != PROMPT_FORMAT_VERSION:
        raise ValueError(f"Unsupported format_version {version}; expected {PROMPT_FORMAT_VERSION}")

    name = _check_text(data.get("name"), "name", MAX_PROMPT_NAME_LENGTH, required=True).strip()
    description = _check_text(data.get("description"), "description", MAX_PROMPT_DESCRIPTION_LENGTH)
    body = _check_text(data.get("body"), "body", MAX_PROMPT_BODY_LENGTH, required=True)
    unknown = [p for p in used_placeholders(body) if p not in PLACEHOLDERS]
    if unknown:
        raise ValueError(
            f"Unknown placeholder(s) {', '.join('{{' + p + '}}' for p in unknown)}; "
            f"available: {', '.join(PLACEHOLDERS)}"
        )
    return Prompt(
        id=prompt_id,
        name=name,
        description=description,
        selection=_check_choice(data.get("selection"), "selection", SELECTION_MODES, "none"),
        free_text=_check_choice(data.get("free_text"), "free_text", FREE_TEXT_MODES, "none"),
        free_text_label=_check_text(data.get("free_text_label"), "free_text_label", MAX_PROMPT_NAME_LENGTH),
        import_mode=_check_choice(data.get("import_mode"), "import_mode", IMPORT_MODES, "create"),
        body=body,
    )


def parse_prompt_text(text: str, prompt_id: str) -> Prompt:
    """Parse a prompt file's JSON text (a leading UTF-8 BOM is ignored)."""
    text = text.removeprefix("﻿")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    except RecursionError as exc:
        raise ValueError("nested too deeply") from exc
    return parse_prompt(data, prompt_id)


def fill_placeholders(body: str, values: dict[str, str]) -> str:
    """Replace every ``{{name}}`` of ``body`` with ``values[name]`` (one pass; values are not re-scanned)."""
    return PLACEHOLDER_PATTERN.sub(lambda m: values.get(m.group(1), m.group(0)), body)
