import { Alert, Button, Checkbox, Group, Modal, Stack, Text, Textarea, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { ApiError } from '../api/client';
import { useCreateBaseline, useProject } from '../api/queries';

interface CreateBaselineModalProps {
  projectId: string;
  opened: boolean;
  onClose: () => void;
}

/** Same rule as the backend's `validate_baseline_name` (which decides in the end). */
const NAME_RULE =
  "1–100 characters: letters, digits, '.', '_' or '-'; starts with a letter or digit; no '..'; not ending in '.' or '.lock'";

function nameError(raw: string): string | null {
  const name = raw.trim();
  if (!name) return 'Baseline name is required';
  if (
    name.length > 100 ||
    !/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(name) ||
    name.includes('..') ||
    name.endsWith('.') ||
    name.endsWith('.lock')
  ) {
    return `Invalid name: ${NAME_RULE}`;
  }
  if (name === 'current' || name === 'compare') return `The name "${name}" is reserved`;
  return null;
}

/**
 * "Create baseline": commits pending changes, fast-forwards the `baseline`
 * branch and creates the annotated `baseline/<name>` tag. A duplicate name
 * or a repository that is not recording is a 409, shown inline. "Lock
 * after baseline" locks the project with it (the lock is committed first, so
 * the baseline is the locked state); hidden while the project is locked.
 */
export function CreateBaselineModal({ projectId, opened, onClose }: CreateBaselineModalProps) {
  const createBaseline = useCreateBaseline(projectId);
  const projectQuery = useProject(projectId);
  const alreadyLocked = !!projectQuery.data?.locked;
  const form = useForm({
    initialValues: { name: '', description: '', lock: false, lockReason: '' },
    validate: { name: nameError },
  });

  const close = () => {
    form.reset();
    createBaseline.reset();
    onClose();
  };

  const handleSubmit = form.onSubmit((values) => {
    createBaseline.mutate(
      {
        name: values.name.trim(),
        description: values.description,
        ...(values.lock && !alreadyLocked ? { lock: true, lock_reason: values.lockReason.trim() } : {}),
      },
      {
        onSuccess: (baseline) => {
          notifications.show({
            message: `Baseline "${baseline.name}" created${values.lock && baseline.locked ? '; project locked' : ''}`,
            color: 'green',
          });
          close();
        },
      },
    );
  });

  return (
    <Modal opened={opened} onClose={close} title="Create baseline" centered>
      <form onSubmit={handleSubmit}>
        <Stack gap="sm">
          <Text size="sm" c="dimmed">
            A baseline is a named, immutable snapshot of the whole project as it is on disk right now (pending
            changes are committed first).
          </Text>
          <TextInput
            label="Name"
            placeholder="v1.0"
            description={NAME_RULE}
            required
            data-autofocus
            {...form.getInputProps('name')}
          />
          <Textarea label="Description" placeholder="What this baseline marks" {...form.getInputProps('description')} />
          {alreadyLocked ? (
            <Text size="sm" c="dimmed">
              The project is locked; the baseline is taken of the locked state.
            </Text>
          ) : (
            <>
              <Checkbox
                label="Lock the project after the baseline"
                description="Every change is then refused until someone unlocks it. The baseline includes the lock."
                {...form.getInputProps('lock', { type: 'checkbox' })}
              />
              {form.values.lock && (
                <TextInput
                  label="Lock reason"
                  placeholder={`Baseline ${form.values.name.trim() || '<name>'}`}
                  maxLength={500}
                  {...form.getInputProps('lockReason')}
                />
              )}
            </>
          )}
          {createBaseline.isError && (
            <Alert color="red" p="xs">
              <Text size="sm">
                {createBaseline.error instanceof ApiError
                  ? createBaseline.error.message
                  : 'Failed to create the baseline'}
              </Text>
            </Alert>
          )}
          <Group justify="flex-end" mt="sm">
            <Button variant="default" onClick={close}>
              Cancel
            </Button>
            <Button type="submit" loading={createBaseline.isPending}>
              Create baseline
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
