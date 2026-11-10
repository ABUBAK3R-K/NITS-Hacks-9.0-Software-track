"use client";
// Visitor progress kept in this browser only (localStorage): the blind-run verdict tally,
// which "Try it in 60 seconds" steps have been seen in real app state, and a few flags.
// Nothing here is sent to the backend and nothing is invented: every tick is recorded by a
// component that observed the real state (a reveal, a RESOLVED lifecycle, a page visit).
import { useSyncExternalStore } from "react";

const KEY = "jarvis_progress_v1";
const CHANGE = "jarvis:progress";

export type Outcome = "matched" | "wrong" | "abstained";
export type StepId = "run" | "read" | "reveal" | "fix" | "report";

export interface Progress {
  /** one entry per revealed blind run, keyed by run_id so a reveal is never counted twice */
  verdicts: Record<string, Outcome>;
  steps: Partial<Record<StepId, boolean>>;
  checklistDismissed: boolean;
  checklistCollapsed: boolean;
}

const EMPTY: Progress = { verdicts: {}, steps: {}, checklistDismissed: false, checklistCollapsed: false };

let cacheRaw: string | null = null;
let cache: Progress = EMPTY;

function read(): Progress {
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(KEY);
  } catch {
    return cache;
  }
  if (raw === cacheRaw) return cache;
  cacheRaw = raw;
  try {
    cache = raw ? { ...EMPTY, ...(JSON.parse(raw) as Partial<Progress>) } : EMPTY;
  } catch {
    cache = EMPTY;
  }
  return cache;
}

function write(next: Progress) {
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* storage unavailable: progress lives for this page only */
    cache = next;
    cacheRaw = null;
  }
  window.dispatchEvent(new Event(CHANGE));
}

export function updateProgress(fn: (p: Progress) => Progress) {
  const cur = read();
  const next = fn(cur);
  if (next !== cur) write(next);
}

export function recordVerdict(runId: string, outcome: Outcome) {
  updateProgress((p) => (p.verdicts[runId] === outcome ? p : { ...p, verdicts: { ...p.verdicts, [runId]: outcome } }));
}

export function markStep(step: StepId) {
  updateProgress((p) => (p.steps[step] ? p : { ...p, steps: { ...p.steps, [step]: true } }));
}

function subscribe(cb: () => void) {
  const onStorage = (e: StorageEvent) => e.key === KEY && cb();
  window.addEventListener(CHANGE, cb);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(CHANGE, cb);
    window.removeEventListener("storage", onStorage);
  };
}

export function useProgress(): Progress {
  return useSyncExternalStore(subscribe, read, () => EMPTY);
}

// ---- Abubaker's tour: the checklist appears only once the tour is finished or skipped.
export const TOUR_SEEN_KEY = "jarvis_tour_seen_v1";
export const TOUR_DONE_EVENT = "jarvis:tour-done";

function subscribeTour(cb: () => void) {
  window.addEventListener(TOUR_DONE_EVENT, cb);
  return () => window.removeEventListener(TOUR_DONE_EVENT, cb);
}

function tourSeen(): boolean {
  try {
    return localStorage.getItem(TOUR_SEEN_KEY) === "1";
  } catch {
    return false;
  }
}

export function useTourDone(): boolean {
  return useSyncExternalStore(subscribeTour, tourSeen, () => false);
}
