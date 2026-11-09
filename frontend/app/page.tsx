import incidents from "@/lib/contracts/fixtures/incidents_list.json";
import type { IncidentSummary } from "@/lib/contracts/types";

// Lane 0 placeholder. Lane C replaces this with the dashboard.
export default function Home() {
  const list = incidents as IncidentSummary[];
  return (
    <main className="min-h-screen bg-zinc-950 p-10 font-sans text-zinc-100">
      <h1 className="text-2xl font-semibold">J.A.R.V.I.S.</h1>
      <p className="mt-1 text-zinc-400">Many alerts, one cause, and every claim shows its evidence.</p>
      <ul className="mt-8 space-y-2">
        {list.map((i) => (
          <li key={i.id} className="rounded border border-zinc-800 p-3">
            <span className="font-mono text-xs text-zinc-500">{i.id}</span> {i.title}{" "}
            <span className="text-zinc-400">
              ({i.alert_count} alerts, {i.status})
            </span>
          </li>
        ))}
      </ul>
    </main>
  );
}
