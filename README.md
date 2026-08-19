# RoadProof

Multi-purpose, evidence-first road-type analysis for European routes.

RoadProof accepts a Google Maps route link, resolves its ordered route request,
and keeps that identity separate from an imported exact-track file. It is
designed to match supplied geometry against country-specific official road data
and report distance by defensible road categories. The same evidence layer can
support compliance testing, route design, fleet QA and research.

## Product status

RoadProof now combines a Windows PowerShell-first analyzer with the existing
web evidence-intake interface. The PowerShell tool:

- resolves a shared Google Maps route link;
- reads the route selected by Google instead of re-routing its waypoints;
- reconciles Google's exact route total with its maneuver distances;
- identifies the route by a link-independent maneuver-level evidence fingerprint;
- prints a colored Highway / Country / City / Unresolved table in PowerShell;
- writes the same detailed result to a timestamped Markdown report;
- reuses a retained official-road analysis only when that evidence fingerprint matches.

The included Denmark evidence package covers the verified Copenhagen loop.
Other links still receive a confirmed Google total, but their entire distance
remains `Unresolved` until an official country-adapter evidence package matches.
RoadProof never substitutes an invented classification.

The web interface separately supports safe Google request resolution and local
GPX, GeoJSON, and KML import. A maneuver-level evidence fingerprint does not
claim to be a full polyline hash; imported exact-track identity and Google
route evidence remain visibly distinct.

## Classification policy

| Product label | EU 2021/1958 test bucket |
| --- | --- |
| Highway | motorways / expressways / dual carriageways |
| Country | non-urban roads |
| City | urban roads and streets |
| Unresolved | insufficient or conflicting evidence |

The legal text defines the route buckets, not a universal GIS implementation.
See [the complete policy](docs/CLASSIFICATION_POLICY.md) for the segment rules
and country-adapter evidence contract.

## Core safeguards

- Never re-route from waypoints when exact Google-selected geometry is needed.
- Never classify from speed limit alone.
- Never promote an OSM-only result to `Confirmed`.
- Never silently allocate unresolved kilometres.
- Always show road-network evidence separately from compliance profiles.

## Multi-purpose profiles

RoadProof keeps classification evidence independent from downstream rules.
The interface currently provides:

- **Composition only** — reports the measured road mix without pass/fail claims;
- **EU ISA 2021/1958** — applies the published 400 km, 25% per road type and
  15% darkness profile.

Additional public or organization-specific profiles can be added without
changing the underlying road evidence.

## Install on Windows

Clone or download this repository, open PowerShell in its folder, and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\installer.ps1
```

`installer.ps1` detects Python 3.10 or newer. If Python is missing, it installs
Python 3.12 for the current user through `winget`, creates an isolated `.venv`,
installs the declared requirements, and runs a self-check. No administrator
rights, Windows application installer, Electron, or macOS package is used.

## Analyze a route

Interactive use:

```powershell
.\start.ps1
```

Paste the Google Maps route link when prompted. Or pass it directly:

```powershell
.\start.ps1 "https://maps.app.goo.gl/rbGN7YeiTqY5E74n9"
```

The default report includes the EU ISA profile checks. For composition only:

```powershell
.\start.ps1 "https://maps.app.goo.gl/rbGN7YeiTqY5E74n9" -Profile composition
```

Reports are saved under `reports\roadproof-<timestamp>-<fingerprint>.md`.

## Web interface

The responsive web interface remains a secondary prototype. Its local
development requirements are Node.js 22.13 or newer:

```bash
npm ci
npm run dev
```

Open the local URL printed by the development server. Use **Load stored Denmark
pilot** to inspect the legacy evidence record.

## Validate

```bash
python -m unittest tests.test_cli -v
npm run lint
npm run test:unit
npm run build
npm run validate:artifact
```

## Official source

- [Commission Delegated Regulation (EU) 2021/1958](https://eur-lex.europa.eu/eli/reg_del/2021/1958/2023-09-21/eng), especially point 4.3.1.3.

## Privacy and credentials

The PowerShell tool requests no Google login, Google API key, private token, or
user location. It sends the supplied public link to Google Maps and stores only
the generated Markdown report locally. Future country adapters must use public
official datasets or document any optional credential separately.
