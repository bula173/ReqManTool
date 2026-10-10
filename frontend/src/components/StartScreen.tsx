import { Button, Card, Center, Container, Group, Stack, Text, TextInput, Title } from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { useState } from 'react';
import { connectLocalBackend, setDesktopMode, type DesktopState } from '../api/desktop';

/**
 * The desktop app's start screen: pick the bundled local instance or an
 * existing ReqManTool server. The last choice and server address are kept in
 * the app's config file, so the screen only appears on the first start and
 * when the tray / app menu asks for it again.
 */
export function StartScreen({ initial, onChosen }: { initial: DesktopState; onChosen: () => void }) {
  const [busy, setBusy] = useState<'local' | 'server' | null>(null);
  const form = useForm({
    initialValues: { serverUrl: initial.serverUrl ?? 'https://' },
    validate: {
      serverUrl: (value) =>
        /^https?:\/\/[^\s/]+/i.test(value.trim()) ? null : 'Enter the server address, e.g. https://reqman.example.com',
    },
  });

  const failed = (what: string, error: unknown) => {
    notifications.show({ color: 'red', title: `Could not ${what}`, message: String(error) });
    setBusy(null);
  };

  const startLocal = async () => {
    setBusy('local');
    try {
      await setDesktopMode('local');
      await connectLocalBackend();
      onChosen();
    } catch (error) {
      failed('start the local instance', error);
    }
  };

  const connectServer = async () => {
    if (form.validate().hasErrors) return;
    setBusy('server');
    try {
      // The window moves to the server, which serves its own UI and API; no
      // local backend is started in this mode.
      await setDesktopMode('server', form.values.serverUrl.trim());
    } catch (error) {
      failed('connect to the server', error);
    }
  };

  return (
    <Center h="100vh">
      <Container size={420}>
        <Card withBorder padding="xl" radius="md" shadow="sm">
          <Stack gap="lg">
            <div>
              <Title order={2}>ReqManTool</Title>
              <Text c="dimmed" size="sm" mt={4}>
                Where should this window get its requirements from?
              </Text>
            </div>

            <Stack gap="xs">
              <Button onClick={startLocal} loading={busy === 'local'} disabled={busy !== null}>
                Start a local instance
              </Button>
              <Text c="dimmed" size="xs">
                Runs ReqManTool on this machine with its own data folder.
              </Text>
            </Stack>

            <form onSubmit={form.onSubmit(connectServer)}>
              <Stack gap="xs">
                <TextInput
                  label="Server address"
                  placeholder="https://reqman.example.com"
                  data-autofocus
                  disabled={busy !== null}
                  {...form.getInputProps('serverUrl')}
                />
                <Group justify="flex-end">
                  <Button type="submit" variant="light" loading={busy === 'server'} disabled={busy !== null}>
                    Connect to a server
                  </Button>
                </Group>
              </Stack>
            </form>
          </Stack>
        </Card>
      </Container>
    </Center>
  );
}
