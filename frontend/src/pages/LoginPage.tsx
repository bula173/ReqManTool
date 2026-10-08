import { Alert, Button, Center, Paper, Stack, Text, Title } from '@mantine/core';
import { IconAlertTriangle, IconBrandGitlab, IconLogin } from '@tabler/icons-react';
import { useState } from 'react';
import { loginUrl } from '../api/auth';
import type { AuthProviderInfo } from '../api/types';

const LOGIN_ERROR_PARAM = 'login_error';

/** The `login_error` the backend's callback put in the URL (a failed login), removed from the address bar. */
function takeLoginError(): string | null {
  const url = new URL(window.location.href);
  const message = url.searchParams.get(LOGIN_ERROR_PARAM);
  if (message !== null) {
    url.searchParams.delete(LOGIN_ERROR_PARAM);
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
  }
  return message;
}

interface LoginPageProps {
  providers: AuthProviderInfo[];
  /** The session check itself failed (backend unreachable, ...), not "not logged in". */
  error: Error | null;
  onRetry: () => void;
}

/**
 * Server edition: shown instead of the app while nobody is logged in. Each
 * provider button is a full-page navigation to the backend, which redirects
 * to the identity provider and, after the login, back to the page that was
 * open (`next`).
 */
export function LoginPage({ providers, error, onRetry }: LoginPageProps) {
  const [loginError] = useState(takeLoginError);
  const next = `${window.location.pathname}${window.location.search}`;

  return (
    <Center h="100vh" p="md">
      <Paper withBorder shadow="sm" p="xl" radius="md" w={380} maw="100%">
        <Stack gap="md">
          <Title order={2}>ReqManTool</Title>
          <Text c="dimmed" size="sm">
            Log in to continue.
          </Text>
          {loginError && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />} title="Login failed">
              {loginError}
            </Alert>
          )}
          {error && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />} title="Cannot reach the server">
              <Stack gap="xs" align="flex-start">
                <Text size="sm">{error.message}</Text>
                <Button size="xs" variant="light" onClick={onRetry}>
                  Retry
                </Button>
              </Stack>
            </Alert>
          )}
          {providers.length === 0 ? (
            <Text size="sm">No login provider is configured on this server.</Text>
          ) : (
            providers.map((provider) => (
              <Button
                key={provider.id}
                component="a"
                href={loginUrl(provider.login_url, next)}
                leftSection={provider.type === 'gitlab' ? <IconBrandGitlab size={18} /> : <IconLogin size={18} />}
                variant="default"
                size="md"
                fullWidth
              >
                Log in with {provider.name}
              </Button>
            ))
          )}
        </Stack>
      </Paper>
    </Center>
  );
}
