import { Kbd, Modal, Stack, Table, Text } from '@mantine/core';
import { actionsWithShortcuts } from '../actions/registry';
import { formatShortcut } from '../actions/shortcuts';

interface KeyboardShortcutsModalProps {
  opened: boolean;
  onClose: () => void;
}

/** Help ▸ Keyboard shortcuts, generated from the action registry so it never drifts from the real bindings. */
export function KeyboardShortcutsModal({ opened, onClose }: KeyboardShortcutsModalProps) {
  const actions = actionsWithShortcuts();

  return (
    <Modal opened={opened} onClose={onClose} title="Keyboard shortcuts" centered size="lg">
      <Stack gap="sm">
        <Table striped>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Action</Table.Th>
              <Table.Th>Shortcut</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {actions.map((action) => (
              <Table.Tr key={action.id}>
                <Table.Td>{action.label}</Table.Td>
                <Table.Td>
                  {action.shortcuts!.map((shortcut, index) => (
                    <span key={index}>
                      {index > 0 && <Text span size="xs" c="dimmed">{' or '}</Text>}
                      <Kbd>{formatShortcut(shortcut)}</Kbd>
                      {shortcut.whenNoTarget && (
                        <Text span size="xs" c="dimmed">
                          {' (nothing selected)'}
                        </Text>
                      )}
                    </span>
                  ))}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        <Text size="xs" c="dimmed">
          Edit shortcuts act on the selected requirement of the open module. Ctrl/Cmd+V pastes as a child of the
          selected requirement (not onto itself), or at the module root only when none is selected. Shortcuts are inactive while a text
          field, a select, the inline cell editor, a menu or a dialog has the focus, and Ctrl/Cmd+C/X copy selected
          page text as usual. Double-click a cell to edit it in place (Enter or leaving the cell saves, Escape
          cancels).
        </Text>
      </Stack>
    </Modal>
  );
}
