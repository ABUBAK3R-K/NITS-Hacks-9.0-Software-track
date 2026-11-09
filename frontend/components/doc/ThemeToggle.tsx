'use client';

import { Moon, Sun } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

type Theme = 'light' | 'dark';
export const DOC_THEME_KEY = 'jarvis-doc-theme';
export const DOC_ROOT_ID = 'cs-root';

const systemTheme = (): Theme => (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
const savedTheme = (): Theme | null => {
  try {
    const t = localStorage.getItem(DOC_THEME_KEY);
    return t === 'light' || t === 'dark' ? t : null;
  } catch {
    return null;
  }
};
const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const wrapper = () => document.getElementById(DOC_ROOT_ID);

/**
 * Light / dark for the case study only. The theme lives on the .cs wrapper (never on <html>), so the dashboard's
 * own dark styling cannot change. With nothing saved the page follows the OS; the first click saves the opposite
 * of what is on screen. Circular reveal via View Transitions, a short crossfade without it, instant under
 * prefers-reduced-motion.
 */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme | null>(null); // null until mounted: avoids a hydration mismatch
  const button = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const saved = savedTheme();
    // Client-side navigations skip the inline no-flash script, so apply the saved choice here as well.
    const el = wrapper();
    if (saved && el) el.dataset.theme = saved;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reading browser-only state after mount
    setTheme(saved ?? systemTheme());
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const onSystem = () => {
      if (!savedTheme()) setTheme(systemTheme());
    };
    mq.addEventListener('change', onSystem);
    return () => mq.removeEventListener('change', onSystem);
  }, []);

  const commit = (next: Theme) => {
    const el = wrapper();
    if (el) el.dataset.theme = next;
    try {
      localStorage.setItem(DOC_THEME_KEY, next);
    } catch {
      // storage blocked: the choice lasts for this page
    }
    setTheme(next);
  };

  const toggle = () => {
    const next: Theme = (theme ?? systemTheme()) === 'dark' ? 'light' : 'dark';
    const el = wrapper();
    if (reducedMotion() || !el) return commit(next);

    const doc = document as Document & {
      startViewTransition?: (cb: () => void) => { ready: Promise<void>; finished: Promise<void> };
    };
    if (!doc.startViewTransition) {
      el.classList.add('theme-fade');
      commit(next);
      window.setTimeout(() => el.classList.remove('theme-fade'), 300);
      return;
    }

    const root = document.documentElement;
    const r = button.current?.getBoundingClientRect();
    const x = r ? r.left + r.width / 2 : window.innerWidth - 40;
    const y = r ? r.top + r.height / 2 : 32;
    const radius = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y));
    root.classList.add('cs-vt'); // scopes the view-transition CSS to this switch only
    const transition = doc.startViewTransition(() => commit(next));
    transition.ready
      .then(() => {
        root.animate(
          { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
          { duration: 450, easing: 'cubic-bezier(0.4, 0, 0.2, 1)', pseudoElement: '::view-transition-new(root)' },
        );
      })
      .catch(() => {});
    transition.finished.finally(() => root.classList.remove('cs-vt')).catch(() => {});
  };

  const effective = theme ?? 'light';
  const label = effective === 'dark' ? 'Switch to light mode' : 'Switch to dark mode';
  return (
    <button ref={button} type="button" className="icon-btn theme-toggle" onClick={toggle} aria-label={label} title={label}>
      <Sun size={16} strokeWidth={1.75} aria-hidden="true" className={`ti ti-sun ${effective === 'dark' ? 'on' : ''}`} />
      <Moon size={16} strokeWidth={1.75} aria-hidden="true" className={`ti ti-moon ${effective === 'light' ? 'on' : ''}`} />
    </button>
  );
}
