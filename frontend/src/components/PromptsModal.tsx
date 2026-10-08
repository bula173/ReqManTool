import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Checkbox,
  Code,
  FileButton,
  Group,
  Loader,
  Modal,
  ScrollArea,
  SegmentedControl,
  Select,
  Stack,
  Table,
  Tabs,
  Text,
  TextInput,
  Textarea,
  Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
  IconAlertCircle,
  IconClipboardText,
  IconCopy,
  IconDownload,
  IconEdit,
  IconPlus,
  IconSparkles,
  IconTrash,
  IconUpload,
  IconCopyPlus,
} from '@tabler/icons-react';
import { useMemo, useState } from 'react';
import { useAppActions } from '../actions/context';
import { ApiError } from '../api/client';
import {
  promptExportUrl,
  useDeletePrompt,
  useImportPrompt,
  useModules,
  usePrompts,
  useRenderPrompt,
  useRequirements,
  useSavePrompt,
} from '../api/queries';
import type { Prompt, PromptFields, PromptImportMode, PromptSelection, RenderedPrompt } from '../api/types';

/** Per browser: the user saw that a copied prompt (with requirements) leaves this machine once pasted into an AI chat. */
const NOTICE_KEY = 'reqman.aiPromptNoticeAcknowledged';

function noticeAcknowledged(): boolean {
  try {
    return window.localStorage.getItem(NOTICE_KEY) === '1';
  } catch {
    return false;
  }
}

function acknowledgeNotice(): void {
  try {
    window.localStorage.setItem(NOTICE_KEY, '1');
  } catch {
    // storage unavailable: the notice shows again next time
  }
}

/** The placeholders a prompt body may use (filled server-side; see backend `prompts.PLACEHOLDERS`). */
const PLACEHOLDER_HELP: [string, string][] = [
  ['project_name', 'project name'],
  ['project_description', 'project description'],
  ['module_name', 'target module name'],
  ['module_description', 'target module description'],
  ['module_type', 'target module type'],
  ['module_prefix', 'target module id prefix'],
  ['csv_header', 'exact CSV import header of the target module'],
  ['attribute_guide', 'one line per column: type, required, choices'],
  ['link_columns', 'the Link: columns of the target module'],
  ['import_rules', 'import rules (ids, Parent, one CSV block)'],
  ['selection_module_name', 'module of the selected requirements'],
  ['selected_ids', 'selected requirement ids'],
  ['selected_csv', 'selected requirements as CSV'],
  ['free_text', 'the free text typed in this dialog'],
];

const EMPTY_FIELDS: PromptFields = {
  name: '',
  description: '',
  selection: 'optional',
  free_text: 'optional',
  free_text_label: '',
  import_mode: 'create',
  body: '{{import_rules}}\n',
};

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

type SelectionSource = 'none' | 'selected' | 'module';

interface PromptsModalProps {
  projectId: string;
  /** The module the dialog was opened for: the default target and the source of the selection. */
  moduleId: string;
  /** The selected/clicked requirement of that module, if any. */
  requirementId: string | null;
  onClose: () => void;
}

/**
 * The AI prompt library. "Use" fills a prompt (server-side) from the target
 * module and the selected requirements for the user to copy into any AI
 * chat; "Paste AI result" then opens the CSV import with a paste box.
 * ReqManTool never sends anything to an AI service. "Manage" adds, edits,
 * duplicates, deletes, exports and imports user prompts (built-in ones are
 * read-only).
 */
export function PromptsModal({ projectId, moduleId, requirementId, onClose }: PromptsModalProps) {
  const [tab, setTab] = useState<string | null>('use');
  const [editing, setEditing] = useState<{ id: string | null; fields: PromptFields } | null>(null);
  const promptsQuery = usePrompts();

  return (
    <Modal
      opened
      onClose={onClose}
      title={
        <Group gap="xs">
          <IconSparkles size={18} />
          <Text fw={600}>AI prompts</Text>
        </Group>
      }
      size="xl"
      centered
    >
      <Tabs value={tab} onChange={setTab} keepMounted={false}>
        <Tabs.List mb="sm">
          <Tabs.Tab value="use">Use a prompt</Tabs.Tab>
          <Tabs.Tab value="manage">Manage prompts</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="use">
          {promptsQuery.isLoading ? (
            <Group justify="center" p="md">
              <Loader size="sm" />
            </Group>
          ) : promptsQuery.isError ? (
            <Alert color="red" icon={<IconAlertCircle size={16} />}>
              {errorText(promptsQuery.error, 'Failed to load prompts')}
            </Alert>
          ) : (
            <UsePrompt
              prompts={promptsQuery.data ?? []}
              projectId={projectId}
              moduleId={moduleId}
              requirementId={requirementId}
              onClose={onClose}
            />
          )}
        </Tabs.Panel>
        <Tabs.Panel value="manage">
          {editing ? (
            <PromptEditor
              id={editing.id}
              initial={editing.fields}
              onDone={() => setEditing(null)}
            />
          ) : (
            <ManagePrompts
              prompts={promptsQuery.data ?? []}
              loading={promptsQuery.isLoading}
              onEdit={(id, fields) => setEditing({ id, fields })}
            />
          )}
        </Tabs.Panel>
      </Tabs>
    </Modal>
  );
}

function UsePrompt({
  prompts,
  projectId,
  moduleId,
  requirementId,
  onClose,
}: {
  prompts: Prompt[];
  projectId: string;
  moduleId: string;
  requirementId: string | null;
  onClose: () => void;
}) {
  const { state } = useAppActions();
  const modulesQuery = useModules(projectId);
  const requirementsQuery = useRequirements(projectId, moduleId);
  const render = useRenderPrompt(projectId);
  const usable = prompts.filter((p) => p.error === null);
  const [promptId, setPromptId] = useState<string | null>(usable[0]?.id ?? null);
  const [targetId, setTargetId] = useState<string>(moduleId);
  const [source, setSource] = useState<SelectionSource>(requirementId ? 'selected' : 'none');
  const [includeChildren, setIncludeChildren] = useState(true);
  const [freeText, setFreeText] = useState('');
  const [rendered, setRendered] = useState<RenderedPrompt | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [acknowledged, setAcknowledged] = useState(noticeAcknowledged);

  const prompt = prompts.find((p) => p.id === promptId) ?? null;
  const modules = modulesQuery.data ?? [];
  const sourceModule = modules.find((m) => m.id === moduleId);
  const target = modules.find((m) => m.id === targetId);
  const locked = state.lockedProjectIds.has(projectId);

  const promptOptions = useMemo(
    () => [
      { group: 'Built-in', items: prompts.filter((p) => p.source === 'builtin') },
      { group: 'My prompts', items: prompts.filter((p) => p.source === 'user') },
    ]
      .filter((g) => g.items.length > 0)
      .map((g) => ({
        group: g.group,
        items: g.items.map((p) => ({
          value: p.id,
          label: p.error ? `${p.name} (invalid)` : p.name,
          disabled: p.error !== null,
        })),
      })),
    [prompts],
  );

  const reset = () => {
    setRendered(null);
    setError(null);
  };

  const selectedIds = (): string[] => {
    if (source === 'selected' && requirementId) return [requirementId];
    if (source === 'module') return (requirementsQuery.data ?? []).map((r) => r.id);
    return [];
  };

  const handleGenerate = () => {
    if (!prompt) return;
    setError(null);
    render.mutate(
      {
        prompt_id: prompt.id,
        module_id: targetId,
        requirement_ids: selectedIds(),
        selection_module_id: moduleId,
        include_children: source === 'selected' && includeChildren,
        free_text: freeText,
      },
      {
        onSuccess: setRendered,
        onError: (err) => {
          setRendered(null);
          setError(errorText(err, 'Failed to generate the prompt'));
        },
      },
    );
  };

  const handleCopy = async () => {
    if (!rendered) return;
    try {
      await navigator.clipboard.writeText(rendered.text);
      notifications.show({ message: 'Prompt copied. Paste it into your AI chat.', color: 'green' });
    } catch {
      notifications.show({ message: 'Copy failed: select the text and copy it manually', color: 'red' });
    }
  };

  const handlePaste = () => {
    if (!target || !prompt) return;
    onClose();
    state.commands.openDialog({
      kind: 'importCsv',
      projectId,
      moduleId: target.id,
      moduleName: target.name,
      input: 'paste',
      mode: prompt.import_mode === 'update' ? 'update' : 'create',
    });
  };

  if (usable.length === 0) {
    return <Text size="sm" c="dimmed">No usable prompts. Add one under Manage prompts.</Text>;
  }

  const needsSelection = prompt?.selection === 'required';
  const missingSelection = needsSelection && selectedIds().length === 0;
  const missingText = prompt?.free_text === 'required' && !freeText.trim();

  return (
    <Stack gap="sm">
      {!acknowledged && (
        <Alert color="yellow" icon={<IconAlertCircle size={16} />} title="Your requirements leave this machine">
          <Text size="sm">
            ReqManTool never contacts an AI service. A generated prompt contains project data (names,
            descriptions and the selected requirements); once you paste it into an external AI chat, that data is
            sent to that service. Only use prompts with data you are allowed to share.
          </Text>
          <Button
            size="xs"
            mt="xs"
            variant="light"
            color="yellow"
            onClick={() => {
              acknowledgeNotice();
              setAcknowledged(true);
            }}
          >
            I understand
          </Button>
        </Alert>
      )}
      <Select
        label="Prompt"
        data={promptOptions}
        value={promptId}
        onChange={(value) => {
          setPromptId(value);
          reset();
        }}
        searchable
        allowDeselect={false}
      />
      {prompt?.description && (
        <Text size="xs" c="dimmed">
          {prompt.description}
        </Text>
      )}
      <Group grow align="flex-start">
        <Select
          label="Target module"
          description="Where the AI's CSV answer will be imported"
          data={modules.map((m) => ({ value: m.id, label: m.name }))}
          value={targetId}
          onChange={(value) => {
            if (value) setTargetId(value);
            reset();
          }}
          disabled={modulesQuery.isLoading}
          allowDeselect={false}
        />
        <Stack gap={4}>
          <Text size="sm" fw={500}>
            Requirements to include{needsSelection ? ' (required)' : ''}
          </Text>
          <SegmentedControl
            size="xs"
            value={source}
            onChange={(value) => {
              setSource(value as SelectionSource);
              reset();
            }}
            data={[
              { value: 'none', label: 'None' },
              { value: 'selected', label: requirementId ? `Selected (${requirementId})` : 'Selected', disabled: !requirementId },
              { value: 'module', label: `All of ${sourceModule?.name ?? 'module'}` },
            ]}
          />
          {source === 'selected' && (
            <Checkbox
              size="xs"
              label="Include child requirements"
              checked={includeChildren}
              onChange={(event) => {
                setIncludeChildren(event.currentTarget.checked);
                reset();
              }}
            />
          )}
        </Stack>
      </Group>
      {prompt && prompt.free_text !== 'none' && (
        <Textarea
          label={prompt.free_text_label || 'Free text'}
          withAsterisk={prompt.free_text === 'required'}
          value={freeText}
          onChange={(event) => {
            setFreeText(event.currentTarget.value);
            reset();
          }}
          autosize
          minRows={2}
          maxRows={8}
        />
      )}
      {error && (
        <Alert color="red" icon={<IconAlertCircle size={16} />}>
          <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
            {error}
          </Text>
        </Alert>
      )}
      <Group justify="flex-end">
        <Button
          variant="light"
          leftSection={<IconSparkles size={16} />}
          onClick={handleGenerate}
          loading={render.isPending}
          disabled={!prompt || missingSelection || missingText}
        >
          Generate preview
        </Button>
      </Group>
      {rendered && (
        <>
          <Textarea
            label={`Prompt for "${rendered.module_name}"${rendered.selected_ids.length ? ` with ${rendered.selected_ids.length} requirement(s)` : ''}`}
            value={rendered.text}
            readOnly
            autosize
            minRows={8}
            maxRows={16}
            styles={{ input: { fontFamily: 'monospace', fontSize: 12 } }}
          />
          <Group justify="space-between">
            <Text size="xs" c="dimmed">
              {rendered.import_mode === 'none'
                ? 'This prompt asks for a review, not CSV: nothing to import.'
                : 'Paste the answer back with "Paste AI result"; it is checked before anything is imported.'}
            </Text>
            <Group gap="xs">
              <Tooltip label="Acknowledge the notice above first" disabled={acknowledged}>
                <Button leftSection={<IconCopy size={16} />} onClick={handleCopy} disabled={!acknowledged}>
                  Copy
                </Button>
              </Tooltip>
              {rendered.import_mode !== 'none' && (
                <Tooltip label="The project is locked" disabled={!locked}>
                  <Button
                    variant="light"
                    leftSection={<IconClipboardText size={16} />}
                    onClick={handlePaste}
                    disabled={locked || !target}
                  >
                    Paste AI result
                  </Button>
                </Tooltip>
              )}
            </Group>
          </Group>
        </>
      )}
    </Stack>
  );
}

function fieldsOf(prompt: Prompt): PromptFields {
  return {
    name: prompt.name,
    description: prompt.description,
    selection: prompt.selection,
    free_text: prompt.free_text,
    free_text_label: prompt.free_text_label,
    import_mode: prompt.import_mode,
    body: prompt.body,
  };
}

function ManagePrompts({
  prompts,
  loading,
  onEdit,
}: {
  prompts: Prompt[];
  loading: boolean;
  onEdit: (id: string | null, fields: PromptFields) => void;
}) {
  const { state } = useAppActions();
  const remove = useDeletePrompt();
  const importPrompt = useImportPrompt();

  const handleDelete = (prompt: Prompt) => {
    if (!window.confirm(`Delete the prompt "${prompt.name}"?`)) return;
    remove.mutate(prompt.id, {
      onSuccess: () => notifications.show({ message: `Deleted "${prompt.name}"`, color: 'green' }),
      onError: (err) => notifications.show({ message: errorText(err, 'Failed to delete'), color: 'red' }),
    });
  };

  const handleImport = (file: File | null) => {
    if (!file) return;
    importPrompt.mutate(file, {
      onSuccess: (prompt) => notifications.show({ message: `Imported "${prompt.name}"`, color: 'green' }),
      onError: (err) => notifications.show({ message: errorText(err, 'Failed to import the prompt'), color: 'red' }),
    });
  };

  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Text size="sm" c="dimmed">
          Built-in prompts are read-only: duplicate one to change it. User prompts are stored in the data folder.
        </Text>
        <Group gap="xs">
          <FileButton onChange={handleImport} accept=".json,application/json">
            {(props) => (
              <Button {...props} size="xs" variant="default" leftSection={<IconUpload size={14} />} loading={importPrompt.isPending}>
                Import
              </Button>
            )}
          </FileButton>
          <Button size="xs" leftSection={<IconPlus size={14} />} onClick={() => onEdit(null, EMPTY_FIELDS)}>
            New prompt
          </Button>
        </Group>
      </Group>
      {loading ? (
        <Loader size="sm" />
      ) : (
        <ScrollArea.Autosize mah={420} type="auto">
          <Table striped fz="sm">
            <Table.Tbody>
              {prompts.map((prompt) => (
                <Table.Tr key={prompt.id}>
                  <Table.Td>
                    <Group gap={6}>
                      <Text size="sm">{prompt.name}</Text>
                      <Badge size="xs" variant="light" color={prompt.source === 'builtin' ? 'gray' : 'blue'}>
                        {prompt.source === 'builtin' ? 'built-in' : 'user'}
                      </Badge>
                      {prompt.error && (
                        <Tooltip label={prompt.error} multiline w={320}>
                          <Badge size="xs" color="red">invalid</Badge>
                        </Tooltip>
                      )}
                    </Group>
                  </Table.Td>
                  <Table.Td w={140}>
                    <Group gap={4} justify="flex-end" wrap="nowrap">
                      {prompt.source === 'user' && (
                        <Tooltip label="Edit">
                          <ActionIcon variant="subtle" onClick={() => onEdit(prompt.id, fieldsOf(prompt))} aria-label="Edit">
                            <IconEdit size={16} />
                          </ActionIcon>
                        </Tooltip>
                      )}
                      {prompt.error === null && (
                        <Tooltip label="Duplicate">
                          <ActionIcon
                            variant="subtle"
                            onClick={() => onEdit(null, { ...fieldsOf(prompt), name: `${prompt.name} (copy)` })}
                            aria-label="Duplicate"
                          >
                            <IconCopyPlus size={16} />
                          </ActionIcon>
                        </Tooltip>
                      )}
                      {prompt.error === null && (
                        <Tooltip label="Export">
                          <ActionIcon
                            variant="subtle"
                            onClick={() => state.commands.download(promptExportUrl(prompt.id))}
                            aria-label="Export"
                          >
                            <IconDownload size={16} />
                          </ActionIcon>
                        </Tooltip>
                      )}
                      {prompt.source === 'user' && (
                        <Tooltip label="Delete">
                          <ActionIcon variant="subtle" color="red" onClick={() => handleDelete(prompt)} aria-label="Delete">
                            <IconTrash size={16} />
                          </ActionIcon>
                        </Tooltip>
                      )}
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </ScrollArea.Autosize>
      )}
    </Stack>
  );
}

function PromptEditor({ id, initial, onDone }: { id: string | null; initial: PromptFields; onDone: () => void }) {
  const save = useSavePrompt();
  const [fields, setFields] = useState<PromptFields>(initial);
  const [error, setError] = useState<string | null>(null);
  const set = <K extends keyof PromptFields>(key: K, value: PromptFields[K]) => {
    setFields((current) => ({ ...current, [key]: value }));
    setError(null);
  };

  const handleSave = () => {
    save.mutate(
      { id, fields },
      {
        onSuccess: (prompt) => {
          notifications.show({ message: `Saved "${prompt.name}"`, color: 'green' });
          onDone();
        },
        onError: (err) => setError(errorText(err, 'Failed to save the prompt')),
      },
    );
  };

  return (
    <Stack gap="sm">
      <TextInput label="Name" required value={fields.name} onChange={(e) => set('name', e.currentTarget.value)} />
      <TextInput label="Description" value={fields.description} onChange={(e) => set('description', e.currentTarget.value)} />
      <Group grow align="flex-start">
        <Select
          label="Selected requirements"
          data={[
            { value: 'none', label: 'Not used' },
            { value: 'optional', label: 'Optional' },
            { value: 'required', label: 'Required' },
          ]}
          value={fields.selection}
          onChange={(value) => value && set('selection', value as PromptSelection)}
          allowDeselect={false}
        />
        <Select
          label="Free text"
          data={[
            { value: 'none', label: 'Not used' },
            { value: 'optional', label: 'Optional' },
            { value: 'required', label: 'Required' },
          ]}
          value={fields.free_text}
          onChange={(value) => value && set('free_text', value as PromptSelection)}
          allowDeselect={false}
        />
        <Select
          label="AI answer"
          data={[
            { value: 'create', label: 'CSV: new requirements' },
            { value: 'update', label: 'CSV: update by Id' },
            { value: 'none', label: 'Review (no CSV)' },
          ]}
          value={fields.import_mode}
          onChange={(value) => value && set('import_mode', value as PromptImportMode)}
          allowDeselect={false}
        />
      </Group>
      {fields.free_text !== 'none' && (
        <TextInput
          label="Free text label"
          value={fields.free_text_label}
          onChange={(e) => set('free_text_label', e.currentTarget.value)}
        />
      )}
      <Textarea
        label="Prompt text"
        required
        value={fields.body}
        onChange={(e) => set('body', e.currentTarget.value)}
        autosize
        minRows={8}
        maxRows={18}
        styles={{ input: { fontFamily: 'monospace', fontSize: 12 } }}
      />
      <Text size="xs" c="dimmed">
        Placeholders (filled when the prompt is generated):{' '}
        {PLACEHOLDER_HELP.map(([name, help], index) => (
          <span key={name} title={help}>
            {index > 0 ? ', ' : ''}
            <Code>{`{{${name}}}`}</Code>
          </span>
        ))}
      </Text>
      {error && (
        <Alert color="red" icon={<IconAlertCircle size={16} />}>
          <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
            {error}
          </Text>
        </Alert>
      )}
      <Group justify="flex-end">
        <Button variant="default" onClick={onDone} disabled={save.isPending}>
          Cancel
        </Button>
        <Button onClick={handleSave} loading={save.isPending} disabled={!fields.name.trim() || !fields.body.trim()}>
          Save
        </Button>
      </Group>
    </Stack>
  );
}
