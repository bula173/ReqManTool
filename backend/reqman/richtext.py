"""Rich text values of ``text`` attributes: detection, sanitising, plain projection.

A ``text`` attribute value (``ObjectText`` included) is either plain text or
rich text. Rich text is a string that starts with exactly ``RICH_TEXT_PREFIX``
(an XHTML ``div`` in the XHTML namespace, the same model as ReqIF's XHTML
values) and holds a small XHTML subset::

    <div xmlns="http://www.w3.org/1999/xhtml"><p>Shall <strong>not</strong> fail.</p></div>

Every other string is plain text, including text that contains ``<`` or even
markup such as ``<p>x</p>``: only the exact prefix marks a value as rich, so
existing plain values keep their meaning. Plain text that would itself start
with the prefix is stored as rich text holding it escaped (``from_plain``).

``sanitize`` returns the canonical form of a value. Plain text is returned
unchanged. Rich text is parsed with ``html.parser`` and rebuilt from an
allow-list; anything else is stripped, never rejected, and the result is
deterministic and idempotent (``sanitize(sanitize(v)) == sanitize(v)``):

- Elements: ``p``, ``br``, ``strong``/``b``, ``em``/``i``, ``u``,
  ``s``/``strike``/``del``, ``ul``, ``ol``, ``li``, ``span``. Aliases are
  written as ``strong``, ``em`` and ``s``; ``br`` as ``<br/>``.
- Attributes: only ``style`` on ``span``, and in it only ``color`` (written
  ``#rrggbb``; ``#rgb`` and ``rgb(r, g, b)`` are converted) and
  ``font-size`` (one of ``FONT_SIZES``). A ``span`` left without a style is
  unwrapped. Every other attribute (event handlers, ``href``, ``class``...)
  is dropped.
- ``script``, ``style`` and other non-content elements (``DROPPED``) are
  removed with their content; any other element (``a``, ``div``, ``h1``,
  ``table``...) is unwrapped, keeping its text. Comments, processing
  instructions and declarations are removed.
- Structure: the top level and list items hold paragraphs and lists;
  stray text is wrapped in a ``p``; a list item starts with a paragraph;
  newlines inside text become spaces (spaces and tabs are kept as they
  are, so an edited plain value keeps its exact spacing); empty inline
  elements are dropped. Unclosed ``p`` and ``li`` elements are closed as
  an HTML parser (the browser) closes them.
- A result with no text is ``""``. A result that is only paragraphs of
  unformatted text is written as plain text (one line per paragraph), so
  editing a value without formatting it never turns plain data into markup.

A rich value over the size limits (``MAX_LENGTH``, ``MAX_NODES``) is the one
thing ``sanitize`` rejects (``RichTextTooLarge``, a ``ValueError``). The limits
apply to both the input and the canonical result (escaping and the added
``p`` elements can make the result larger than the input), so a value
``sanitize`` accepts is always accepted again, unchanged, and is always
projected in full by ``to_plain``.

``to_plain`` is the plain-text projection (search, tooltips, the CSV and
XLSX exports; the DOCX and ReqIF exports read the formatted tree through
``blocks``): paragraphs and line breaks become newlines, list items get
``- `` / ``1. `` markers, indented two spaces per nesting level.

The frontend mirrors these rules in ``frontend/src/components/richText.ts``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

RICH_TEXT_PREFIX = '<div xmlns="http://www.w3.org/1999/xhtml">'
RICH_TEXT_SUFFIX = "</div>"

# The font sizes the editor offers; any other ``font-size`` is stripped.
FONT_SIZES = ("10px", "12px", "14px", "16px", "20px", "24px")

# Inline formatting elements and the name each is written as.
MARKS = {"strong": "strong", "b": "strong", "em": "em", "i": "em", "u": "u", "s": "s", "strike": "s", "del": "s"}
LISTS = ("ul", "ol")
# Elements removed together with their content.
DROPPED = frozenset(
    {
        "script", "style", "template", "iframe", "object", "embed", "noscript", "noembed", "noframes",
        "textarea", "title", "head", "svg", "math", "select", "xmp", "plaintext", "frame", "frameset",
    }
)
# HTML void elements: never hold content, so never stay open.
VOID = frozenset(
    {"br", "img", "hr", "input", "meta", "link", "wbr", "area", "base", "col", "embed", "param", "source", "track"}
)

# Allowed attributes for <img> (void element, so handled specially in handle_startendtag).
# Only data: URLs are accepted (offline/local images). Max ~2 MB decoded (~2.6 MB base64).
IMG_ALLOWED_ATTRS = frozenset({"src", "alt", "width", "height"})
MAX_IMG_DATA_URL_LENGTH = 2_800_000  # ~2 MB decoded

# Parsed elements nested deeper than this are ignored (their text is kept),
# so hostile input cannot exhaust the normaliser's recursion. The output
# nests at most ``MAX_LIST_DEPTH`` lists and ``MAX_MARK_DEPTH`` inline
# elements, far less, so sanitising a sanitised value never hits the limit.
MAX_DEPTH = 200
MAX_LIST_DEPTH = 16
MAX_MARK_DEPTH = 16

# Size limits, so a value costs bounded time and memory to sanitise. A write
# of a rich value longer than ``MAX_LENGTH`` characters, or parsing into more
# than ``MAX_NODES`` elements and text nodes, is rejected (``RichTextTooLarge``,
# a ``ValueError``); so is one whose canonical result is over either limit, so
# every stored value written through ``sanitize`` passes ``sanitize`` again.
# Reading a stored value never fails: of a value over the limits (hand-edited
# or pulled from git), ``to_plain`` reads at most ``MAX_LENGTH`` characters
# and ``MAX_NODES`` nodes; a value ``sanitize`` accepted is read whole. Implicitly
# closed formatting elements are reopened at most ``MAX_CLONES`` times per
# value; further ones are not reopened.
MAX_LENGTH = 2_000_000
MAX_NODES = 300_000
MAX_CLONES = 2_000


class RichTextTooLarge(ValueError):
    """A rich text value exceeds ``MAX_LENGTH`` or ``MAX_NODES``."""

    def __init__(self) -> None:
        super().__init__(
            f"Rich text is too large (at most {MAX_LENGTH} characters and {MAX_NODES} elements)"
        )

_INVALID_CHARS = re.compile("[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\ud800-\\udfff\\ufffe\\uffff]")
_LINE_BREAKS = re.compile(r"\r\n|[\r\n]")
_HEX6 = re.compile(r"#([0-9a-f]{6})")
_HEX3 = re.compile(r"#([0-9a-f]{3})")
_RGB = re.compile(r"rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)")


def is_rich(value: Any) -> bool:
    """True if ``value`` is a rich text string (starts with ``RICH_TEXT_PREFIX``)."""
    return isinstance(value, str) and value.startswith(RICH_TEXT_PREFIX)


# --- parsed tree ---------------------------------------------------------------


@dataclass
class _Element:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[_Element | str] = field(default_factory=list)


# The HTML parsing rules the browser applies (and so the frontend's DOMParser
# mirror), reduced to what changes the sanitised result:
# - start tags that close an open ``p`` ("in button scope");
CLOSES_P = frozenset(
    {
        "address", "article", "aside", "blockquote", "center", "details", "dialog", "dir", "div", "dl",
        "fieldset", "figcaption", "figure", "footer", "header", "hgroup", "main", "menu", "nav", "ol", "p",
        "search", "section", "summary", "ul", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "listing", "form",
        "li", "dd", "dt", "plaintext", "table", "hr", "xmp",
    }
)
# - elements that stop the search for an open ``p`` (button scope boundaries);
_P_SCOPE = frozenset({"applet", "caption", "html", "table", "td", "th", "marquee", "object", "template", "button"})
# - "special" elements: a new ``li`` closes an open ``li`` unless one of these
#   (other than address, div and p) lies in between;
_SPECIAL = frozenset(
    {
        "address", "applet", "area", "article", "aside", "base", "basefont", "bgsound", "blockquote", "body",
        "br", "button", "caption", "center", "col", "colgroup", "dd", "details", "dir", "div", "dl", "dt",
        "embed", "fieldset", "figcaption", "figure", "footer", "form", "frame", "frameset", "h1", "h2", "h3",
        "h4", "h5", "h6", "head", "header", "hgroup", "hr", "html", "iframe", "img", "input", "keygen", "li",
        "link", "listing", "main", "marquee", "menu", "meta", "nav", "noembed", "noframes", "noscript",
        "object", "ol", "p", "param", "plaintext", "pre", "script", "search", "section", "select", "source",
        "style", "summary", "table", "tbody", "td", "template", "textarea", "tfoot", "th", "thead", "title",
        "tr", "track", "ul", "wbr", "xmp",
    }
)
# - formatting elements: one closed implicitly (by a new ``p``, ``li``...)
#   is reopened before the next text or inline element.
_FORMATTING = frozenset(
    {"a", "b", "big", "code", "em", "font", "i", "nobr", "s", "small", "strike", "strong", "tt", "u"}
)


class _TreeBuilder(HTMLParser):
    """Builds a loose element tree, following the browser's HTML parser where it matters.

    End tags close the nearest open element of that name. As in HTML (and so
    the frontend's DOMParser mirror): a block start tag closes an open ``p``,
    an ``li`` closes an open ``li`` of the same list, a stray ``</p>`` is an
    empty paragraph, and formatting elements closed that way are reopened
    before the next text or inline element (HTML's list of active formatting
    elements, with its limit of three equal entries). So hostile input stays
    linear, entries are equal by canonical tag alone (attributes are dropped
    anyway), at most ``MAX_MARK_DEPTH`` entries are tracked and at most
    ``MAX_CLONES`` elements are reopened. After ``MAX_NODES`` nodes the rest
    of the input is ignored and ``truncated`` is set.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Element("#root")
        self.stack = [self.root]
        self.open: set[int] = {id(self.root)}  # ids of the elements in ``stack``
        # So hostile input stays linear, the stack is never scanned for
        # elements that are not there: open elements per tag (end tags), and
        # the stack indices of the open elements that end the search for an
        # open ``p`` (``p`` and ``_P_SCOPE``) and for an open ``li`` (``li``
        # and the ``_SPECIAL`` elements that stop it).
        self.open_tags: dict[str, int] = {}
        self.p_marks: list[int] = []
        self.li_marks: list[int] = []
        self.formatting: list[_Element] = []
        self.nodes = 0
        self.clones = 0
        self.truncated = False

    def _count(self) -> bool:
        """Count one more node; False (and ``truncated``) once ``MAX_NODES`` is reached."""
        if self.nodes >= MAX_NODES:
            self.truncated = True
            return False
        self.nodes += 1
        return True

    def _push(self, element: _Element) -> None:
        self.stack[-1].children.append(element)
        tag = element.tag
        if tag == "p" or tag in _P_SCOPE:
            self.p_marks.append(len(self.stack))
        if tag in _SPECIAL and tag not in ("address", "div", "p"):
            self.li_marks.append(len(self.stack))
        self.stack.append(element)
        self.open.add(id(element))
        self.open_tags[tag] = self.open_tags.get(tag, 0) + 1

    def _pop_to(self, index: int) -> None:
        """Close ``stack[index]`` and everything opened inside it."""
        for element in self.stack[index:]:
            self.open.discard(id(element))
            self.open_tags[element.tag] -= 1
        del self.stack[index:]
        while self.p_marks and self.p_marks[-1] >= index:
            self.p_marks.pop()
        while self.li_marks and self.li_marks[-1] >= index:
            self.li_marks.pop()

    def _p_in_scope(self) -> int:
        """The stack index of an open ``p`` in button scope, or 0 if there is none."""
        if self.p_marks and self.stack[self.p_marks[-1]].tag == "p":
            return self.p_marks[-1]
        return 0

    def _close_li(self) -> None:
        if self.li_marks and self.stack[self.li_marks[-1]].tag == "li":
            self._pop_to(self.li_marks[-1])

    def _reconstruct_formatting(self) -> None:
        """Reopen, inside the current element, the active formatting elements no longer open."""
        entries = self.formatting
        if not entries or id(entries[-1]) in self.open:
            return
        first = len(entries) - 1
        while first > 0 and id(entries[first - 1]) not in self.open:
            first -= 1
        for index in range(first, len(entries)):
            if len(self.stack) > MAX_DEPTH or self.clones >= MAX_CLONES or not self._count():
                break
            self.clones += 1
            clone = _Element(entries[index].tag, dict(entries[index].attrs))
            self._push(clone)
            entries[index] = clone

    def _add_formatting(self, element: _Element) -> None:
        key = MARKS.get(element.tag, element.tag)
        same = [i for i, e in enumerate(self.formatting) if MARKS.get(e.tag, e.tag) == key]
        if len(same) >= 3:
            del self.formatting[same[0]]
        if len(self.formatting) >= MAX_MARK_DEPTH:
            del self.formatting[0]
        self.formatting.append(element)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.truncated:
            return
        if tag == "li":
            self._close_li()
        if tag in CLOSES_P:
            if index := self._p_in_scope():
                self._pop_to(index)
        else:
            self._reconstruct_formatting()
        if len(self.stack) > MAX_DEPTH or not self._count():
            return
        element = _Element(tag, {k: v or "" for k, v in attrs})
        if tag in VOID:
            self.stack[-1].children.append(element)
            return
        self._push(element)
        if tag in _FORMATTING:
            self._add_formatting(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in VOID:
            # As in HTML, the self-closing flag of a non-void element is ignored.
            self.handle_starttag(tag, attrs)
            return
        if self.truncated or len(self.stack) > MAX_DEPTH or not self._count():
            return
        self.stack[-1].children.append(_Element(tag, {k: v or "" for k, v in attrs}))

    def handle_endtag(self, tag: str) -> None:
        if self.truncated:
            return
        if tag in _FORMATTING:
            index = next((i for i in range(len(self.formatting) - 1, -1, -1) if self.formatting[i].tag == tag), None)
            if index is not None:
                entry = self.formatting.pop(index)
                if id(entry) not in self.open:
                    return  # already closed implicitly: nothing to close
        if tag == "p" and not self._p_in_scope():
            # A stray </p>: HTML inserts an empty paragraph.
            if len(self.stack) <= MAX_DEPTH and self._count():
                self.stack[-1].children.append(_Element("p"))
            return
        if not self.open_tags.get(tag):
            return
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                self._pop_to(index)
                return

    def handle_data(self, data: str) -> None:
        if self.truncated:
            return
        self._reconstruct_formatting()
        if self._count():
            self.stack[-1].children.append(data)


def _parse(markup: str, strict: bool) -> list[_Element | str]:
    """The element tree of ``markup``.

    Over ``MAX_LENGTH`` characters or ``MAX_NODES`` nodes, ``strict`` raises
    ``RichTextTooLarge``; otherwise only the part within the limits is read.
    """
    if len(markup) > MAX_LENGTH:
        if strict:
            raise RichTextTooLarge()
        markup = markup[:MAX_LENGTH]
    builder = _TreeBuilder()
    try:
        builder.feed(markup)
        builder.close()
    except Exception:  # noqa: BLE001 - html.parser can fail on odd markup on some Python versions
        # Never fail a write (a 500) over markup the parser cannot handle:
        # keep the value's content as literal text instead.
        return [markup.removesuffix(RICH_TEXT_SUFFIX)]
    if builder.truncated and strict:
        raise RichTextTooLarge()
    return builder.root.children


# --- normalised tree -----------------------------------------------------------
# Inline: str | _Br | _Mark. Blocks: _Para | _List.


@dataclass
class _Br:
    pass


@dataclass
class _Img:
    src: str  # data: URL
    alt: str = ""
    width: str | None = None
    height: str | None = None


@dataclass
class _Mark:
    tag: str  # strong, em, u, s or span
    style: str  # canonical style declarations (span only), else ""
    children: list


@dataclass
class _Para:
    children: list


@dataclass
class _List:
    tag: str  # ul or ol
    items: list[list]  # each item: its blocks, starting with a _Para


def _color(value: str) -> str | None:
    if m := _HEX6.fullmatch(value):
        return "#" + m.group(1)
    if m := _HEX3.fullmatch(value):
        return "#" + "".join(c * 2 for c in m.group(1))
    if m := _RGB.fullmatch(value):
        parts = [int(p) for p in m.groups()]
        if all(p <= 255 for p in parts):
            return "#" + "".join(f"{p:02x}" for p in parts)
    return None


def _style(raw: str) -> str:
    """The allowed declarations of a ``style`` attribute, canonical (color first), or ""."""
    color: str | None = None
    size: str | None = None
    for declaration in raw.split(";"):
        name, sep, value = declaration.partition(":")
        if not sep:
            continue
        name, value = name.strip().lower(), value.strip().lower()
        if name == "color":
            color = _color(value)
        elif name == "font-size":
            size = value if value in FONT_SIZES else None
    parts = []
    if color:
        parts.append(f"color: {color}")
    if size:
        parts.append(f"font-size: {size}")
    return "; ".join(parts)


def _is_kept(node: _Element | str) -> bool:
    """True for nodes the normaliser handles itself; others are replaced by their children."""
    return isinstance(node, str) or node.tag in DROPPED or node.tag in MARKS or node.tag in (
        "br", "img", "span", "p", "ul", "ol"
    )


def _flatten(nodes: list) -> list:
    """``nodes`` with every unknown element (``a``, ``div``, ``li`` outside a list...) unwrapped."""
    result: list = []
    for node in nodes:
        if _is_kept(node):
            result.append(node)
        else:
            result.extend(_flatten(node.children))
    return result


def _clean_text(text: str) -> str:
    return _INVALID_CHARS.sub("", _LINE_BREAKS.sub(" ", text))


def _inline(nodes: list, depth: int = 0) -> list:
    """Inline content: text, line breaks, images and marks. Blocks inside inline content are unwrapped.

    ``depth`` is the number of enclosing marks; deeper marks are unwrapped.
    """
    result: list = []

    def add_text(text: str) -> None:
        if result and isinstance(result[-1], str):
            result[-1] += text
        else:
            result.append(text)

    for node in nodes:
        if isinstance(node, str):
            text = _clean_text(node)
            if text:
                add_text(text)
            continue
        if node.tag in DROPPED:
            continue
        if node.tag == "br":
            result.append(_Br())
            continue
        if node.tag == "img":
            src = node.attrs.get("src", "")
            # Only allow data: URLs (offline/local images), max size enforced.
            if src.startswith("data:") and len(src) <= MAX_IMG_DATA_URL_LENGTH:
                result.append(_Img(
                    src=src,
                    alt=node.attrs.get("alt", ""),
                    width=node.attrs.get("width") or None,
                    height=node.attrs.get("height") or None,
                ))
            continue
        style = _style(node.attrs.get("style", "")) if node.tag == "span" else ""
        if (node.tag in MARKS or style) and depth < MAX_MARK_DEPTH:
            children = _inline(node.children, depth + 1)
            if children:
                result.append(_Mark(MARKS.get(node.tag, "span"), style, children))
            continue
        # A span without an allowed style, a p, list or unknown element: unwrap.
        for child in _inline(node.children, depth):
            if isinstance(child, str):
                add_text(child)
            else:
                result.append(child)
    return result


def _blocks(nodes: list, depth: int = 0) -> list:
    """Block content: paragraphs and lists; runs of inline content become paragraphs.

    ``depth`` is the number of enclosing lists; deeper lists are unwrapped
    into inline content.
    """
    blocks: list = []
    run: list = []

    def flush() -> None:
        inline = _inline(run)
        run.clear()
        if any(not isinstance(n, str) or n.strip() for n in inline):
            blocks.append(_Para(inline))

    for node in _flatten(nodes):
        if isinstance(node, _Element) and node.tag == "p":
            flush()
            blocks.append(_Para(_inline(node.children)))
        elif isinstance(node, _Element) and node.tag in LISTS and depth < MAX_LIST_DEPTH:
            flush()
            items = _list_items(node.children, depth + 1)
            if items:
                blocks.append(_List(node.tag, items))
        else:
            run.append(node)
    flush()
    return blocks


def _item(nodes: list, depth: int) -> list:
    blocks = _blocks(nodes, depth)
    if not blocks or not isinstance(blocks[0], _Para):
        blocks.insert(0, _Para([]))
    return blocks


def _list_items(nodes: list, depth: int) -> list[list]:
    """A list's items. Content between ``li`` elements becomes an item of its own."""
    items: list[list] = []
    run: list = []

    def flush() -> None:
        if any(not isinstance(n, str) or n.strip() for n in run):
            items.append(_item(list(run), depth))
        run.clear()

    for node in nodes:
        if isinstance(node, str) or node.tag in MARKS or node.tag in ("br", "span", "p", "ul", "ol"):
            run.append(node)
        elif node.tag == "li":
            flush()
            items.append(_item(node.children, depth))
        elif node.tag not in DROPPED:
            # An unknown wrapper inside a list: look for items inside it.
            flush()
            items.extend(_list_items(node.children, depth))
    flush()
    return items


def _normalised(value: str, strict: bool = True) -> list:
    """The canonical blocks of a rich value (which starts with ``RICH_TEXT_PREFIX``)."""
    return _blocks(_parse(value[len(RICH_TEXT_PREFIX):], strict))


def _has_text(nodes: list) -> bool:
    for node in nodes:
        if isinstance(node, str):
            if node.strip():
                return True
        elif isinstance(node, (_Mark, _Para)):
            if _has_text(node.children):
                return True
        elif isinstance(node, _List):
            if any(_has_text(item) for item in node.items):
                return True
    return False


# --- output --------------------------------------------------------------------


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _render_inline(nodes: list, count: list[int]) -> str:
    """The markup of inline ``nodes``; adds the elements and text nodes written to ``count[0]``."""
    out = []
    count[0] += len(nodes)
    for node in nodes:
        if isinstance(node, str):
            out.append(_escape(node))
        elif isinstance(node, _Br):
            out.append("<br/>")
        elif isinstance(node, _Img):
            attrs = f'src="{_escape(node.src)}"'
            if node.alt:
                attrs += f' alt="{_escape(node.alt)}"'
            if node.width:
                attrs += f' width="{_escape(node.width)}"'
            if node.height:
                attrs += f' height="{_escape(node.height)}"'
            out.append(f"<img {attrs}/>")
        elif node.tag == "span":
            out.append(f'<span style="{node.style}">{_render_inline(node.children, count)}</span>')
        else:
            out.append(f"<{node.tag}>{_render_inline(node.children, count)}</{node.tag}>")
    return "".join(out)


def _render_blocks(blocks: list, count: list[int]) -> str:
    """The markup of ``blocks``; adds the elements and text nodes written to ``count[0]``."""
    out = []
    count[0] += len(blocks)
    for block in blocks:
        if isinstance(block, _Para):
            out.append(f"<p>{_render_inline(block.children, count)}</p>")
        else:
            count[0] += len(block.items)
            items = "".join(f"<li>{_render_blocks(item, count)}</li>" for item in block.items)
            out.append(f"<{block.tag}>{items}</{block.tag}>")
    return "".join(out)


def _plain_paragraphs(blocks: list) -> str | None:
    """The plain text of ``blocks`` if they are only paragraphs of unformatted text, else None."""
    lines = []
    for block in blocks:
        if not isinstance(block, _Para) or any(not isinstance(n, str) for n in block.children):
            return None
        lines.append("".join(block.children))
    return "\n".join(lines)


def sanitize(value: str) -> str:
    """The canonical, allow-listed form of a ``text`` value (see the module docstring).

    Raises ``RichTextTooLarge`` (a ``ValueError``) for a rich value, or a
    rich result, over the size limits; so anything it returns it accepts
    again and returns unchanged.
    """
    if not is_rich(value):
        return value
    blocks = _normalised(value)
    if not _has_text(blocks):
        return ""
    plain = _plain_paragraphs(blocks)
    if plain is not None and not plain.startswith(RICH_TEXT_PREFIX):
        return plain
    count = [0]
    result = RICH_TEXT_PREFIX + _render_blocks(blocks, count) + RICH_TEXT_SUFFIX
    if len(result) > MAX_LENGTH or count[0] > MAX_NODES:
        raise RichTextTooLarge()
    return result


def from_plain(text: str) -> str:
    """Store ``text`` as plain text: unchanged, unless it starts with the rich prefix.

    Such text is wrapped as rich text holding it escaped (one paragraph per
    line), so it keeps reading as the literal text. Used by the CSV import.
    """
    if not is_rich(text):
        return text
    lines = re.split(r"\r\n|\r|\n", text)
    return RICH_TEXT_PREFIX + "".join(f"<p>{_escape(_clean_text(line))}</p>" for line in lines) + RICH_TEXT_SUFFIX


# --- plain projection ----------------------------------------------------------


def _inline_text(nodes: list) -> str:
    out = []
    for node in nodes:
        if isinstance(node, str):
            out.append(node)
        elif isinstance(node, _Br):
            out.append("\n")
        elif isinstance(node, _Img):
            out.append(f"[image: {node.alt}]" if node.alt else "[image]")
        else:
            out.append(_inline_text(node.children))
    return "".join(out)


def _block_lines(blocks: list, depth: int) -> list[str]:
    """Lines of ``blocks``; paragraphs indented two spaces per ``depth`` (list nesting)."""
    indent = "  " * depth
    lines: list[str] = []
    for block in blocks:
        if isinstance(block, _Para):
            lines.extend(indent + line for line in _inline_text(block.children).split("\n"))
            continue
        for number, item in enumerate(block.items, start=1):
            marker = "- " if block.tag == "ul" else f"{number}. "
            first, *rest = _inline_text(item[0].children).split("\n")
            lines.append(indent + marker + first)
            lines.extend(indent + " " * len(marker) + line for line in rest)
            lines.extend(_block_lines(item[1:], depth + 1))
    return lines


def to_plain(value: Any) -> Any:
    """The plain-text projection of a rich value; any other value is returned unchanged.

    A TipTap JSON document string (``{"type": "doc", "content": [...]}``, as the
    editor writes) projects to its text, like a rich value; anything else that
    is not XHTML rich text is returned unchanged.

    Never raises: of a stored value over the size limits (hand-edited or
    pulled from git), only the part within the limits is projected.
    """
    if not is_rich(value):
        if isinstance(value, str):
            tiptap = _tiptap_text(value)
            if tiptap is not None:
                return tiptap
        return value
    return "\n".join(_block_lines(_normalised(value, strict=False), 0))


# A TipTap JSON document (the editor's format) holds at most this many walked
# nodes and nesting levels when projected; beyond that the rest is skipped, so
# hostile input costs bounded time (like ``MAX_NODES``/``MAX_DEPTH`` above).
_TIPTAP_MAX_NODES = 100_000
_TIPTAP_MAX_DEPTH = 64


def _tiptap_text(value: str) -> str | None:
    """The plain text of a TipTap JSON document string, or None when ``value`` is not one.

    Only ``{"type": "doc", "content": [...]}`` objects count; any other JSON
    (or invalid JSON) reads as the literal string. Paragraphs and headings
    become lines, list items get ``- `` / ``1. `` markers like ``to_plain``,
    ``hardBreak`` a newline; marks are dropped. Never raises.
    """
    stripped = value.strip()
    if not (stripped.startswith('{"') and stripped.endswith("}")):
        return None
    try:
        doc = json.loads(stripped)
    except (ValueError, RecursionError):
        return None
    if not isinstance(doc, dict) or doc.get("type") != "doc" or not isinstance(doc.get("content"), list):
        return None
    lines: list[str] = []
    count = [0]

    def inline_text(node: Any, depth: int) -> str:
        if depth > _TIPTAP_MAX_DEPTH or count[0] > _TIPTAP_MAX_NODES or not isinstance(node, dict):
            return ""
        count[0] += 1
        kind = node.get("type")
        if kind == "text":
            text = node.get("text")
            return text if isinstance(text, str) else ""
        if kind == "hardBreak":
            return "\n"
        content = node.get("content")
        if isinstance(content, list):
            return "".join(inline_text(child, depth + 1) for child in content)
        return ""

    def block_lines(nodes: Any, depth: int) -> None:
        if not isinstance(nodes, list) or depth > _TIPTAP_MAX_DEPTH:
            return
        indent = "  " * depth
        number = 0
        for node in nodes:
            if not isinstance(node, dict):
                continue
            count[0] += 1
            if count[0] > _TIPTAP_MAX_NODES:
                return
            kind = node.get("type")
            content = node.get("content") if isinstance(node.get("content"), list) else []
            if kind in ("paragraph", "heading"):
                for line in inline_text(node, depth + 1).split("\n"):
                    lines.append(indent + line)
            elif kind in ("bulletList", "orderedList"):
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    number = number + 1 if kind == "orderedList" else 0
                    marker = "- " if kind == "bulletList" else f"{number}. "
                    item_content = item.get("content") if isinstance(item.get("content"), list) else []
                    first = True
                    for block in item_content:
                        if not isinstance(block, dict):
                            continue
                        block_kind = block.get("type")
                        if block_kind in ("paragraph", "heading") and first:
                            first = False
                            parts = inline_text(block, depth + 1).split("\n")
                            lines.append(indent + marker + parts[0])
                            lines.extend(indent + " " * len(marker) + part for part in parts[1:])
                        elif block_kind in ("paragraph", "heading"):
                            for line in inline_text(block, depth + 1).split("\n"):
                                lines.append(indent + " " * len(marker) + line)
                        else:
                            block_lines([block], depth + 1)
            else:
                # A list item outside a list, a blockquote, or an unknown
                # block: descend (depth grows, so hostile nesting stops at
                # _TIPTAP_MAX_DEPTH), so its text is still projected.
                block_lines(content, depth + 1)

    block_lines(doc["content"], 0)
    return "\n".join(lines)


# --- structured read (formatted exports) ---------------------------------------
# The DOCX and ReqIF exports walk the canonical tree of a rich value instead of
# re-parsing its markup. Blocks are ``Paragraph`` (``.children``: inline nodes)
# and ``ListBlock`` (``.tag``: ``ul``/``ol``; ``.items``: each item's blocks,
# starting with a ``Paragraph``). Inline nodes are ``str``, ``LineBreak``,
# ``Mark`` (``.tag``: strong, em, u, s or span; ``.style``: the canonical span
# style, see ``span_style``; ``.children``: inline nodes) and ``Image``.

Paragraph = _Para
ListBlock = _List
Mark = _Mark
LineBreak = _Br
Image = _Img


def blocks(value: Any) -> list:
    """The canonical blocks of a rich value; ``[]`` if it has no visible text.

    Any other value (plain text, which is never markup, or a non-string)
    has no blocks: ``[]``. Never raises: like ``to_plain``, a stored value
    over the size limits is read only within the limits.
    """
    if not is_rich(value):
        return []
    tree = _normalised(value, strict=False)
    return tree if _has_text(tree) else []


def span_style(style: str) -> tuple[str | None, str | None]:
    """``(color, font_size)`` of a span style: ``#rrggbb`` and e.g. ``"14px"``, each or None."""
    color: str | None = None
    size: str | None = None
    for declaration in _style(style).split(";"):
        name, _, value = declaration.partition(":")
        if name.strip() == "color":
            color = value.strip()
        elif name.strip() == "font-size":
            size = value.strip()
    return color, size


def xml_text(text: str) -> str:
    """``text`` without the characters XML 1.0 cannot hold (control characters...)."""
    return _INVALID_CHARS.sub("", text)
