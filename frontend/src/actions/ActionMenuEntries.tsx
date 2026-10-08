import { Menu, Text } from '@mantine/core';
import { IconCheck } from '@tabler/icons-react';
import { useEffect, useRef, type KeyboardEvent, type ReactNode } from 'react';
import { useAppActions } from './context';
import { contextFor, getAction, isActionAvailable, isSupported } from './registry';
import { formatShortcut } from './shortcuts';
import type { Action, ActionIcon, ActionTargetOverrides, AppState, MenuEntry } from './types';

const ICON_SIZE = 14;

function iconSection(Icon: ActionIcon | undefined) {
  return Icon ? <Icon size={ICON_SIZE} /> : undefined;
}

/** Keeps labels aligned with the iconed items when a toggle is off. */
function checkSection(checked: boolean) {
  return checked ? <IconCheck size={ICON_SIZE} /> : <span style={{ display: 'inline-block', width: ICON_SIZE }} />;
}

function menuLabel(action: Action): string {
  return action.opensDialog ? `${action.label}…` : action.label;
}

/**
 * A submenu whose item gets focus back when ArrowLeft closes the submenu.
 * Works around Mantine 9.6: its `focusParentItem` looks up the element
 * `${id}-target`, but the submenu item's id is `${id}`, so focus would drop
 * to `<body>`. Runs after Mantine's 16 ms timer, so it is harmless once
 * Mantine finds the item itself.
 */
function SubMenu({ label, leftSection, children }: { label: ReactNode; leftSection: ReactNode; children: ReactNode }) {
  const itemRef = useRef<HTMLButtonElement>(null);
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ArrowLeft') return;
    // Only this submenu's own items; a nested submenu closes and refocuses itself.
    if ((event.target as Element).closest('[data-menu-dropdown]') !== event.currentTarget) return;
    window.setTimeout(() => itemRef.current?.focus(), 20);
  };
  return (
    <Menu.Sub>
      <Menu.Sub.Target>
        <Menu.Sub.Item ref={itemRef} leftSection={leftSection}>
          {label}
        </Menu.Sub.Item>
      </Menu.Sub.Target>
      <Menu.Sub.Dropdown onKeyDown={onKeyDown}>{children}</Menu.Sub.Dropdown>
    </Menu.Sub>
  );
}

/** Calls `onShown` once when mounted: submenu dropdowns mount only while open. */
function ShownSignal({ onShown }: { onShown: () => void }) {
  const onShownRef = useRef(onShown);
  useEffect(() => {
    onShownRef.current();
  }, []);
  return null;
}

/**
 * The layout without the entries the targets do not support: unsupported
 * actions, submenus left empty, and leading, trailing or repeated dividers.
 */
function supportedEntries(list: MenuEntry[], state: AppState, targets?: ActionTargetOverrides): MenuEntry[] {
  const kept: MenuEntry[] = [];
  for (const entry of list) {
    if (entry.kind === 'action') {
      const action = getAction(entry.id);
      if (isSupported(action, contextFor(action, state, targets))) kept.push(entry);
    } else if (entry.kind === 'submenu') {
      const entries = supportedEntries(entry.entries, state, targets);
      if (entries.length > 0) kept.push({ ...entry, entries });
    } else if (kept.length > 0 && kept[kept.length - 1].kind !== 'divider') {
      kept.push(entry);
    }
  }
  while (kept.length > 0 && kept[kept.length - 1].kind === 'divider') kept.pop();
  return kept;
}

/**
 * Renders a menu layout (`MenuEntry[]`) as Mantine menu items, inside a
 * `Menu.Dropdown`. Every item runs its registry action; availability,
 * check marks and shortcut hints come from the registry too, so the menu
 * bar and context menus can never disagree. `targets` holds the clicked
 * element of a context menu keyed by element type (e.g. `{ requirement: row }`),
 * so each action gets a target of its own element type; actions without an
 * entry (and the whole menu bar) use their default target. Actions the
 * targets do not support are hidden; unavailable ones are disabled.
 */
export function ActionMenuEntries({ entries, targets }: { entries: MenuEntry[]; targets?: ActionTargetOverrides }) {
  const { state, run } = useAppActions();

  const hasAvailableEntry = (list: MenuEntry[]): boolean =>
    list.some((entry) => {
      if (entry.kind === 'divider') return false;
      if (entry.kind === 'submenu') return hasAvailableEntry(entry.entries);
      const action = getAction(entry.id);
      return isActionAvailable(action, contextFor(action, state, targets));
    });

  const renderAction = (id: string, key: string) => {
    const action = getAction(id);
    const ctx = contextFor(action, state, targets);
    const available = isActionAvailable(action, ctx);
    const shortcut = action.shortcuts?.[0];
    const leftSection = action.isChecked ? checkSection(action.isChecked(ctx)) : iconSection(action.icon);

    if (action.items && available) {
      const items = action.items(ctx);
      return (
        <SubMenu key={key} label={action.label} leftSection={leftSection}>
          {action.onItemsShown && <ShownSignal onShown={() => action.onItemsShown?.(ctx)} />}
          {items.map((item) => (
            <Menu.Item
              key={item.key}
              leftSection={item.checked !== undefined ? checkSection(item.checked) : undefined}
              disabled={item.disabled}
              title={item.description}
              onClick={item.run}
            >
              {item.label}
            </Menu.Item>
          ))}
        </SubMenu>
      );
    }

    return (
      <Menu.Item
        key={key}
        leftSection={leftSection}
        rightSection={
          shortcut ? (
            <Text size="xs" c="dimmed">
              {formatShortcut(shortcut)}
            </Text>
          ) : undefined
        }
        color={action.color}
        disabled={!available}
        onClick={() => run(action.id, targets)}
      >
        {menuLabel(action)}
      </Menu.Item>
    );
  };

  const renderEntries = (list: MenuEntry[], prefix: string): ReactNode[] =>
    list.map((entry, index) => {
      const key = `${prefix}${index}`;
      if (entry.kind === 'divider') return <Menu.Divider key={key} />;
      if (entry.kind === 'action') return renderAction(entry.id, key);
      if (!hasAvailableEntry(entry.entries)) {
        return (
          <Menu.Item key={key} leftSection={iconSection(entry.icon)} disabled>
            {entry.label}
          </Menu.Item>
        );
      }
      return (
        <SubMenu key={key} label={entry.label} leftSection={iconSection(entry.icon)}>
          {renderEntries(entry.entries, `${key}.`)}
        </SubMenu>
      );
    });

  return <>{renderEntries(supportedEntries(entries, state, targets), '')}</>;
}
