// The recorded payments outage that ships in /samples, re-timed so it forms a fresh incident now.
// Used by the top bar and by the "Next up" card after the 60 second checklist.
export async function loadSampleLog(): Promise<File> {
  const text = await (await fetch("/samples/payment_outage.log")).text();
  const lines = text.split("\n").filter((l) => l.trim());
  const first = Date.parse(lines[0].split(" ")[0]);
  const shift = Date.now() - 60_000 - first;
  const moved = lines
    .map((l) => {
      const sp = l.indexOf(" ");
      const t = Date.parse(l.slice(0, sp));
      return Number.isNaN(t) ? l : new Date(t + shift).toISOString() + l.slice(sp);
    })
    .join("\n");
  return new File([moved], "payment_outage.log", { type: "text/plain" });
}
