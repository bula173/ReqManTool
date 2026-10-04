import { Button, Group, Menu } from '@mantine/core';
import { useRef, useState, type KeyboardEvent } from 'react';
import { ActionMenuEntries } from '../actions/ActionMenuEntries';
import { MENU_BAR } from '../actions/registry';

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
 */
export function MenuBar() {
  const count = MENU_BAR.length;
  const [openIndex, setOpenIndex] = useState<number | null>(null);
  const [focusIndex, setFocusIndex] = useState(0);
  const triggerRefs = useRef<(HTMLButtonElement | null)[]>([]);
  // Submenu dropdowns are not portalled, so they are inside these elements.
  const dropdownRefs = useRef<(HTMLDivElement | null)[]>([]);

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

  const onTriggerKeyDown = (index: number, event: KeyboardEvent<HTMLButtonElement>) => {
    // A modified key (e.g. Alt+ArrowDown = Move Down) is an app shortcut, not menu navigation.
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    switch (event.key) {
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
    <Group gap={0} component="nav" aria-label="Application menu" role="menubar" wrap="nowrap">
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
