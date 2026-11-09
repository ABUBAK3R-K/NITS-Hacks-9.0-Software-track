'use client';

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

export type TocItem = { id: string; title: string };

/**
 * Table of contents with scroll tracking. Pinned in the margin on wide screens (CSS sticky), collapsible at the
 * top on narrow ones. One indicator bar slides along the rail to the active entry (transform + height, both
 * transitioned), so it glides instead of jumping. The active entry is the last section whose heading has passed
 * the top 30% of the viewport: where the reader's eyes are, not merely what is visible.
 */
export function Toc({ items }: { items: TocItem[] }) {
  const [active, setActive] = useState(items[0]?.id ?? '');
  const [bar, setBar] = useState<{ y: number; h: number } | null>(null);
  const listRef = useRef<HTMLOListElement>(null);

  useEffect(() => {
    const sections = items.map((i) => document.getElementById(i.id)).filter((el): el is HTMLElement => Boolean(el));
    if (!sections.length) return;
    const update = () => {
      const band = window.innerHeight * 0.3;
      let current = sections[0]!.id;
      for (const s of sections) if (s.getBoundingClientRect().top - band <= 0) current = s.id;
      if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) current = sections.at(-1)!.id;
      setActive(current);
    };
    update();
    let frame = 0;
    const onScroll = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(update);
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('scroll', onScroll);
    };
  }, [items]);

  // Measure the active entry and move the bar there. Re-measure when fonts load or the list resizes (wrapping titles).
  const measure = useCallback(() => {
    const el = listRef.current?.querySelector<HTMLElement>('[aria-current="location"]');
    if (el) setBar({ y: el.offsetTop, h: el.offsetHeight });
  }, []);
  useLayoutEffect(measure, [active, measure]);
  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const ro = new ResizeObserver(measure);
    ro.observe(list);
    document.fonts?.ready.then(measure);
    return () => ro.disconnect();
  }, [measure]);

  // Keep the active entry visible inside a long, scrollable TOC.
  useEffect(() => {
    listRef.current?.querySelector('[aria-current="location"]')?.scrollIntoView({ block: 'nearest' });
  }, [active]);

  const entries = items.map((item, i) => (
    <li key={item.id}>
      <a href={`#${item.id}`} aria-current={active === item.id ? 'location' : undefined}>
        <span className="toc-num">{i + 1}</span>
        {item.title}
      </a>
    </li>
  ));

  return (
    <>
      <aside className="toc" aria-label="Contents">
        <p className="toc-title">On this page</p>
        <ol ref={listRef}>
          {bar && <span className="toc-bar" aria-hidden="true" style={{ transform: `translateY(${bar.y}px)`, height: bar.h }} />}
          {entries}
        </ol>
      </aside>
      <details className="toc-mobile">
        <summary>On this page</summary>
        <ol>{entries}</ol>
      </details>
    </>
  );
}
