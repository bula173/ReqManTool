import type { Shortcut } from './types';

export const IS_MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad|iPod/i.test(navigator.platform || navigator.userAgent);

/** True when `event` is exactly `shortcut` (no extra modifiers). `mod` is Cmd on macOS, Ctrl elsewhere. */
export function matchesShortcut(event: KeyboardEvent, shortcut: Shortcut): boolean {
  const modPressed = IS_MAC ? event.metaKey : event.ctrlKey;
  const otherCommandKey = IS_MAC ? event.ctrlKey : event.metaKey;
  if (Boolean(shortcut.mod) !== modPressed || otherCommandKey) return false;
  if (Boolean(shortcut.shift) !== event.shiftKey) return false;
  if (Boolean(shortcut.alt) !== event.altKey) return false;
  // With Alt (Option on macOS) `key` can be a composed character, so letters
  // are also matched on the physical key code.
  if (event.key.toLowerCase() === shortcut.key.toLowerCase()) return true;
  return shortcut.key.length === 1 && event.code === `Key${shortcut.key.toUpperCase()}`;
}

const KEY_LABELS: Record<string, string> = {
  ArrowUp: '↑',
  ArrowDown: '↓',
  ArrowLeft: '←',
  ArrowRight: '→',
  Delete: 'Del',
};

/** Display form: `⌘C`, `⇧⌘V`, `⌥↑` on macOS; `Ctrl+C`, `Ctrl+Shift+V`, `Alt+↑` elsewhere. */
export function formatShortcut(shortcut: Shortcut): string {
  const key = KEY_LABELS[shortcut.key] ?? shortcut.key.toUpperCase();
  if (IS_MAC) {
    return `${shortcut.alt ? '⌥' : ''}${shortcut.shift ? '⇧' : ''}${shortcut.mod ? '⌘' : ''}${key}`;
  }
  return [shortcut.mod && 'Ctrl', shortcut.alt && 'Alt', shortcut.shift && 'Shift', key].filter(Boolean).join('+');
}

/**
 * Elements where keys belong to the focused control, so application
 * shortcuts must stay inactive: text fields, selects, rich text, the inline
 * cell editor (`data-inline-editor`), comboboxes/listboxes, open menus and
 * dialogs (a modal's own buttons and fields keep their keys).
 */
const TYPING_SELECTOR = [
  'input',
  'textarea',
  'select',
  '[contenteditable]:not([contenteditable="false"])',
  '[data-inline-editor]',
  '[role="combobox"]',
  '[role="listbox"]',
  '[role="menu"]',
  '[role="dialog"]',
].join(', ');

export function isShortcutBlockedTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  if (target instanceof HTMLElement && target.isContentEditable) return true;
  return target.closest(TYPING_SELECTOR) !== null;
}

/** True while the user has selected page text: Ctrl/Cmd+C/X then means "copy this text", not "copy the requirement". */
export function hasTextSelection(): boolean {
  const selection = typeof window !== 'undefined' ? window.getSelection() : null;
  return selection !== null && !selection.isCollapsed && selection.toString().trim().length > 0;
}
