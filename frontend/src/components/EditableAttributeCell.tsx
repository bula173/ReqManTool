import { Badge, Checkbox, Group, NumberInput, Select, Text, Textarea, TextInput, Tooltip } from '@mantine/core';
import { IconCheck } from '@tabler/icons-react';
import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent, Ref } from 'react';
import { useFindRequirement } from '../api/queries';
import type { AttributeDefinition } from '../api/types';

interface EditableAttributeCellProps {
  attr: AttributeDefinition;
  value: unknown;
  onSave: (value: unknown) => void;
  disabled?: boolean;
  projectId?: string;
  onNavigateToLinked?: (targetId: string) => void;
  /**
   * Table column display mode: true wraps the full value inside the column
   * width, false keeps it on one line (truncated). Omitted: the compact
   * default (text clamped to two lines).
   */
  wrap?: boolean;
}

function LinkBadge({
  entry,
  projectId,
  onNavigate,
}: {
  entry: string;
  projectId?: string;
  onNavigate?: (targetId: string) => void;
}) {
  const [hovered, setHovered] = useState(false);
  const outgoing = entry.startsWith('>');
  const targetId = entry.slice(1).trim();
  const findQuery = useFindRequirement(projectId, targetId, hovered);
  const tooltipLabel = findQuery.data
    ? String(findQuery.data.requirement.attributes.object_text ?? targetId)
    : findQuery.isFetching
      ? 'Loading…'
      : targetId;

  return (
    <Tooltip label={tooltipLabel} openDelay={300}>
      <Badge
        variant="light"
        color={outgoing ? 'green' : 'blue'}
        onMouseEnter={() => setHovered(true)}
        onDoubleClick={(e) => {
          e.stopPropagation();
          onNavigate?.(targetId);
        }}
        style={{ cursor: onNavigate ? 'pointer' : undefined }}
      >
        {entry}
      </Badge>
    </Tooltip>
  );
}

function LinkEntries({
  value,
  projectId,
  onNavigate,
  wrap,
}: {
  value: unknown;
  projectId?: string;
  onNavigate?: (targetId: string) => void;
  wrap?: boolean;
}) {
  if (value === null || value === undefined || value === '') {
    return <Text c="dimmed">—</Text>;
  }
  const entries = String(value)
    .split(';')
    .map((e) => e.trim())
    .filter(Boolean);
  return (
    <Group gap={4} wrap={wrap === false ? 'nowrap' : 'wrap'} style={wrap === false ? { overflow: 'hidden' } : undefined}>
      {entries.map((entry, idx) => (
        <LinkBadge key={idx} entry={entry} projectId={projectId} onNavigate={onNavigate} />
      ))}
    </Group>
  );
}

function DisplayValue({
  attr,
  value,
  projectId,
  onNavigate,
  wrap,
}: {
  attr: AttributeDefinition;
  value: unknown;
  projectId?: string;
  onNavigate?: (targetId: string) => void;
  wrap?: boolean;
}) {
  if (attr.system) {
    return <LinkEntries value={value} projectId={projectId} onNavigate={onNavigate} wrap={wrap} />;
  }
  if (value === null || value === undefined || value === '') {
    return <Text c="dimmed">—</Text>;
  }
  if (attr.type === 'boolean') {
    return value ? <IconCheck size={16} color="var(--mantine-color-green-6)" /> : <Text c="dimmed">—</Text>;
  }
  if (attr.type === 'enum') {
    return <Badge variant="light">{String(value)}</Badge>;
  }
  if (wrap === false) {
    return (
      <Text size="sm" truncate="end" title={String(value)}>
        {String(value)}
      </Text>
    );
  }
  if (wrap === true) {
    return (
      <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
        {String(value)}
      </Text>
    );
  }
  if (attr.type === 'text') {
    return (
      <Text size="sm" lineClamp={2} maw={280}>
        {String(value)}
      </Text>
    );
  }
  return <Text size="sm">{String(value)}</Text>;
}

function coerceValue(attr: AttributeDefinition, raw: unknown): unknown {
  if (attr.type === 'integer') {
    if (raw === '' || raw === null || raw === undefined) return null;
    // NumberInput reports a partial entry such as "-" as a string; send numbers only.
    const n = typeof raw === 'number' ? raw : Number(raw);
    return Number.isNaN(n) ? null : n;
  }
  return raw;
}

export function EditableAttributeCell({
  attr,
  value,
  onSave,
  disabled,
  projectId,
  onNavigateToLinked,
  wrap,
}: EditableAttributeCellProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<unknown>(value);
  const inputRef = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (editing) setDraft(value ?? (attr.type === 'boolean' ? false : ''));
  }, [editing, value, attr.type]);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  if (disabled || attr.system) {
    return (
      <DisplayValue attr={attr} value={value} projectId={projectId} onNavigate={onNavigateToLinked} wrap={wrap} />
    );
  }

  const commit = () => {
    setEditing(false);
    const coerced = coerceValue(attr, draft);
    if (coerced !== value) onSave(coerced);
  };

  const cancel = () => setEditing(false);

  if (!editing) {
    return (
      <div onDoubleClick={() => setEditing(true)} style={{ cursor: 'pointer', minHeight: 22 }}>
        <DisplayValue attr={attr} value={value} wrap={wrap} />
      </div>
    );
  }

  const commonKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Escape') cancel();
    if (e.key === 'Enter' && attr.type !== 'text') commit();
  };

  // `data-inline-editor` marks the open editor so application keyboard
  // shortcuts (Ctrl/Cmd+C/X/V, Alt+arrows) stay inactive while it has focus.
  const renderEditor = () => {
    switch (attr.type) {
      case 'text':
        return (
          <Textarea
            ref={inputRef as Ref<HTMLTextAreaElement>}
            autosize
            minRows={2}
            value={String(draft ?? '')}
            onChange={(e) => setDraft(e.currentTarget.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === 'Escape') cancel();
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) commit();
            }}
          />
        );
      case 'integer':
        return (
          <NumberInput
            ref={inputRef as Ref<HTMLInputElement>}
            value={draft === null || draft === undefined ? '' : (draft as number | string)}
            onChange={setDraft}
            allowDecimal={false}
            onBlur={commit}
            onKeyDown={commonKeyDown}
          />
        );
      case 'boolean':
        return (
          <Checkbox
            ref={inputRef as Ref<HTMLInputElement>}
            checked={Boolean(draft)}
            onChange={(e) => {
              const next = e.currentTarget.checked;
              setDraft(next);
              onSave(next);
              setEditing(false);
            }}
            onBlur={cancel}
          />
        );
      case 'enum':
        return (
          <Select
            data={attr.choices ?? []}
            value={draft === null || draft === undefined ? null : String(draft)}
            onChange={(next) => {
              setDraft(next);
              onSave(next);
              setEditing(false);
            }}
            onDropdownClose={cancel}
            clearable={!attr.required}
          />
        );
      default:
        return (
          <TextInput
            ref={inputRef as Ref<HTMLInputElement>}
            value={String(draft ?? '')}
            onChange={(e) => setDraft(e.currentTarget.value)}
            onBlur={commit}
            onKeyDown={commonKeyDown}
          />
        );
    }
  };

  return <div data-inline-editor="">{renderEditor()}</div>;
}
