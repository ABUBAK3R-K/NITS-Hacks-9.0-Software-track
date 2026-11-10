"use client";
// "What is this?": a tiny (?) that explains one thing in one plain sentence. Hover shows the
// native title; a click or tap opens a small popover (phones have no hover). The popover is
// portalled to <body> and clamped to the viewport, so it never causes horizontal overflow and
// is never clipped by a scrolling panel.
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

const W = 260;

export function Hint({ text, label = "What is this?" }: { text: string; label?: string }) {
  const btn = useRef<HTMLButtonElement>(null);
  const [pos, setPos] = useState<{ top?: number; bottom?: number; left: number; w: number } | null>(null);

  useEffect(() => {
    if (!pos) return;
    const close = (e: Event) => {
      if (e.target instanceof Node && btn.current?.contains(e.target)) return;
      setPos(null);
    };
    window.addEventListener("pointerdown", close);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setPos(null);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", close);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [pos]);

  const toggle = (e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    if (pos) return setPos(null);
    const r = btn.current?.getBoundingClientRect();
    if (!r) return;
    const w = Math.min(W, window.innerWidth - 16);
    const left = Math.max(8, Math.min(r.left + r.width / 2 - w / 2, window.innerWidth - w - 8));
    // open upward when the (?) sits low on the screen
    if (r.bottom > window.innerHeight * 0.65) setPos({ bottom: window.innerHeight - r.top + 6, left, w });
    else setPos({ top: r.bottom + 6, left, w });
  };

  return (
    <>
      <button
        ref={btn}
        type="button"
        onClick={toggle}
        title={text}
        aria-label={label}
        aria-expanded={pos !== null}
        className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-bold leading-none text-subtle ring-1 ring-rule-strong transition hover:text-ink hover:ring-muted"
      >
        ?
      </button>
      {pos &&
        createPortal(
          <div
            role="tooltip"
            className="fade-up fixed z-[120] rounded-lg border border-rule-strong bg-sheet px-3 py-2 text-[13px] font-normal normal-case leading-snug tracking-normal text-ink-2 shadow-2xl"
            style={{ top: pos.top, bottom: pos.bottom, left: pos.left, width: pos.w }}
          >
            {text}
          </div>,
          document.body,
        )}
    </>
  );
}

export const HINTS = {
  score:
    "Each candidate's score adds four terms: upstream (how many other failing services depend on it), precedence (it broke before they did), change (a deploy on it shortly before) and severity (how big its anomaly is).",
  lifecycle:
    "The engine moves each incident through detected, spreading, cause identified, mitigating (a fix was applied) and resolved (its signals look healthy again). The UI never moves it by hand.",
  noise: "How many alerts the engine folded into how many incidents, so on-call reads one card instead of a page storm.",
  naive:
    "Naive AI mode asks a plain LLM the same question over the raw events, with no engine, so you can compare its answer and its measured accuracy with ours.",
};
