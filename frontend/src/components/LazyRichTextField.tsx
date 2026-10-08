import { Loader } from '@mantine/core';
import { Suspense, lazy } from 'react';
import type { RichTextFieldProps } from './RichTextField';

const RichTextField = lazy(() => import('./RichTextField'));

/** The rich text editor, loaded on first use (TipTap is a separate chunk). */
export function LazyRichTextField(props: RichTextFieldProps) {
  return (
    <Suspense fallback={<Loader size="xs" />}>
      <RichTextField {...props} />
    </Suspense>
  );
}
