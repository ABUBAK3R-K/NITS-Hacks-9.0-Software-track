import {
  ArrowRight,
  Bug,
  CircleAlert,
  CircleCheck,
  FileText,
  Info,
  MonitorPlay,
  ShieldCheck,
} from 'lucide-react';
import type { Candidate, Event, Incident, Report } from '@/lib/contracts/types';
import { ABSTAIN_MIN_MARGIN, ABSTAIN_MIN_SCORE, ENGINE_WEIGHTS, VICTIM_PENALTY } from '@/lib/contracts/types';
import heroJson from '@/lib/contracts/fixtures/incident_db_pool.json';
import ambiguousJson from '@/lib/contracts/fixtures/incident_ambiguous.json';
import reportJson from '@/lib/contracts/fixtures/report_db_pool.json';
import { EvalTable } from '@/components/doc/EvalTable';
import { LocalTime } from '@/components/doc/LocalTime';
import { Toc, type TocItem } from '@/components/doc/Toc';
import { Topbar, REPO_URL } from '@/components/doc/Topbar';

// Every number on this page comes from these engine-produced fixtures, the eval API, or the engine constants.
const hero = heroJson as unknown as Incident;
const amb = ambiguousJson as unknown as Incident;
const report = reportJson as unknown as Report;

const ICON = { size: 16, strokeWidth: 1.75, 'aria-hidden': true } as const;
const SMALL = { size: 14, strokeWidth: 1.75, 'aria-hidden': true } as const;

const w = (x: number) => x.toFixed(2);
const s3 = (x: number) => x.toFixed(3);
const pct = (x: number) => `${Math.round(x * 100)}%`;
const inr = (x: number) =>
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(x);
const int = (x: number) => new Intl.NumberFormat('en-IN').format(x);
const list = (xs: string[]) =>
  xs.length <= 1 ? xs.join('') : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`;
const sum = (c: Candidate) => c.components.upstream + c.components.precedence + c.components.change + c.components.severity;
const seconds = (a: string, b: string) => (Date.parse(b) - Date.parse(a)) / 1000;

const EVENTS = new Map<string, Event>([...hero.evidence_events, ...amb.evidence_events].map((e) => [e.id, e]));
const describe = (id: string) => {
  const e = EVENTS.get(id);
  if (!e) return id;
  const what = e.message ?? `${e.name}${e.value != null ? ` = ${e.value}` : ''}`;
  return `${e.service} · ${e.kind} · ${what} (${e.ts})`;
};

function Cites({ ids }: { ids: string[] }) {
  if (!ids.length) return null;
  return (
    <span className="chips">
      {ids.map((id) => (
        <span key={id} className="chip chip-ref" title={describe(id)}>
          {id}
        </span>
      ))}
    </span>
  );
}

// ---- derived facts
const heroRoot = hero.candidates[0]!;
const heroVictims = hero.candidates.slice(1);
const onsets = hero.candidates.map((c) => c.onset).filter((x): x is string => Boolean(x)).sort();
const spreadS = onsets.length > 1 ? seconds(onsets[0]!, onsets[onsets.length - 1]!) : 0;
const incidents = hero.noise?.incidents ?? 1;
const [ambTop1, ambTop2] = amb.candidates as [Candidate, Candidate];
const ambMargin = ambTop1.score - ambTop2.score;
const ambEarlier = [ambTop1, ambTop2].sort((a, b) => Date.parse(a.onset ?? '') - Date.parse(b.onset ?? ''))[0]!;
const reportCites = new Set(report.sentences.flatMap((x) => x.cites));
const allCitesResolve = [...reportCites].every((id) => EVENTS.has(id));
const ruledOut = hero.candidates.filter((c) => c.ruled_out_reason);

const TOC: TocItem[] = [
  { id: 'problem', title: 'The problem' },
  { id: 'what', title: 'What it does' },
  { id: 'architecture', title: 'Architecture' },
  { id: 'engine', title: 'How the engine decides' },
  { id: 'abstain', title: 'Abstaining instead of guessing' },
  { id: 'why-not', title: 'Why not X? and evidence' },
  { id: 'narrator', title: "The LLM's narrow job" },
  { id: 'evaluation', title: 'Evaluation' },
  { id: 'failures', title: 'What broke and what we learned' },
  { id: 'limits', title: 'Limits and roadmap' },
];

const FAILURES: { title: string; text: string; lesson: string }[] = [
  {
    title: 'A dead LLM model that only a live call caught',
    text: 'The Groq model named in our plan is no longer served on our key and returns 404 model_not_found. Every mocked narrator test passed anyway. One real call found it, and we switched to openai/gpt-oss-120b.',
    lesson: 'Mocks prove the code path, not the provider. Make one live call per provider before trusting a green suite.',
  },
  {
    title: 'Fixtures drifted from hand-written copies of production logic',
    text: 'The report fixture cited events the engine never put in its evidence set, and the sample SSE topology came from a second copy of the health logic. Contract tests failed after a regeneration. Fixtures are now produced by running the real engine, and the hand-written scoring copy was deleted.',
    lesson: 'Any fixture built by a copy of production logic will drift. Generate fixtures from the one real implementation.',
  },
  {
    title: 'A silent 404 from wiring by guesswork',
    text: 'The narrator looked for incidents by guessing attribute names on the store module, and a try/except ImportError in main.py hid the miss. Every report would have returned 404. It was caught on read-through before merge; the router is now wired explicitly, so a broken wiring fails at startup.',
    lesson: 'Never wrap wiring in a broad except. A crash at startup is cheaper than a quiet 404 in the demo.',
  },
  {
    title: 'Grounding cannot check causal claims',
    text: 'In a live report, one sentence said ledger-svc errors "led to" auth-svc failures. auth-svc does not call ledger-svc; both depend on postgres. Every cite was valid, so grounding kept the sentence.',
    lesson: 'Grounding checks that evidence exists, not that the causal story is right. A next step is to drop causal claims between services with no edge between them.',
  },
  {
    title: '"Biggest anomaly wins" mislabelled every victim',
    text: 'On the first run every victim got the signature latency_spike, because latency z-scores cap higher than error-rate z-scores. An explicit rule now lets a high error rate dominate.',
    lesson: 'Comparing z-scores across different signals is not meaningful without a rule for which signal tells the story.',
  },
  {
    title: 'The abstain case is fragile to scrape phase',
    text: 'On some seeds the synthetic ambiguous scenario opened confidently on postgres, because its lock waits happened to cross the threshold one scrape earlier. The scenario was pinned, and the lesson went into the product: the disambiguation list asks for 1 s resolution data.',
    lesson: 'When onsets are close to the scrape interval, the order is not reliable, and the system should say so.',
  },
];

export default function CaseStudyPage() {
  return (
    <>
      <Topbar />
      <div className="doc" id="top">
        <Toc items={TOC} />
        <main className="doc-main">
          <p className="kicker">Engineering case study</p>
          <h1 className="doc-title">J.A.R.V.I.S.: many alerts, one cause, proof attached</h1>
          <p className="doc-subtitle">
            Our AI is not allowed to decide anything. It can only explain what the math proved.
          </p>

          <details className="guide" open>
            <summary>How to read this page</summary>
            <p className="guide-lede">
              This is a write-up of a deterministic root-cause engine for a simulated payments platform. Every table is
              rendered from the engine&apos;s own output.
            </p>
            <ol className="guide-steps">
              <li>
                <strong>What it does</strong> (section 2) gives the claim and the hero incident&apos;s numbers, with a
                link to the live demo.
              </li>
              <li>
                <strong>How the engine decides</strong> and <strong>Abstaining</strong> (sections 4 and 5) show the
                scoring with the real candidates, including a case where the engine declines to pick.
              </li>
              <li>
                <strong>Evaluation</strong> and <strong>What broke</strong> (sections 8 and 9) say how well it works and
                where it did not.
              </li>
            </ol>
            <p className="guide-foot">
              No number on this page is typed by hand. Scores, counts and amounts are computed from the committed
              engine output or fetched from the eval API. Hover an event id to see the raw event.
            </p>
          </details>

          {/* 1 */}
          <section className="section" id="problem">
            <h2>The problem</h2>
            <p className="section-intro">Why a pile of alerts is a bad guide to what broke.</p>
            <p>
              Our platform is a small payments stack: an API gateway, auth with a session cache, payments, a ledger, a
              PSP adapter and one postgres database. When postgres runs out of connections, it does not fail alone.
              Login, ledger writes and payments all start failing, and the login, ledger and payments teams are paged at
              once, each looking at their own red dashboard.
            </p>
            <p>
              In the hero incident, {hero.alert_count} alerts fired across {hero.services.length} services, and{' '}
              {list(heroVictims.map((c) => c.service))} all went bad within {spreadS.toFixed(1)} s of {heroRoot.service}. The
              alerts do not arrive in the order things broke: thresholds, scrape intervals and alert rules differ per
              service. Alert order is not causal order, so the engine takes onsets from metrics and logs instead.
            </p>
            <figure className="tbl">
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Onset</th>
                      <th>Service</th>
                      <th>What the engine saw</th>
                      <th>After</th>
                    </tr>
                  </thead>
                  <tbody>
                    {hero.timeline.map((t) => (
                      <tr key={`${t.ts}-${t.service}`}>
                        <td className="num nowrap">
                          <LocalTime iso={t.ts} format="time" />
                        </td>
                        <td className="svc">{t.service}</td>
                        <td>{t.what}</td>
                        <td className="svc">{t.caused_by ?? <span className="muted">first</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <figcaption>
                Hero incident timeline, opened <LocalTime iso={hero.opened_at} />. Each row is the first anomalous
                signal per service; &quot;After&quot; is the nearest failing dependency that started earlier.
              </figcaption>
            </figure>
          </section>

          {/* 2 */}
          <section className="section" id="what">
            <h2>What it does</h2>
            <p className="section-intro">The promise, and what it produced on the hero incident.</p>
            <p>
              J.A.R.V.I.S. groups related alerts into one incident, ranks root-cause candidates with a fixed weighted
              score, and attaches the raw event ids behind every number. When two causes are too close to call, it says
              so instead of guessing. A language model writes the report, but it can only rephrase what the engine
              found, and any sentence it cannot cite is removed.
            </p>
            <div className="stats">
              <div className="stat">
                <div className="stat-value">
                  {hero.alert_count} to {incidents}
                </div>
                <div className="stat-label">alerts collapsed into {incidents === 1 ? 'one incident' : 'incidents'}</div>
              </div>
              <div className="stat">
                <div className="stat-value">{hero.root_cause ? pct(hero.root_cause.confidence) : 'none'}</div>
                <div className="stat-label">confidence in {hero.root_cause?.service ?? 'no root'}, the named root cause</div>
              </div>
              <div className="stat">
                <div className="stat-value">{int(hero.impact.failed_payments)}</div>
                <div className="stat-label">failed payments, {inr(hero.impact.failed_value_inr)} in value</div>
              </div>
              <div className="stat">
                <div className="stat-value">{int(hero.impact.users_affected)}</div>
                <div className="stat-label">users affected across {hero.impact.services.length} services</div>
              </div>
            </div>
            <div className="actions">
              <a className="btn primary" href="/dashboard">
                <MonitorPlay {...ICON} />
                Open the live demo
              </a>
              <a className="btn" href="#report">
                <FileText {...ICON} />
                Read the report example
              </a>
            </div>
          </section>

          {/* 3 */}
          <section className="section" id="architecture">
            <h2>Architecture</h2>
            <p className="section-intro">Two paths: detection is deterministic and continuous; explanation runs on request.</p>
            <figure className="fig">
              <div className="flow-fig">
                <div>
                  <p className="flow-lane-title">Detection, recomputed as events arrive</p>
                  <ol className="flow">
                    {[
                      ['Ingest', 'simulator, events API, Alertmanager and Grafana webhooks'],
                      ['Event store', 'in-memory sliding window'],
                      ['Deterministic engine', 'anomaly, grouping, scoring, impact'],
                      ['SSE stream', 'GET /api/stream'],
                      ['Dashboard', 'live graph, incident, evidence'],
                    ].map(([name, note], i, all) => (
                      <li key={name} className="flow-step">
                        <span className={`flow-box ${name === 'Deterministic engine' ? 'is-key' : ''}`}>
                          <span className="flow-name">{name}</span>
                          <span className="flow-note">{note}</span>
                        </span>
                        {i < all.length - 1 && <ArrowRight {...ICON} className="flow-arrow" />}
                      </li>
                    ))}
                  </ol>
                </div>
                <div>
                  <p className="flow-lane-title">Explanation, on request</p>
                  <ol className="flow">
                    {[
                      ['Incident', 'engine output with evidence ids'],
                      ['Narrator', 'Groq, then Gemini, then template'],
                      ['Grounding', 'drops uncited sentences'],
                      ['Report', 'every sentence cites events'],
                    ].map(([name, note], i, all) => (
                      <li key={name} className="flow-step">
                        <span className={`flow-box ${name === 'Grounding' ? 'is-key' : ''}`}>
                          <span className="flow-name">{name}</span>
                          <span className="flow-note">{note}</span>
                        </span>
                        {i < all.length - 1 && <ArrowRight {...ICON} className="flow-arrow" />}
                      </li>
                    ))}
                  </ol>
                </div>
              </div>
              <figcaption>
                The engine never calls a model. The narrator only reads a finished incident, and grounding sits between it
                and the reader.
              </figcaption>
            </figure>
          </section>

          {/* 4 */}
          <section className="section" id="engine">
            <h2>How the engine decides</h2>
            <p className="section-intro">Four steps, each explainable in one line, each recording the events it used.</p>
            <ol>
              <li>
                <strong>Anomaly.</strong> Per service and signal, an EWMA baseline plus a z-score. Alerts and error-log
                bursts count directly. The onset is the first anomalous timestamp.
              </li>
              <li>
                <strong>Grouping.</strong> Anomalous services close in time and connected in the dependency graph form
                one incident. Disconnected failures stay separate.
              </li>
              <li>
                <strong>Scoring.</strong> Every anomalous service is a candidate with four weighted components:{' '}
                <em>upstream</em> (share of failing services that call it, up to {w(ENGINE_WEIGHTS.upstream)}),{' '}
                <em>precedence</em> (share of its failing callers that started later, up to{' '}
                {w(ENGINE_WEIGHTS.precedence)}), <em>change</em> (a deploy or config change shortly before its onset, up
                to {w(ENGINE_WEIGHTS.change)}) and <em>severity</em> (its peak anomaly relative to the incident&apos;s
                largest, up to {w(ENGINE_WEIGHTS.severity)}).
              </li>
              <li>
                <strong>Victim penalty.</strong> If a candidate has a failing dependency of its own, it is probably a
                victim, so its sum is multiplied by {VICTIM_PENALTY}.
              </li>
            </ol>
            <p>
              <code>score = (upstream + precedence + change + severity) × penalty</code>
            </p>
            <figure className="tbl">
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Candidate</th>
                      <th className="num">
                        Upstream<span className="th-sub">max {w(ENGINE_WEIGHTS.upstream)}</span>
                      </th>
                      <th className="num">
                        Precedence<span className="th-sub">max {w(ENGINE_WEIGHTS.precedence)}</span>
                      </th>
                      <th className="num">
                        Change<span className="th-sub">max {w(ENGINE_WEIGHTS.change)}</span>
                      </th>
                      <th className="num">
                        Severity<span className="th-sub">max {w(ENGINE_WEIGHTS.severity)}</span>
                      </th>
                      <th className="num">Sum</th>
                      <th className="num">Penalty</th>
                      <th className="num">Score</th>
                    </tr>
                  </thead>
                  <tbody>
                    {hero.candidates.map((c) => (
                      <tr key={c.service} className={c.service === hero.root_cause?.service ? 'is-root' : undefined}>
                        <td className="svc">
                          {c.service}
                          {c.victim_of && <span className="cell-sub">victim of {c.victim_of}</span>}
                        </td>
                        <td className="num">{w(c.components.upstream)}</td>
                        <td className="num">{w(c.components.precedence)}</td>
                        <td className="num">{w(c.components.change)}</td>
                        <td className="num">{w(c.components.severity)}</td>
                        <td className="num">{w(sum(c))}</td>
                        <td className="num">
                          {c.penalty < 1 ? `×${c.penalty}` : 'none'}
                        </td>
                        <td className="num">
                          <strong>{w(c.score)}</strong>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <figcaption>
                Hero incident candidates as the engine scored them. The highlighted row is the named root cause,{' '}
                {heroRoot.service} ({heroRoot.signature}).
              </figcaption>
            </figure>
            <p>
              Look at {heroVictims[0]?.service}: its raw sum is {w(heroVictims[0] ? sum(heroVictims[0]) : 0)}, but it
              depends on {heroVictims[0]?.victim_of}, which is failing too, so the penalty halves it to{' '}
              {w(heroVictims[0]?.score ?? 0)}. {heroRoot.service} has no failing dependency, every other failing service
              sits above it, and it went first, so it keeps its full {w(heroRoot.score)}.
            </p>
          </section>

          {/* 5 */}
          <section className="section" id="abstain">
            <h2>Abstaining instead of guessing</h2>
            <p className="section-intro">When the math cannot separate two causes, the incident says so and asks for data.</p>
            <p>
              The rule: if the top score is below {ABSTAIN_MIN_SCORE}, or the top two are less than {ABSTAIN_MIN_MARGIN}{' '}
              apart, the incident is <span className="badge badge-amber">ABSTAINED</span> and no root cause is named.
              The recommendations stay cheap and reversible.
            </p>
            <p>
              In the ambiguous incident, {ambEarlier.service} started first, but {ambTop1.service} has the most
              upstream weight. After the penalty they land at {s3(ambTop1.score)} and {s3(ambTop2.score)}, a margin of{' '}
              {s3(ambMargin)}.
            </p>
            <figure className="tbl">
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Candidate</th>
                      <th className="num">Upstream</th>
                      <th className="num">Precedence</th>
                      <th className="num">Change</th>
                      <th className="num">Severity</th>
                      <th className="num">Penalty</th>
                      <th className="num">Score</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[ambTop1, ambTop2].map((c) => (
                      <tr key={c.service} className="is-top">
                        <td className="svc">{c.service}</td>
                        <td className="num">{s3(c.components.upstream)}</td>
                        <td className="num">{s3(c.components.precedence)}</td>
                        <td className="num">{s3(c.components.change)}</td>
                        <td className="num">{s3(c.components.severity)}</td>
                        <td className="num">{c.penalty < 1 ? `×${c.penalty}` : 'none'}</td>
                        <td className="num">
                          <strong>{s3(c.score)}</strong>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <figcaption>
                Top two of the ambiguous incident, status {amb.status}. Engine&apos;s reason: {amb.abstain_reason}.
              </figcaption>
            </figure>
            <h3>What would settle it</h3>
            <p className="section-intro">The incident lists the data that would separate the two, instead of a guess.</p>
            <ul>
              {amb.disambiguation.map((d) => (
                <li key={d}>{d}</li>
              ))}
            </ul>
          </section>

          {/* 6 */}
          <section className="section" id="why-not">
            <h2>Why not X? and evidence</h2>
            <p className="section-intro">
              Each ruled-out candidate gets one deterministic sentence with the engine&apos;s numbers and the events behind it.
            </p>
            <ul className="rows">
              {ruledOut.map((c) => (
                <li key={c.service} className="row">
                  <div className="row-head">
                    <CircleAlert {...SMALL} className="row-kind" />
                    <h3>Why not {c.service}?</h3>
                    <span className="badge">ruled out</span>
                  </div>
                  <p className="row-text">{c.ruled_out_reason}</p>
                  <Cites ids={c.ruled_out_evidence} />
                </li>
              ))}
            </ul>
            <p>
              The same holds for the winner. {heroRoot.service}&apos;s score rests on{' '}
              {heroRoot.component_evidence.upstream.length} upstream, {heroRoot.component_evidence.precedence.length}{' '}
              precedence and {heroRoot.component_evidence.severity.length} severity events, and the incident carries all{' '}
              {hero.evidence_events.length} referenced events resolved, so any claim can be opened to the raw record.
            </p>
            <Cites ids={heroRoot.evidence} />
          </section>

          {/* 7 */}
          <section className="section" id="narrator">
            <h2>The LLM&apos;s narrow job</h2>
            <p className="section-intro">The model narrates a finished incident. It cannot change the answer.</p>
            <ul>
              <li>
                <strong>Narrate only.</strong> The model gets the engine&apos;s incident and writes short sentences, each
                with the event ids it relies on.
              </li>
              <li>
                <strong>Grounding.</strong> A sentence is dropped if it has no cites, cites an event that is not in the
                incident&apos;s evidence, or names a cause other than the engine&apos;s root.
              </li>
              <li>
                <strong>Fallback.</strong> Groq first, then Gemini, then a deterministic template built from engine
                output, all inside one 8 s budget. The report endpoint never fails because of a model.
              </li>
            </ul>
            <figure className="fig" id="report" style={{ scrollMarginTop: 88 }}>
              <div className="report">
                <div className="report-head">
                  <span>
                    Report for {report.incident_id}, provider {report.provider}
                  </span>
                  <span className={`badge ${report.dropped ? 'badge-amber' : 'badge-green'}`}>
                    <ShieldCheck {...SMALL} />
                    {report.dropped
                      ? `Guardrail blocked ${report.dropped} uncited sentence${report.dropped === 1 ? '' : 's'}`
                      : 'Guardrail: every sentence cited'}
                  </span>
                </div>
                <ol>
                  <li>
                    <strong>{report.summary}</strong>
                  </li>
                  {report.sentences.map((x) => (
                    <li key={x.text}>
                      {x.text}
                      <Cites ids={x.cites} />
                    </li>
                  ))}
                </ol>
              </div>
              <figcaption>
                The committed hero report: {report.sentences.length} sentences citing {reportCites.size} distinct events,{' '}
                {allCitesResolve ? 'all of which resolve to raw events in the incident' : 'some of which do not resolve'}.
                Generated <LocalTime iso={report.generated_at} />.
              </figcaption>
            </figure>
          </section>

          {/* 8 */}
          <section className="section" id="evaluation">
            <h2>Evaluation</h2>
            <p className="section-intro">
              Blind runs against simple baselines: latest deploy, most alerts, and an LLM reading the raw events.
            </p>
            <div className="callout callout-info">
              <Info {...ICON} />
              <p>
                Evaluated on our own simulator with randomized blind faults; RCAEval real-data numbers are a final-round
                item.
              </p>
            </div>
            <EvalTable />
            <p>
              Weights were frozen before the final eval. Any tuning uses the first half of the seeds, and only the second
              half is reported. The LLM baseline gets a neutral prompt written before any results, at temperature 0.
            </p>
          </section>

          {/* 9 */}
          <section className="section" id="failures">
            <h2>What broke and what we learned</h2>
            <p className="section-intro">Real entries from our failure log, each with the lesson we kept.</p>
            <ul className="rows">
              {FAILURES.map((f) => (
                <li key={f.title} className="row">
                  <div className="row-head">
                    <Bug {...SMALL} className="row-kind" />
                    <h3>{f.title}</h3>
                  </div>
                  <p className="row-text">{f.text}</p>
                  <p className="row-text">
                    <CircleCheck {...SMALL} style={{ verticalAlign: '-2px', color: 'var(--safe)' }} />{' '}
                    <strong>Lesson:</strong> {f.lesson}
                  </p>
                </li>
              ))}
            </ul>
          </section>

          {/* 10 */}
          <section className="section" id="limits">
            <h2>Limits and roadmap</h2>
            <p className="section-intro">What this does not prove yet, and what comes next.</p>
            <ul>
              <li>
                <strong>Simulator-only evaluation.</strong> Our simulator and engine were written by the same team. Blind
                randomized faults and decoy deploys reduce that bias but do not remove it.
              </li>
              <li>
                <strong>RCAEval.</strong> Running the engine on the public RCAEval benchmarks is the first final-round
                item.
              </li>
              <li>
                <strong>OpenTelemetry ingest.</strong> Today we take our own event schema plus Alertmanager and Grafana
                webhooks. Traces would also settle onset order, which is exactly what the abstain case asks for.
              </li>
              <li>
                <strong>Slack and PagerDuty.</strong> Post the incident, its evidence and the abstain reason where the
                on-call already is.
              </li>
              <li>
                <strong>Known engine gaps.</strong> Two concurrent faults that share the gateway merge into one incident,
                and a fault that outlasts the store window loses its baseline.
              </li>
            </ul>
          </section>

          <footer className="doc-foot">
            <p>
              Built for NITS Hacks 9.0, software track. Source on <a href={REPO_URL}>GitHub</a>. Hero incident{' '}
              <span className="mono">{hero.id}</span>, last updated <LocalTime iso={hero.updated_at} />.
            </p>
          </footer>
        </main>
      </div>
    </>
  );
}
