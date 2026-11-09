'use client';

import { useEffect, useState } from 'react';

const FORMATS = {
  // "Mon, 9 Nov, 2:02 pm IST" in the viewer's own timezone and locale.
  datetime: { weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' },
  time: { hour: 'numeric', minute: '2-digit', second: '2-digit' },
  date: { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' },
} as const satisfies Record<string, Intl.DateTimeFormatOptions>;

// "IST" reads better than the "GMT+5:30" most locales print for India; other zones keep the locale's short name.
function zoneLabel(date: Date): string {
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (tz === 'Asia/Kolkata' || tz === 'Asia/Calcutta') return 'IST';
  return new Intl.DateTimeFormat(undefined, { timeZoneName: 'short' }).formatToParts(date).find((p) => p.type === 'timeZoneName')?.value ?? '';
}

function localText(iso: string, format: keyof typeof FORMATS): string {
  const date = new Date(iso);
  if (format === 'date') return new Intl.DateTimeFormat(undefined, FORMATS.date).format(date);
  return `${new Intl.DateTimeFormat(undefined, FORMATS[format]).format(date)} ${zoneLabel(date)}`.trim();
}

/**
 * A date in the reader's timezone (IST for the team and the judges). The server cannot know it, so the first
 * paint shows UTC, labelled as such, and the browser replaces it after mount. Full ISO value in the tooltip.
 */
export function LocalTime({ iso, format = 'datetime' }: { iso: string; format?: keyof typeof FORMATS }) {
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the viewer's timezone is only known in the browser
    setText(localText(iso, format));
  }, [iso, format]);
  if (!iso) return null;
  const fallback = `${new Intl.DateTimeFormat('en-GB', { ...FORMATS[format], timeZone: 'UTC' }).format(new Date(iso))} UTC`;
  return (
    <time dateTime={iso} title={new Date(iso).toISOString().replace('T', ' ').slice(0, 19) + ' UTC'} suppressHydrationWarning>
      {text ?? fallback}
    </time>
  );
}
