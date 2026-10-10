import '@mantine/core/styles.css';
import '@mantine/notifications/styles.css';

import { MantineProvider } from '@mantine/core';
import { ModalsProvider } from '@mantine/modals';
import { Notifications } from '@mantine/notifications';
import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { App } from './App.tsx';
import { invalidateGitStatus } from './api/queries';
import { AuthGate } from './components/AuthGate';
import { DesktopGate } from './components/DesktopGate';
import './index.css';

const queryClient: QueryClient = new QueryClient({
  // Any successful change may add or amend a commit in the project's history.
  mutationCache: new MutationCache({ onSuccess: () => invalidateGitStatus(queryClient) }),
  defaultOptions: {
    queries: {
      retry: 1,
    },
  },
});

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <MantineProvider defaultColorScheme="auto">
      <Notifications position="top-right" />
      <ModalsProvider>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <DesktopGate>
              <AuthGate>
                <App />
              </AuthGate>
            </DesktopGate>
          </BrowserRouter>
        </QueryClientProvider>
      </ModalsProvider>
    </MantineProvider>
  </StrictMode>,
);
