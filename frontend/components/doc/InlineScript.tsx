'use client';

/**
 * An inline script that runs during HTML parsing (before first paint) on hard loads. On the client it renders as
 * text/plain, so React neither warns about nor re-runs it on soft navigations (Next.js "Preventing Flash" guide).
 */
export function InlineScript({ html }: { html: string }) {
  return (
    <script
      type={typeof window === 'undefined' ? 'text/javascript' : 'text/plain'}
      suppressHydrationWarning
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
