import {
  ActionIcon,
  Alert,
  Button,
  Checkbox,
  CloseButton,
  ColorInput,
  Group,
  Modal,
  Select,
  Stack,
  Table,
  Text,
  Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconArrowDown, IconArrowUp, IconPlus, IconTrash } from '@tabler/icons-react';
import { useState } from 'react';
import { ApiError } from '../api/client';
import { useSetRowStyles } from '../api/queries';
import type { AttributeDefinition, Project, RowStyle } from '../api/types';
import { HEX_COLOR, effectiveRowStyles, rowStyleCss, type ColorScheme } from './rowStyles';

/** Suggested colors: the template defaults (green, blue) first, then a few readable ones. */
const SWATCHES = ['#2f9e44', '#1971c2', '#e03131', '#f08c00', '#7048e8', '#0c8599', '#fff3bf', '#e7f5ff', '#ebfbee', '#fff5f5'];

/** The dark-mode body and text colors (Mantine `dark-7`, `dark-0`), for the preview. */
const PREVIEW = {
  light: { background: '#ffffff', color: '#000000', border: 'var(--mantine-color-gray-3)' },
  dark: { background: '#242424', color: '#c9c9c9', border: 'var(--mantine-color-dark-4)' },
} as const;

interface DraftRule {
  /** Local key for React; not sent. */
  key: number;
  attribute_id: string | null;
  value: string | null;
  font_color: string;
  background_color: string;
  bold: boolean;
}

let nextKey = 1;

function toDraft(style: RowStyle): DraftRule {
  return {
    key: nextKey++,
    attribute_id: style.attribute_id,
    value: style.value,
    font_color: style.font_color ?? '',
    background_color: style.background_color ?? '',
    bold: style.bold,
  };
}

/** The problem with one draft rule (for its row), or null. Mirrors the server's checks. */
function draftProblem(rule: DraftRule, index: number, rules: DraftRule[]): string | null {
  if (!rule.attribute_id) return 'Pick an attribute';
  if (!rule.value) return 'Pick a value';
  if (rule.font_color && !HEX_COLOR.test(rule.font_color)) return 'Font color must be #rrggbb';
  if (rule.background_color && !HEX_COLOR.test(rule.background_color)) return 'Background must be #rrggbb';
  if (!rule.font_color && !rule.background_color && !rule.bold) return 'Set a color or bold';
  const duplicate = rules.findIndex((r) => r.attribute_id === rule.attribute_id && r.value === rule.value);
  if (duplicate !== index) return 'Duplicate of an earlier rule';
  return null;
}

function toRowStyle(rule: DraftRule): RowStyle {
  return {
    attribute_id: rule.attribute_id ?? '',
    value: rule.value ?? '',
    font_color: rule.font_color ? rule.font_color.toLowerCase() : null,
    background_color: rule.background_color ? rule.background_color.toLowerCase() : null,
    bold: rule.bold,
  };
}

/** One sample row in a given color scheme, styled as the rule says. */
function Preview({ rule, scheme }: { rule: DraftRule; scheme: ColorScheme }) {
  const base = PREVIEW[scheme];
  const style = draftProblem(rule, 0, [rule]) === null ? toRowStyle(rule) : null;
  return (
    <div
      title={`${scheme === 'light' ? 'Light' : 'Dark'} mode`}
      style={{
        background: base.background,
        color: base.color,
        border: `1px solid ${base.border}`,
        borderRadius: 4,
        padding: '2px 6px',
        fontSize: 'var(--mantine-font-size-xs)',
        whiteSpace: 'nowrap',
        ...rowStyleCss(style, scheme),
      }}
    >
      {rule.value ?? 'Sample'}
    </div>
  );
}

interface RowStylesModalProps {
  project: Project;
  opened: boolean;
  onClose: () => void;
}

/**
 * Edits the project's row styling rules: per enum attribute value, a font
 * color, a background color and bold, with a light/dark preview. Rules are
 * checked in order (the first match styles a row). Saved as a whole with
 * `PUT /projects/{id}/row-styles`.
 */
export function RowStylesModal({ project, opened, onClose }: RowStylesModalProps) {
  return (
    <Modal opened={opened} onClose={onClose} title="Row styles" size="xl" centered>
      {opened && <RowStylesForm project={project} onClose={onClose} />}
    </Modal>
  );
}

function RowStylesForm({ project, onClose }: { project: Project; onClose: () => void }) {
  const setRowStyles = useSetRowStyles(project.id);
  const enumAttributes = project.attributes.filter((a) => a.type === 'enum' && !a.system);
  const stored = project.row_styles ?? [];
  const usable = effectiveRowStyles(stored, project.attributes);
  const [rules, setRules] = useState<DraftRule[]>(() => usable.map(toDraft));
  const [error, setError] = useState<string | null>(null);
  const busy = setRowStyles.isPending;
  const droppedCount = stored.length - usable.length;

  const update = (key: number, patch: Partial<DraftRule>) =>
    setRules((prev) => prev.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  const move = (index: number, delta: number) =>
    setRules((prev) => {
      const next = [...prev];
      const [rule] = next.splice(index, 1);
      next.splice(index + delta, 0, rule);
      return next;
    });
  const addRule = () => {
    const attr = enumAttributes.find((a) => a.id === 'object_type') ?? enumAttributes[0];
    setRules((prev) => [
      ...prev,
      { key: nextKey++, attribute_id: attr?.id ?? null, value: null, font_color: '', background_color: '', bold: false },
    ]);
  };

  const problems = rules.map((rule, index) => draftProblem(rule, index, rules));
  const canSave = problems.every((p) => p === null) && !busy;

  const handleSave = () => {
    setError(null);
    setRowStyles.mutate(rules.map(toRowStyle), {
      onSuccess: () => {
        notifications.show({ message: 'Row styles saved', color: 'green' });
        onClose();
      },
      onError: (err) => setError(err instanceof ApiError ? err.message : 'Failed to save row styles'),
    });
  };

  const choicesOf = (attrId: string | null): string[] =>
    enumAttributes.find((a: AttributeDefinition) => a.id === attrId)?.choices ?? [];

  const colorInput = (rule: DraftRule, field: 'font_color' | 'background_color', label: string) => (
    <ColorInput
      size="xs"
      w={130}
      aria-label={label}
      placeholder="None"
      format="hex"
      swatches={SWATCHES}
      fixOnBlur={false}
      value={rule[field]}
      onChange={(value) => update(rule.key, { [field]: value })}
      disabled={busy}
      rightSection={
        rule[field] ? (
          <CloseButton size="xs" aria-label={`Clear ${label.toLowerCase()}`} onClick={() => update(rule.key, { [field]: '' })} />
        ) : undefined
      }
    />
  );

  return (
    <Stack gap="sm">
      <Text size="sm" c="dimmed">
        Rows whose attribute has the value get these colors and weight in the requirements table, the outline,
        baselines and the Word export. The first matching rule wins. In dark mode, colors are adjusted so text stays
        readable; the Word export uses them as given.
      </Text>
      {droppedCount > 0 && (
        <Alert color="yellow" p="xs">
          <Text size="sm">
            {droppedCount} stored rule(s) name an attribute or value that no longer exists; they are ignored and
            will be removed when you save.
          </Text>
        </Alert>
      )}
      {enumAttributes.length === 0 ? (
        <Text size="sm" c="dimmed">
          This project has no enum attributes. Add one (for example "Object Type") to style rows by its values.
        </Text>
      ) : rules.length === 0 ? (
        <Text size="sm" c="dimmed">
          No row styles yet.
        </Text>
      ) : (
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Attribute</Table.Th>
              <Table.Th>Value</Table.Th>
              <Table.Th>Font</Table.Th>
              <Table.Th>Background</Table.Th>
              <Table.Th>Bold</Table.Th>
              <Table.Th>Preview</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rules.map((rule, index) => (
              <Table.Tr key={rule.key}>
                <Table.Td>
                  <Select
                    size="xs"
                    w={140}
                    aria-label="Attribute"
                    data={enumAttributes.map((a) => ({ value: a.id, label: a.name }))}
                    value={rule.attribute_id}
                    onChange={(value) => update(rule.key, { attribute_id: value, value: null })}
                    allowDeselect={false}
                    disabled={busy}
                  />
                </Table.Td>
                <Table.Td>
                  <Select
                    size="xs"
                    w={140}
                    aria-label="Value"
                    placeholder="Pick a value"
                    data={choicesOf(rule.attribute_id)}
                    value={rule.value}
                    onChange={(value) => update(rule.key, { value })}
                    allowDeselect={false}
                    disabled={busy || !rule.attribute_id}
                    error={problems[index] ?? undefined}
                  />
                </Table.Td>
                <Table.Td>{colorInput(rule, 'font_color', 'Font color')}</Table.Td>
                <Table.Td>{colorInput(rule, 'background_color', 'Background color')}</Table.Td>
                <Table.Td>
                  <Checkbox
                    aria-label="Bold"
                    checked={rule.bold}
                    onChange={(event) => update(rule.key, { bold: event.currentTarget.checked })}
                    disabled={busy}
                  />
                </Table.Td>
                <Table.Td>
                  <Stack gap={2}>
                    <Preview rule={rule} scheme="light" />
                    <Preview rule={rule} scheme="dark" />
                  </Stack>
                </Table.Td>
                <Table.Td>
                  <Group gap={0} wrap="nowrap">
                    <Tooltip label="Move up">
                      <ActionIcon
                        variant="subtle"
                        size="sm"
                        aria-label="Move rule up"
                        disabled={busy || index === 0}
                        onClick={() => move(index, -1)}
                      >
                        <IconArrowUp size={14} />
                      </ActionIcon>
                    </Tooltip>
                    <Tooltip label="Move down">
                      <ActionIcon
                        variant="subtle"
                        size="sm"
                        aria-label="Move rule down"
                        disabled={busy || index === rules.length - 1}
                        onClick={() => move(index, 1)}
                      >
                        <IconArrowDown size={14} />
                      </ActionIcon>
                    </Tooltip>
                    <Tooltip label="Remove">
                      <ActionIcon
                        variant="subtle"
                        color="red"
                        size="sm"
                        aria-label="Remove rule"
                        disabled={busy}
                        onClick={() => setRules((prev) => prev.filter((r) => r.key !== rule.key))}
                      >
                        <IconTrash size={14} />
                      </ActionIcon>
                    </Tooltip>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      {error && (
        <Text size="sm" c="red">
          {error}
        </Text>
      )}
      <Group justify="space-between" mt="sm">
        <Button
          variant="default"
          size="xs"
          leftSection={<IconPlus size={14} />}
          onClick={addRule}
          disabled={busy || enumAttributes.length === 0}
        >
          Add rule
        </Button>
        <Group gap="xs">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleSave} loading={busy} disabled={!canSave}>
            Save
          </Button>
        </Group>
      </Group>
    </Stack>
  );
}
