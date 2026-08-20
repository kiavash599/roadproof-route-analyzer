"use client";

import { FormEvent, useState } from "react";

const SAMPLE_URL = "https://maps.app.goo.gl/HnrF4ACcqeiERj82A";
type ViewState = "empty" | "sample" | "resolving" | "result" | "invalid";
type AnalysisProfile = "composition" | "eu-isa";
type AnalysisDocument = {
  schema: string;
  status: string;
  route: { name: string; origin?: string; destination?: string; distance_m: number; duration_s: number; maneuver_count: number; countries: string[]; route_fingerprint: string; automatic_geometry?: { status?: string; accepted?: number; rejected?: unknown[] } };
  evidence: { available: boolean; message?: string; adapter?: string; matched_maneuvers?: number; breakdown: Array<{ category: string; distance_m: number; share: number; official_layer: string; status: string }> };
  profile: { status: string; checks: Array<{ check: string; state: string; measured_m: number | null; target_m: number }> };
};

const routeSegments = [
  { label: "Signed motorway", km: 160.718, percent: 61.28, tone: "highway" },
  { label: "Outside built-up signs", km: 84.443, percent: 32.19, tone: "country" },
  { label: "Inside built-up signs", km: 8.127, percent: 3.1, tone: "city" },
  { label: "Unresolved", km: 9.001, percent: 3.43, tone: "unknown" },
];

const classificationRows = [
  { product: "Highway", legal: "Motorways, expressways & dual carriageways", proof: "Official designation or official carriageway attributes", color: "var(--violet)" },
  { product: "Country", legal: "Non-urban roads", proof: "Official national non-urban status", color: "var(--lime)" },
  { product: "City", legal: "Urban roads & streets", proof: "Official national urban status", color: "var(--cyan)" },
  { product: "Unresolved", legal: "Missing, conflicting or unsupported evidence", proof: "Never redistributed into another bucket", color: "var(--muted)" },
];

function Icon({ name, size = 18 }: { name: string; size?: number }) {
  const common = { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  if (name === "arrow") return <svg {...common}><path d="M5 12h14M13 6l6 6-6 6" /></svg>;
  if (name === "route") return <svg {...common}><circle cx="6" cy="18" r="2" /><circle cx="18" cy="6" r="2" /><path d="M8 18h3a3 3 0 0 0 3-3V9a3 3 0 0 1 3-3" /></svg>;
  if (name === "check") return <svg {...common}><path d="m5 12 4 4L19 6" /></svg>;
  if (name === "shield") return <svg {...common}><path d="M12 3 5 6v5c0 4.6 2.8 8 7 10 4.2-2 7-5.4 7-10V6l-7-3Z" /><path d="m9 12 2 2 4-4" /></svg>;
  if (name === "globe") return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" /></svg>;
  if (name === "link") return <svg {...common}><path d="M10 13a5 5 0 0 0 7.1.1l2-2a5 5 0 0 0-7.1-7.1l-1.1 1.1M14 11a5 5 0 0 0-7.1-.1l-2 2A5 5 0 0 0 12 20l1.1-1.1" /></svg>;
  if (name === "alert") return <svg {...common}><path d="M12 4 3 20h18L12 4Z" /><path d="M12 9v5M12 17h.01" /></svg>;
  return null;
}

function isGoogleMapsUrl(value: string) {
  try {
    const host = new URL(value.trim()).hostname.toLowerCase();
    return host === "maps.app.goo.gl" || host === "goo.gl" || host === "google.com" || host.endsWith(".google.com");
  } catch { return false; }
}

export default function AnalyzerClient() {
  const [url, setUrl] = useState("");
  const [view, setView] = useState<ViewState>("empty");
  const [profile, setProfile] = useState<AnalysisProfile>("composition");
  const [result, setResult] = useState<AnalysisDocument | null>(null);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!isGoogleMapsUrl(url)) { setError("Enter a supported Google Maps route URL."); setView("invalid"); return; }
    setView("resolving"); setError(""); setResult(null);
    try {
      const response = await fetch("http://127.0.0.1:8765/api/analyze", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ url: url.trim(), profile }),
      });
      const data = await response.json() as AnalysisDocument & { error?: string };
      if (!response.ok || data.schema !== "roadproof.result.v1") throw new Error(data.error || "The route could not be analyzed.");
      setResult(data); setView("result");
      window.setTimeout(() => document.getElementById("result")?.scrollIntoView({ behavior: "smooth", block: "start" }), 80);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The route could not be analyzed.");
      setView("invalid");
    }
  }

  function loadSample() {
    setUrl(SAMPLE_URL); setResult(null); setError(""); setView("sample");
    window.setTimeout(() => document.getElementById("result")?.scrollIntoView({ behavior: "smooth", block: "start" }), 80);
  }

  return <main>
    <header className="site-header">
      <a className="brand" href="#top" aria-label="RoadProof home"><span className="brand-mark"><Icon name="route" size={19} /></span><span>RoadProof<span className="brand-dot">.</span></span></a>
      <nav aria-label="Primary navigation"><a href="#policy">Classification policy</a><a href="#evidence">Evidence model</a><a href="#coverage">Europe coverage</a></nav>
      <span className="policy-pill"><span className="live-dot" /> Policy v1.0</span>
    </header>

    <section className="hero" id="top">
      <div className="hero-grid" /><div className="hero-orb hero-orb-one" /><div className="hero-orb hero-orb-two" />
      <div className="hero-inner">
        <div className="eyebrow"><Icon name="shield" size={15} /> Multi-purpose route intelligence</div>
        <h1>Know the road mix.<br /><span>Prove every kilometre.</span></h1>
        <p className="hero-copy">Paste a Google Maps route link. RoadProof reconstructs road-following geometry, checks it against Google maneuver distances, and classifies the route with official country data.</p>
        <form className="route-form" onSubmit={submit}>
          <label htmlFor="route-url">Google Maps route link</label>
          <div className="profile-row" role="group" aria-label="Analysis profile">
            <span>Analysis profile</span>
            <button type="button" className={profile === "composition" ? "active" : ""} onClick={() => setProfile("composition")}>Composition only</button>
            <button type="button" className={profile === "eu-isa" ? "active" : ""} onClick={() => setProfile("eu-isa")}>EU ISA 2021/1958</button>
          </div>
          <div className={`input-shell ${view === "invalid" ? "input-error" : ""}`}>
            <span className="input-icon"><Icon name="link" /></span>
            <input id="route-url" value={url} onChange={(event) => { setUrl(event.target.value); if (view === "invalid") { setView("empty"); setError(""); } }} placeholder="https://maps.app.goo.gl/…" spellCheck={false} />
            <button type="submit" disabled={view === "resolving"}>{view === "resolving" ? "Analyzing route…" : "Analyze route"} <Icon name="arrow" size={17} /></button>
          </div>
          <div className="form-meta"><span className={view === "invalid" ? "error-copy" : ""}>{view === "invalid" ? error : "No Google account, API key or private token required."}</span><button type="button" className="text-button" onClick={loadSample}>Load stored Denmark pilot</button></div>
        </form>
        <div className="trust-row"><span><Icon name="check" size={15} /> No track upload required</span><span><Icon name="check" size={15} /> Official data first</span><span><Icon name="check" size={15} /> Ambiguity stays visible</span></div>
      </div>
    </section>

    {view === "sample" && <SampleResult profile={profile} />}
    {view === "result" && result && <LiveResult result={result} profile={profile} />}

    <section className="section policy-section" id="policy">
      <div className="section-heading"><div><span className="section-kicker">01 / classification policy</span><h2>One European rule.<br />Country-specific proof.</h2></div><p>The legal buckets come from Commission Delegated Regulation (EU) 2021/1958. The regulation names the three required road types but does not provide one universal GIS tag recipe, so each country must supply its own auditable adapter.</p></div>
      <div className="policy-table"><div className="policy-table-head"><span>Product label</span><span>Official EU test bucket</span><span>Required segment evidence</span></div>{classificationRows.map((row) => <div className="policy-row" key={row.product}><span className="policy-name"><i style={{ background: row.color }} />{row.product}</span><strong>{row.legal}</strong><span>{row.proof}</span></div>)}</div>
      <div className="legal-note"><Icon name="alert" size={18} /><p><strong>Important:</strong> a speed limit alone does not determine road type. An OSM tag may support an inference, but only an official national source can create a confirmed segment.</p></div>
    </section>

    <section className="section evidence-section" id="evidence">
      <div className="section-heading compact"><div><span className="section-kicker">02 / evidence model</span><h2>Every answer carries its confidence.</h2></div><p>A defensible report keeps observed road-network facts separate from any selected compliance profile or downstream interpretation.</p></div>
      <div className="evidence-grid">
        <article className="evidence-card confirmed"><span className="status-icon"><Icon name="check" size={18} /></span><div><span className="card-label">Confirmed</span><h3>Official and traceable</h3><p>Matched route segment, named official dataset, recorded field value, retrieval time and source URL.</p></div></article>
        <article className="evidence-card inferred"><span className="status-icon">≈</span><div><span className="card-label">Inferred</span><h3>Supported, not official</h3><p>Strong network evidence exists, but an official classification attribute is absent or incomplete.</p></div></article>
        <article className="evidence-card unresolved"><span className="status-icon">?</span><div><span className="card-label">Unresolved</span><h3>No silent allocation</h3><p>Conflicts, weak matches and unsupported countries remain visible and count toward no test bucket.</p></div></article>
      </div>
    </section>

    <section className="section coverage-section" id="coverage">
      <div className="coverage-copy"><span className="section-kicker">03 / Europe coverage</span><h2>Built for borders,<br />not one country.</h2><p>RoadProof can dispatch reconciled exact-track edges to country-specific adapters when each edge has one unambiguous supported-country guard. Border overlaps, transitions and unsupported countries remain explicitly unresolved.</p><div className="coverage-list"><span><Icon name="globe" size={17} /> Country-neutral canonical road model</span><span><Icon name="route" size={17} /> Conservative exact-track country dispatch</span><span><Icon name="shield" size={17} /> Ambiguous edges return Unresolved</span></div></div>
      <EuropeCard />
    </section>

    <section className="requirements-strip"><div><span className="big-number">Exact</span><span>selected route<br />geometry first</span></div><div><span className="big-number">3</span><span>explicit evidence<br />confidence states</span></div><div><span className="big-number">EU</span><span>country-specific<br />official adapters</span></div><div><span className="big-number">0</span><span>silent assumptions<br />or forced allocation</span></div></section>
    <footer><a className="brand" href="#top"><span className="brand-mark"><Icon name="route" size={19} /></span><span>RoadProof<span className="brand-dot">.</span></span></a><p>Evidence-first route intelligence for planning, testing and research.</p><a href="https://eur-lex.europa.eu/eli/reg_del/2021/1958/2023-09-21/eng" target="_blank" rel="noreferrer">EU road-type reference ↗</a></footer>
  </main>;
}

function SampleResult({ profile }: { profile: AnalysisProfile }) {
  const compliance = profile === "eu-isa";
  return <section className="section result-section" id="result"><div className="result-topbar"><div><span className="result-state"><Icon name="check" size={14} /> Stored evidence record</span><h2>Copenhagen loop</h2><p>Arne Jacobsens Allé 2 · Denmark · Same start and end</p></div><div className="result-total"><span>Stored route total</span><strong>262.290 <small>km</small></strong><em>{compliance ? "137.710 km below selected profile" : "Legacy maneuver total"}</em></div></div><div className="result-body"><div className="chart-panel"><div className="panel-title"><span>Road-network evidence</span><span className="subtle">Conservative Denmark pilot</span></div><div className="route-bar" aria-label="Road type distribution">{routeSegments.map((segment) => <span key={segment.label} className={segment.tone} style={{ width: `${segment.percent}%` }} title={`${segment.label}: ${segment.percent}%`} />)}</div><div className="segment-list">{routeSegments.map((segment) => <div className="segment-row" key={segment.label}><span className={`legend-dot ${segment.tone}`} /><span>{segment.label}</span><strong>{segment.km.toFixed(3)} km</strong><em>{segment.percent.toFixed(2)}%</em></div>)}</div><div className="sample-warning"><Icon name="alert" size={17} /><p>This is a manually loaded legacy pilot, not a live result for the pasted URL. Its exact geometry hash was not stored, so RoadProof never reuses it merely because another link has the same stops.</p></div></div>{compliance ? <div className="threshold-panel"><div className="panel-title"><span>EU ISA 2021/1958 profile</span><span className="subtle">400 km · 25% each · 15% darkness</span></div><Threshold label="Total distance" value="262.290 / 400 km" percent={65.57} pass={false} /><Threshold label="Signed motorway only" value="160.718 / 100 km" percent={100} pass /><Threshold label="Outside built-up signs" value="84.443 / 100 km" percent={84.44} pass={false} /><Threshold label="Inside built-up signs" value="8.127 / 100 km" percent={8.13} pass={false} /><Threshold label="Darkness" value="Not measured" percent={0} pass={false} unresolved /><div className="overall-fail"><Icon name="alert" size={17} /><div><strong>Stored route does not meet this profile</strong><span>Total distance fails regardless of category mapping.</span></div></div></div> : <div className="threshold-panel"><div className="panel-title"><span>Evidence coverage</span><span className="subtle">No compliance thresholds applied</span></div><Threshold label="Stored maneuver distance" value="262.290 km" percent={100} pass /><Threshold label="Signed motorway evidence" value="160.718 km" percent={61.28} pass /><Threshold label="Built-up status allocation" value="92.570 km" percent={35.29} pass /><Threshold label="Unresolved distance" value="9.001 km" percent={3.43} pass={false} unresolved /><div className="overall-neutral"><Icon name="shield" size={17} /><div><strong>Composition report only</strong><span>Select a compliance profile to evaluate thresholds.</span></div></div></div>}</div></section>;
}

function Threshold({ label, value, percent, pass, unresolved = false }: { label: string; value: string; percent: number; pass: boolean; unresolved?: boolean }) {
  return <div className="threshold-row"><div><span>{label}</span><strong>{value}</strong></div><div className="threshold-track"><i className={pass ? "pass" : unresolved ? "pending" : "fail"} style={{ width: `${Math.min(percent, 100)}%` }} /></div><em className={pass ? "pass-copy" : unresolved ? "pending-copy" : "fail-copy"}>{pass ? "PASS" : unresolved ? "UNRESOLVED" : "SHORT"}</em></div>;
}

function LiveResult({ result, profile }: { result: AnalysisDocument; profile: AnalysisProfile }) {
  const total = result.route.distance_m;
  const rows = result.evidence.breakdown;
  const geometry = result.route.automatic_geometry;
  const rejected = geometry?.rejected?.length ?? 0;
  const labels: Record<string, string> = { Highway: "Highway", Country: "Country", City: "City", Unresolved: "Unresolved" };
  const tones: Record<string, string> = { Highway: "highway", Country: "country", City: "city", Unresolved: "unknown" };
  return <section className="section result-section" id="result">
    <div className="result-topbar">
      <div><span className="result-state"><Icon name="check" size={14} /> Live analysis complete</span><h2>{result.route.name}</h2><p>{result.route.origin} → {result.route.destination} · {result.route.countries.join(", ") || "Country unresolved"}</p></div>
      <div className="result-total"><span>Google route total</span><strong>{(total / 1000).toFixed(3)} <small>km</small></strong><em>{result.status === "complete" ? "Complete evidence coverage" : "Unresolved distance remains visible"}</em></div>
    </div>
    <div className="result-body">
      <div className="chart-panel">
        <div className="panel-title"><span>Road-type composition</span><span className="subtle">{result.evidence.adapter || "No supported adapter"}</span></div>
        <div className="route-bar" aria-label="Road type distribution">{rows.map((row) => <span key={row.category} className={tones[row.category] || "unknown"} style={{ width: `${total ? 100 * row.distance_m / total : 0}%` }} title={`${row.category}: ${(row.distance_m / 1000).toFixed(3)} km`} />)}</div>
        <div className="segment-list">{rows.map((row) => <div className="segment-row" key={row.category}><span className={`legend-dot ${tones[row.category] || "unknown"}`} /><span>{labels[row.category] || row.category}</span><strong>{(row.distance_m / 1000).toFixed(3)} km</strong><em>{(total ? 100 * row.distance_m / total : 0).toFixed(2)}%</em></div>)}</div>
        <div className="sample-warning"><Icon name="route" size={17} /><p>Automatic road geometry accepted {geometry?.accepted ?? 0} Google maneuver(s); {rejected} remained on conservative fallback geometry. No GPX upload was required.</p></div>
      </div>
      <div className="threshold-panel">
        <div className="panel-title"><span>{profile === "eu-isa" ? "EU ISA 2021/1958 profile" : "Evidence coverage"}</span><span className="subtle">{result.evidence.message || result.status}</span></div>
        {profile === "eu-isa" ? result.profile.checks.map((check) => <Threshold key={check.check} label={check.check.replaceAll("_", " ")} value={check.measured_m === null ? "Not measured" : `${(check.measured_m / 1000).toFixed(3)} / ${(check.target_m / 1000).toFixed(3)} km`} percent={check.measured_m === null || check.target_m === 0 ? 0 : 100 * check.measured_m / check.target_m} pass={check.state === "meets"} unresolved={check.state !== "meets" && check.state !== "fails"} />) : rows.map((row) => <Threshold key={row.category} label={row.category} value={`${(row.distance_m / 1000).toFixed(3)} km`} percent={total ? 100 * row.distance_m / total : 0} pass={row.category !== "Unresolved" && row.distance_m > 0} unresolved={row.category === "Unresolved"} />)}
        <div className={result.status === "complete" ? "overall-neutral" : "overall-fail"}><Icon name={result.status === "complete" ? "shield" : "alert"} size={17} /><div><strong>{result.status === "complete" ? "Classification complete" : "Partial evidence result"}</strong><span>Uncertain kilometres are never silently reassigned.</span></div></div>
      </div>
    </div>
  </section>;
}


function EuropeCard() {
  return <div className="europe-card" aria-label="European adapter status"><div className="map-lines"><span /><span /><span /><span /><span /></div><div className="europe-card-top"><span><Icon name="globe" size={18} /> Adapter registry</span><strong>Europe</strong></div><div className="adapter-row active"><span className="flag">DK</span><div><strong>Denmark</strong><small>Vejman pilot adapter</small></div><em>Verified pilot</em></div><div className="adapter-row"><span className="flag">EU</span><div><strong>EU / EEA countries</strong><small>Country-specific official source required</small></div><em>Registry ready</em></div><div className="adapter-row"><span className="flag">↔</span><div><strong>Cross-border routes</strong><small>Split, classify, recombine</small></div><em>Core ready</em></div><div className="adapter-foot"><span className="live-dot" /> Coverage is evidence-based, never claimed by geography alone.</div></div>;
}
