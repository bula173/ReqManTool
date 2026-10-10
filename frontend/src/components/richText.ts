/**
 * Rich text values of `text` attributes (ObjectText included). Mirrors
 * `backend/reqman/richtext.py`; keep the two in sync.
 *
 * A value is rich text exactly when it starts with `RICH_TEXT_PREFIX` (an
 * XHTML `div`, as in ReqIF); every other string is plain text, `<` and
 * markup-like text included. Rich text holds an allow-listed XHTML subset:
 * `p`, `br`, `strong`, `em`, `u`, `s`, `ul`, `ol`, `li` and `span` with only
 * `color: #rrggbb` / `font-size` (one of `FONT_SIZES`). The server sanitises
 * every write; `sanitizeRichText` applies the same rules on the client before
 * anything is rendered as HTML (hand-edited data and older revisions are
 * never trusted), and turns editor output into the value to store.
 */

export const RICH_TEXT_PREFIX = '<div xmlns="http://www.w3.org/1999/xhtml">';
export const RICH_TEXT_SUFFIX = '</div>';

/** The font sizes the editor offers (the server accepts no others). */
export const FONT_SIZES = ['10px', '12px', '14px', '16px', '20px', '24px'] as const;

const MARKS: Record<string, string> = { strong: 'strong', b: 'strong', em: 'em', i: 'em', u: 'u', s: 's', strike: 's', del: 's' };
const DROPPED = new Set([
  'script', 'style', 'template', 'iframe', 'object', 'embed', 'noscript', 'noembed', 'noframes',
  'textarea', 'title', 'head', 'svg', 'math', 'select', 'xmp', 'plaintext', 'frame', 'frameset',
]);
const KEPT = new Set(['br', 'img', 'span', 'p', 'ul', 'ol']);
// Max length of a data: URL for an image (~2 MB decoded = ~2.6 MB base64)
const MAX_IMG_DATA_URL_LENGTH = 2_800_000;
const MAX_DEPTH = 200;
const MAX_LIST_DEPTH = 16;
const MAX_MARK_DEPTH = 16;
// eslint-disable-next-line no-control-regex
const INVALID_CHARS = /[\u0000-\u0008\u000b\u000c\u000e-\u001f￾￿]/g;
// Newlines in text become spaces; spaces and tabs are kept (an edited plain value keeps its spacing).
const LINE_BREAKS = /\r\n|[\r\n]/g;

export function isRichText(value: unknown): value is string {
  return typeof value === 'string' && value.startsWith(RICH_TEXT_PREFIX);
}

// --- parsed tree (from the browser's inert HTML parser) --------------------------

interface El {
  tag: string;
  style: string;
  src?: string;
  alt?: string;
  width?: string;
  height?: string;
  children: Node_[];
}
type Node_ = El | string;

function toTree(node: Node, depth: number): Node_[] {
  const result: Node_[] = [];
  node.childNodes.forEach((child) => {
    if (child.nodeType === 3) result.push(child.textContent ?? '');
    else if (child.nodeType === 1) {
      const el = child as Element;
      const tag = el.localName.toLowerCase();
      if (depth >= MAX_DEPTH) {
        result.push(...toTree(el, depth));
        return;
      }
      const element: El = {
        tag,
        style: el.getAttribute('style') ?? '',
        children: toTree(el, depth + 1),
      };
      if (tag === 'img') {
        element.src = el.getAttribute('src') ?? '';
        element.alt = el.getAttribute('alt') ?? '';
        element.width = el.getAttribute('width') ?? undefined;
        element.height = el.getAttribute('height') ?? undefined;
      }
      result.push(element);
    }
  });
  return result;
}

function parse(markup: string): Node_[] {
  // DOMParser documents are inert: no scripts run, no resources load.
  const doc = new DOMParser().parseFromString(`<!DOCTYPE html><html><body>${markup}</body></html>`, 'text/html');
  return toTree(doc.body, 0);
}

// --- normalised tree -------------------------------------------------------------

interface Br {
  kind: 'br';
}
interface Mark {
  kind: 'mark';
  tag: string;
  style: string;
  children: Inline[];
}
interface Img {
  kind: 'img';
  src: string;
  alt: string;
  width: string | null;
  height: string | null;
}
type Inline = string | Br | Mark | Img;
interface Para {
  kind: 'p';
  children: Inline[];
}
interface List {
  kind: 'list';
  tag: 'ul' | 'ol';
  items: Block[][];
}
type Block = Para | List;

function color(value: string): string | null {
  let m = /^#([0-9a-f]{6})$/.exec(value);
  if (m) return `#${m[1]}`;
  m = /^#([0-9a-f]{3})$/.exec(value);
  if (m) return `#${[...m[1]].map((c) => c + c).join('')}`;
  m = /^rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)$/.exec(value);
  if (m) {
    const parts = [m[1], m[2], m[3]].map(Number);
    if (parts.every((p) => p <= 255)) return `#${parts.map((p) => p.toString(16).padStart(2, '0')).join('')}`;
  }
  return null;
}

function canonicalStyle(raw: string): string {
  let c: string | null = null;
  let size: string | null = null;
  for (const declaration of raw.split(';')) {
    const at = declaration.indexOf(':');
    if (at < 0) continue;
    const name = declaration.slice(0, at).trim().toLowerCase();
    const value = declaration.slice(at + 1).trim().toLowerCase();
    if (name === 'color') c = color(value);
    else if (name === 'font-size') size = (FONT_SIZES as readonly string[]).includes(value) ? value : null;
  }
  return [c && `color: ${c}`, size && `font-size: ${size}`].filter(Boolean).join('; ');
}

const isKept = (node: Node_) =>
  typeof node === 'string' || DROPPED.has(node.tag) || node.tag in MARKS || KEPT.has(node.tag);

function flatten(nodes: Node_[]): Node_[] {
  return nodes.flatMap((node) => (isKept(node) ? [node] : flatten((node as El).children)));
}

const cleanText = (text: string) => text.replace(LINE_BREAKS, ' ').replace(INVALID_CHARS, '');

function inline(nodes: Node_[], depth = 0): Inline[] {
  const result: Inline[] = [];
  const addText = (text: string) => {
    const last = result.length - 1;
    if (last >= 0 && typeof result[last] === 'string') result[last] = (result[last] as string) + text;
    else result.push(text);
  };
  for (const node of nodes) {
    if (typeof node === 'string') {
      const text = cleanText(node);
      if (text) addText(text);
      continue;
    }
    if (DROPPED.has(node.tag)) continue;
    if (node.tag === 'br') {
      result.push({ kind: 'br' });
      continue;
    }
    if (node.tag === 'img') {
      const src = node.src ?? '';
      // Only allow data: URLs (offline/local images), max size enforced.
      if (src.startsWith('data:') && src.length <= MAX_IMG_DATA_URL_LENGTH) {
        result.push({
          kind: 'img',
          src,
          alt: node.alt ?? '',
          width: node.width ?? null,
          height: node.height ?? null,
        });
      }
      continue;
    }
    const style = node.tag === 'span' ? canonicalStyle(node.style) : '';
    if ((node.tag in MARKS || style) && depth < MAX_MARK_DEPTH) {
      const children = inline(node.children, depth + 1);
      if (children.length) result.push({ kind: 'mark', tag: MARKS[node.tag] ?? 'span', style, children });
      continue;
    }
    for (const child of inline(node.children, depth)) {
      if (typeof child === 'string') addText(child);
      else result.push(child);
    }
  }
  return result;
}

const hasContent = (nodes: (Inline | Node_)[]) => nodes.some((n) => typeof n !== 'string' || n.trim() !== '');

function blocks(nodes: Node_[], depth = 0): Block[] {
  const result: Block[] = [];
  let run: Node_[] = [];
  const flush = () => {
    const content = inline(run);
    run = [];
    if (hasContent(content)) result.push({ kind: 'p', children: content });
  };
  for (const node of flatten(nodes)) {
    if (typeof node !== 'string' && node.tag === 'p') {
      flush();
      result.push({ kind: 'p', children: inline(node.children) });
    } else if (typeof node !== 'string' && (node.tag === 'ul' || node.tag === 'ol') && depth < MAX_LIST_DEPTH) {
      flush();
      const items = listItems(node.children, depth + 1);
      if (items.length) result.push({ kind: 'list', tag: node.tag, items });
    } else {
      run.push(node);
    }
  }
  flush();
  return result;
}

function item(nodes: Node_[], depth: number): Block[] {
  const result = blocks(nodes, depth);
  if (result.length === 0 || result[0].kind !== 'p') result.unshift({ kind: 'p', children: [] });
  return result;
}

function listItems(nodes: Node_[], depth: number): Block[][] {
  const items: Block[][] = [];
  let run: Node_[] = [];
  const flush = () => {
    if (hasContent(run)) items.push(item(run, depth));
    run = [];
  };
  for (const node of nodes) {
    if (typeof node === 'string' || node.tag in MARKS || KEPT.has(node.tag)) run.push(node);
    else if (node.tag === 'li') {
      flush();
      items.push(item(node.children, depth));
    } else if (!DROPPED.has(node.tag)) {
      flush();
      items.push(...listItems(node.children, depth));
    }
  }
  flush();
  return items;
}

function hasText(nodes: (Inline | Block)[]): boolean {
  return nodes.some((node) => {
    if (typeof node === 'string') return node.trim() !== '';
    if (node.kind === 'img') return true; // Images count as content
    if (node.kind === 'mark' || node.kind === 'p') return hasText(node.children);
    if (node.kind === 'list') return node.items.some((i) => hasText(i));
    return false;
  });
}

// Tables render the same values again and again: keep recent parses.
const CACHE_LIMIT = 1000;
const cache = new Map<string, Block[]>();

function normalised(value: string): Block[] {
  const hit = cache.get(value);
  if (hit) return hit;
  const result = blocks(parse(value.slice(RICH_TEXT_PREFIX.length)));
  if (cache.size >= CACHE_LIMIT) cache.delete(cache.keys().next().value as string);
  cache.set(value, result);
  return result;
}

// --- output ----------------------------------------------------------------------

const escapeHtml = (text: string) => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

function renderInline(nodes: Inline[]): string {
  return nodes
    .map((node) => {
      if (typeof node === 'string') return escapeHtml(node);
      if (node.kind === 'br') return '<br/>';
      if (node.kind === 'img') {
        let attrs = `src="${escapeHtml(node.src)}"`;
        if (node.alt) attrs += ` alt="${escapeHtml(node.alt)}"`;
        if (node.width) attrs += ` width="${escapeHtml(node.width)}"`;
        if (node.height) attrs += ` height="${escapeHtml(node.height)}"`;
        return `<img ${attrs}/>`;
      }
      if (node.tag === 'span') return `<span style="${node.style}">${renderInline(node.children)}</span>`;
      return `<${node.tag}>${renderInline(node.children)}</${node.tag}>`;
    })
    .join('');
}

function renderBlocks(list: Block[]): string {
  return list
    .map((block) =>
      block.kind === 'p'
        ? `<p>${renderInline(block.children)}</p>`
        : `<${block.tag}>${block.items.map((i) => `<li>${renderBlocks(i)}</li>`).join('')}</${block.tag}>`,
    )
    .join('');
}

/**
 * The canonical form of a `text` value, as the server stores it: plain text
 * unchanged; rich text allow-listed, `""` when it has no text, and plain text
 * when it is only unformatted paragraphs.
 */
export function sanitizeRichText(value: string): string {
  if (!isRichText(value)) return value;
  const content = normalised(value);
  if (!hasText(content)) return '';
  if (content.every((b) => b.kind === 'p' && b.children.every((c) => typeof c === 'string'))) {
    const plain = content.map((b) => (b as Para).children.join('')).join('\n');
    if (!plain.startsWith(RICH_TEXT_PREFIX)) return plain;
  }
  return RICH_TEXT_PREFIX + renderBlocks(content) + RICH_TEXT_SUFFIX;
}

/** The sanitised inner HTML of a rich value (without the wrapping `div`), safe to render. */
export function richTextInnerHtml(value: string): string {
  return renderBlocks(normalised(value));
}

/** Plain text as editor HTML: one paragraph per line. */
export function plainToEditorHtml(text: string): string {
  return text
    .split(/\r\n|\r|\n/)
    .map((line) => `<p>${escapeHtml(cleanText(line))}</p>`)
    .join('');
}

/** Editor HTML for a stored value (rich or plain). */
export function valueToEditorHtml(value: unknown): string {
  if (value === null || value === undefined) return '';
  const text = String(value);
  return isRichText(text) ? richTextInnerHtml(text) : plainToEditorHtml(text);
}

/** The value to store for the editor's HTML: canonical rich text, or plain text when unformatted. */
export function editorHtmlToValue(html: string): string {
  return sanitizeRichText(RICH_TEXT_PREFIX + html + RICH_TEXT_SUFFIX);
}

// --- plain projection ------------------------------------------------------------

function inlineText(nodes: Inline[]): string {
  return nodes
    .map((node) => {
      if (typeof node === 'string') return node;
      if (node.kind === 'br') return '\n';
      if (node.kind === 'img') return node.alt ? `[image: ${node.alt}]` : '[image]';
      return inlineText(node.children);
    })
    .join('');
}

function blockLines(list: Block[], depth: number): string[] {
  const indent = '  '.repeat(depth);
  const lines: string[] = [];
  for (const block of list) {
    if (block.kind === 'p') {
      lines.push(...inlineText(block.children).split('\n').map((l) => indent + l));
      continue;
    }
    block.items.forEach((entry, index) => {
      const marker = block.tag === 'ul' ? '- ' : `${index + 1}. `;
      const [first, ...rest] = inlineText((entry[0] as Para).children).split('\n');
      lines.push(indent + marker + first);
      lines.push(...rest.map((l) => indent + ' '.repeat(marker.length) + l));
      lines.push(...blockLines(entry.slice(1), depth + 1));
    });
  }
  return lines;
}

/**
 * The plain-text projection (search, tooltips, single-line cells, diffs):
 * paragraphs and line breaks become newlines, list items get `- ` / `1. `.
 * Any other value is returned as `String(value)` (`''` for null/undefined).
 */
export function richTextToPlain(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (!isRichText(value)) return typeof value === 'string' ? value : String(value);
  return blockLines(normalised(value), 0).join('\n');
}
