// Display helpers. Every time on screen is IST (Asia/Kolkata), whatever the viewer's machine says.

const timeFmt = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

const dateTimeFmt = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

const inrFmt = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

const intFmt = new Intl.NumberFormat("en-IN");

export function istTime(ts: string | null | undefined): string {
  if (!ts) return "--:--:--";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return timeFmt.format(d);
}

export function istTimeMs(ts: string | null | undefined): string {
  if (!ts) return "--:--:--";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  const ms = String(d.getUTCMilliseconds()).padStart(3, "0");
  return `${timeFmt.format(d)}.${ms}`;
}

export function istDateTime(ts: string | null | undefined): string {
  if (!ts) return "";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return `${dateTimeFmt.format(d)} IST`;
}

export function inr(n: number): string {
  return inrFmt.format(n);
}

export function int(n: number): string {
  return intFmt.format(n);
}

export function pct(n: number | null | undefined, digits = 0): string {
  if (n === null || n === undefined) return "n/a";
  return `${(n * 100).toFixed(digits)}%`;
}

export function deltaSeconds(a: string, b: string): string {
  const s = (new Date(b).getTime() - new Date(a).getTime()) / 1000;
  return `${s >= 0 ? "+" : ""}${s.toFixed(1)} s`;
}

export const FAULT_LABELS: Record<string, string> = {
  db_pool_exhaustion: "DB pool exhaustion",
  bad_deploy: "Bad deploy",
  redis_down: "Redis down",
  red_herring: "Red herring deploy",
  psp_slow: "Slow bank (PSP)",
};

export const SIGNATURE_LABELS: Record<string, string> = {
  connection_pool_exhausted: "connection pool exhausted",
  error_spike_after_deploy: "error spike after deploy",
  dependency_down: "dependency down",
  latency_spike: "latency spike",
  error_spike: "error spike",
};

export function signatureLabel(s: string | null | undefined): string {
  if (!s) return "";
  return SIGNATURE_LABELS[s] ?? s.replaceAll("_", " ");
}

export function shortId(id: string): string {
  return id.replace(/^evt_0*/, "#");
}
