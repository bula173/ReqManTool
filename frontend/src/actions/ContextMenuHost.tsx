import { Menu } from '@mantine/core';
import { useEffect, useRef } from 'react';
import { ActionMenuEntries } from './ActionMenuEntries';
import type { MenuRequest } from './types';

/**
 * The one context menu of the app (`AppActionsValue.openMenu`): a registry
 * menu layout shown at a screen position. Mantine closes it on Escape, an
 * outside click and an item click; focus then goes back to the element that
 * had it when the menu opened (the row, tree node or header the menu belongs
 * to) unless something else (a dialog) took it.
 */
export function ContextMenuHost({ request, onClose }: { request: MenuRequest | null; onClose: () => void }) {
  const returnFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!request) return;
    const active = document.activeElement;
    returnFocusRef.current = active instanceof HTMLElement && active !== document.body ? active : null;
  }, [request]);

  const close = () => {
    onClose();
    const element = returnFocusRef.current;
    returnFocusRef.current = null;
    window.setTimeout(() => {
      const active = document.activeElement;
      if (element?.isConnected && (active === null || active === document.body || !active.isConnected)) {
        element.focus();
      }
    }, 10);
  };

  return (
    <Menu
      // A new request (another element right-clicked) starts a fresh menu at its position.
      key={request ? `${request.x},${request.y}` : 'closed'}
      opened={request !== null}
      onClose={close}
      withinPortal
      shadow="md"
      position="bottom-start"
      returnFocus={false}
    >
      <Menu.Target>
        <div
          aria-hidden
          style={{ position: 'fixed', top: request?.y ?? 0, left: request?.x ?? 0, width: 1, height: 1 }}
        />
      </Menu.Target>
      <Menu.Dropdown miw={220}>
        {request?.label && <Menu.Label>{request.label}</Menu.Label>}
        {request && <ActionMenuEntries entries={request.entries} targets={request.targets} />}
      </Menu.Dropdown>
    </Menu>
  );
}
