import { Badge, Checkbox, Group, NumberInput, Select, Text, TextInput, Tooltip } from '@mantine/core';
import { IconCheck } from '@tabler/icons-react';
import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent, Ref } from 'react';
import { parseLinkValue, type LinkEntry } from '../actions/links';
import { useFindRequirement } from '../api/queries';
import type { AttributeDefinition } from '../api/types';
import { LazyRichTextField } from './LazyRichTextField';
import { isRichText, richTextToPlain } from './richText';
import { RichTextView } from './RichTextView';

interface EditableAttributeCellProps {
  attr: AttributeDefinition;
  value: unknown;
  onSave: (value: unknown) => void;
  disabled?: boolean;
  projectId?: string;
  onNavigateToLinked?: (targetId: string) => void;
  /**
   * Opens a link badge's menu: `navigate` on left click, `edit` on right
   * click (the requirements table). Without it, badges only navigate on
   * double-click.
   */
  onLinkMenu?: LinkMenuHandler;
  /**
   * Table column display mode: true wraps the full value inside the column
   * width, false keeps it on one line (truncated). Omitted: the compact
   * default (text clamped to two lines).
   */
  wrap?: boolean;
}

type LinkMenuHandler = (link: LinkEntry, kind: 'navigate' | 'edit', position: { x: number; y: number }) => void;

function LinkBadge({
  link,
  projectId,
  onNavigate,
  onLinkMenu,
}: {
  link: LinkEntry;
  projectId?: string;
  onNavigate?: (targetId: string) => void;
  onLinkMenu?: LinkMenuHandler;
}) {
  const [hovered, setHovered] = useState(false);
  const outgoing = link.direction === 'out';
  const targetId = link.otherId;
  const findQuery = useFindRequirement(projectId, targetId, hovered);
  const tooltipLabel = findQuery.data
    ? richTextToPlain(findQuery.data.requirement.attributes.object_text ?? targetId)
    : findQuery.isFetching
      ? 'Loading…'
      : targetId;

  return (
    <Tooltip label={tooltipLabel} openDelay={300}>
      <Badge
        variant="light"
        color={outgoing ? 'green' : 'blue'}
        onMouseEnter={() => setHovered(true)}
        onClick={
          onLinkMenu ? (e) => onLinkMenu(link, 'navigate', { x: e.clientX, y: e.clientY }) : undefined
        }
        onContextMenu={
          onLinkMenu
            ? (e) => {
                // The badge's own menu, not the row's.
                e.preventDefault();
                e.stopPropagation();
                onLinkMenu(link, 'edit', { x: e.clientX, y: e.clientY });
              }
            : undefined
        }
        onDoubleClick={(e) => {
          e.stopPropagation();
          onNavigate?.(targetId);
        }}
        style={{ cursor: onNavigate || onLinkMenu ? 'pointer' : undefined }}
      >
        {outgoing ? '>' : '<'} {targetId}
      </Badge>
    </Tooltip>
  );
}

function LinkEntries({
  value,
  projectId,
  onNavigate,
  onLinkMenu,
  wrap,
}: {
  value: unknown;
  projectId?: string;
  onNavigate?: (targetId: string) => void;
  onLinkMenu?: LinkMenuHandler;
  wrap?: boolean;
}) {
  const entries = parseLinkValue(value === null || value === undefined ? '' : String(value));
  if (entries.length === 0) {
    return <Text c="dimmed">—</Text>;
  }
  return (
    <Group gap={4} wrap={wrap === false ? 'nowrap' : 'wrap'} style={wrap === false ? { overflow: 'hidden' } : undefined}>
      {entries.map((link, idx) => (
        <LinkBadge key={idx} link={link} projectId={projectId} onNavigate={onNavigate} onLinkMenu={onLinkMenu} />
      ))}
    </Group>
  );
}

function DisplayValue({
  attr,
  value,
  projectId,
  onNavigate,
  onLinkMenu,
  wrap,
}: {
  attr: AttributeDefinition;
  value: unknown;
  projectId?: string;
  onNavigate?: (targetId: string) => void;
  onLinkMenu?: LinkMenuHandler;
  wrap?: boolean;
}) {
  if (attr.system) {
    return (
      <LinkEntries value={value} projectId={projectId} onNavigate={onNavigate} onLinkMenu={onLinkMenu} wrap={wrap} />
    );
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
  if (attr.type === 'text' && isRichText(value)) {
    return <RichTextView value={value} wrap={wrap} />;
  }
  if (attr.type === 'date') {
    return (
      <Text size="sm" style={{ whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>
        {String(value)}
      </Text>
    );
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
  if (attr.type === 'date') {
    // The native date input reports '' while empty or incomplete.
    return raw === '' || raw === undefined ? null : raw;
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
  onLinkMenu,
  wrap,
}: EditableAttributeCellProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<unknown>(value);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (editing) setDraft(value ?? (attr.type === 'boolean' ? false : ''));
  }, [editing, value, attr.type]);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  if (disabled || attr.system) {
    return (
      <DisplayValue
        attr={attr}
        value={value}
        projectId={projectId}
        onNavigate={onNavigateToLinked}
        onLinkMenu={onLinkMenu}
        wrap={wrap}
      />
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
        // Rich text (TipTap): Esc cancels, Ctrl/Cmd+Enter or a click/Tab outside saves.
        return (
          <LazyRichTextField
            value={value}
            onChange={setDraft}
            autoFocus
            onCommit={commit}
            onCancel={cancel}
            aria-label={attr.name}
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
      case 'date':
        // Native date picker; the value is always `YYYY-MM-DD` (or '' while empty).
        return (
          <TextInput
            ref={inputRef as Ref<HTMLInputElement>}
            type="date"
            aria-label={attr.name}
            value={typeof draft === 'string' ? draft : ''}
            onChange={(e) => setDraft(e.currentTarget.value)}
            onBlur={commit}
            onKeyDown={commonKeyDown}
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
