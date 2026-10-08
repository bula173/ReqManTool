import { Button, Group, Menu } from '@mantine/core';
import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { ActionMenuEntries } from '../actions/ActionMenuEntries';
import { MENU_BAR } from '../actions/registry';
import { isShortcutBlockedTarget } from '../actions/shortcuts';

/** Keys inside a dialog belong to the dialog. */
function inDialog(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest('[role="dialog"]') !== null;
}

/**
 * The desktop-style application menu bar (File / Edit / View / Help). Every
 * entry is a registry action (`MENU_BAR` in `actions/registry.ts`), so it
 * shares availability, labels, shortcuts and behaviour with the context
 * menus and keyboard shortcuts.
 *
 * Keyboard (WAI-ARIA menubar pattern): the bar is one tab stop (roving
 * tabindex); ArrowLeft/ArrowRight move between the top-level menus, Home/End
 * to the first/last, ArrowDown opens the focused menu. With a menu open,
 * ArrowLeft/ArrowRight open the adjacent menu (ArrowRight on a submenu item
 * opens the submenu instead), and Escape closes it, returning to its title.
 *
 * Reaching the bar (desktop convention): F10, or pressing and releasing Alt
 * alone, moves focus to the first menu title; again (or Escape on a title)
 * gives focus back to where it was. Inactive inside dialogs; the Alt tap is
 * also inactive while typing in a field.
 */
export function MenuBar() {
  const count = MENU_BAR.length;
  const [openIndex, setOpenIndex] = useState<number | null>(null);
  const [focusIndex, setFocusIndex] = useState(0);
  const triggerRefs = useRef<(HTMLButtonElement | null)[]>([]);
  // Submenu dropdowns are not portalled, so they are inside these elements.
  const dropdownRefs = useRef<(HTMLDivElement | null)[]>([]);
  const barRef = useRef<HTMLDivElement>(null);
  // Where focus was before F10 / Alt moved it to the bar.
  const previousFocusRef = useRef<HTMLElement | null>(null);

  const focusTrigger = (index: number): number => {
    const next = (index + count) % count;
    setFocusIndex(next);
    triggerRefs.current[next]?.focus();
    return next;
  };

  /** Opens the adjacent menu; the closing one must not pull focus back to its title. */
  const switchMenu = (from: number, delta: number) => {
    setOpenIndex(focusTrigger(from + delta));
  };

  /** Gives focus back to where it was before the bar was reached by keyboard. */
  const leaveBar = () => {
    const previous = previousFocusRef.current;
    previousFocusRef.current = null;
    if (previous?.isConnected) previous.focus();
    else (document.activeElement as HTMLElement | null)?.blur();
  };

  /** F10 / Alt tap: focus the bar's first menu title, or leave the bar if it has focus. */
  const toggleBarFocus = () => {
    const active = document.activeElement;
    if (active && barRef.current?.contains(active)) {
      setOpenIndex(null);
      leaveBar();
      return;
    }
    previousFocusRef.current = active instanceof HTMLElement && active !== document.body ? active : null;
    focusTrigger(0);
  };
  const toggleBarFocusRef = useRef(toggleBarFocus);
  useEffect(() => {
    toggleBarFocusRef.current = toggleBarFocus;
  });

  useEffect(() => {
    // Alt pressed and released with nothing else in between.
    let altArmed = false;
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Alt') {
        altArmed = !event.repeat && !event.ctrlKey && !event.metaKey && !event.shiftKey;
        return;
      }
      altArmed = false;
      if (event.key !== 'F10' || event.shiftKey || event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.defaultPrevented || inDialog(event.target)) return;
      event.preventDefault();
      toggleBarFocusRef.current();
    };
    const onKeyUp = (event: globalThis.KeyboardEvent) => {
      if (event.key !== 'Alt' || !altArmed) return;
      altArmed = false;
      const target = event.target;
      const onBar = target instanceof Element && barRef.current?.contains(target);
      if (inDialog(target) || (!onBar && isShortcutBlockedTarget(target))) return;
      event.preventDefault();
      toggleBarFocusRef.current();
    };
    const disarm = () => {
      altArmed = false;
    };
    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);
    window.addEventListener('mousedown', disarm, true);
    window.addEventListener('blur', disarm);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('keyup', onKeyUp);
      window.removeEventListener('mousedown', disarm, true);
      window.removeEventListener('blur', disarm);
    };
  }, []);

  const onTriggerKeyDown = (index: number, event: KeyboardEvent<HTMLButtonElement>) => {
    // A modified key (e.g. Alt+ArrowDown = Move Down) is an app shortcut, not menu navigation.
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    switch (event.key) {
      case 'Escape':
        // No menu is open (Mantine handles Escape inside an open one): leave the bar.
        if (openIndex === null) {
          event.preventDefault();
          leaveBar();
        }
        break;
      case 'ArrowRight':
      case 'ArrowLeft': {
        event.preventDefault();
        const next = focusTrigger(index + (event.key === 'ArrowRight' ? 1 : -1));
        if (openIndex !== null) setOpenIndex(next);
        break;
      }
      case 'Home':
      case 'End':
        event.preventDefault();
        focusTrigger(event.key === 'Home' ? 0 : count - 1);
        break;
      case 'ArrowDown':
        event.preventDefault();
        setOpenIndex(index);
        break;
    }
  };

  const onDropdownKeyDown = (index: number, event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    const target = event.target as Element;
    // Only keys of this menu's own items: submenus handle their arrows.
    if (target.closest('[role="menu"]') !== event.currentTarget) return;
    if (event.key === 'ArrowRight' && target.closest('[data-sub-menu-item]')) return;
    event.preventDefault();
    switchMenu(index, event.key === 'ArrowRight' ? 1 : -1);
  };

  /**
   * Mantine closed menu `index` (Escape, item click, click outside): like its
   * own `returnFocus`, put focus back on the menu title unless something else
   * (a dialog, the clicked control, another menu) took it. Focus still inside
   * the closing menu's (fading) dropdown or one of its submenus, or on an
   * element already removed, counts as not taken: it would drop to `<body>`
   * once the dropdown unmounts (e.g. Escape in a submenu). Done here rather
   * than with `returnFocus` so that switching menus with the arrow keys never
   * does it.
   */
  const returnFocusAfterClose = (index: number) => {
    const activeAtClose = document.activeElement;
    window.setTimeout(() => {
      const active = document.activeElement;
      if (
        active === null ||
        active === document.body ||
        active === activeAtClose ||
        !active.isConnected ||
        dropdownRefs.current[index]?.contains(active)
      ) {
        triggerRefs.current[index]?.focus();
      }
    }, 10);
  };

  return (
    <Group ref={barRef} gap={0} component="nav" aria-label="Application menu (F10)" role="menubar" wrap="nowrap">
      {MENU_BAR.map((menu, index) => (
        <Menu
          key={menu.label}
          position="bottom-start"
          shadow="md"
          width={260}
          offset={2}
          opened={openIndex === index}
          onChange={(opened) => {
            if (opened) {
              setOpenIndex(index);
              setFocusIndex(index);
            } else {
              setOpenIndex((current) => (current === index ? null : current));
              returnFocusAfterClose(index);
            }
          }}
          returnFocus={false}
        >
          <Menu.Target>
            <Button
              ref={(node) => {
                triggerRefs.current[index] = node;
              }}
              variant="subtle"
              color="gray"
              size="compact-sm"
              fw={500}
              role="menuitem"
              aria-haspopup="menu"
              tabIndex={index === focusIndex ? 0 : -1}
              onFocus={() => setFocusIndex(index)}
              onKeyDown={(event) => onTriggerKeyDown(index, event)}
            >
              {menu.label}
            </Button>
          </Menu.Target>
          <Menu.Dropdown
            ref={(node) => {
              dropdownRefs.current[index] = node;
            }}
            onKeyDown={(event) => onDropdownKeyDown(index, event)}
          >
            <ActionMenuEntries entries={menu.entries} />
          </Menu.Dropdown>
        </Menu>
      ))}
    </Group>
  );
}
