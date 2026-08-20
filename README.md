# RoadProof

## Quick start

You need Git and an internet connection. The platform installer checks for
CPython 3.10 through 3.14, installs it when supported and missing,
creates an isolated `.venv`, and installs RoadProof's pinned requirements from
official binary wheels where a compiled runtime is required.

### Windows (PowerShell)

Open PowerShell and run:

```powershell
git clone https://github.com/kiavash599/roadproof-route-analyzer.git
cd roadproof-route-analyzer
powershell -ExecutionPolicy Bypass -File .\installer.ps1
.\start.ps1
```

Rerunning `installer.ps1` is safe and repairs an existing environment after a
Python or dependency upgrade. In particular, it replaces experimental MinGW
NumPy builds with the official wheel before running RoadProof's self-check.

Paste either a short Google Maps share link or the full directions URL when
prompted. Command Prompt and double-click users can run `start.bat` instead.

### macOS or Linux (Terminal)

Open Terminal and run:

```bash
git clone https://github.com/kiavash599/roadproof-route-analyzer.git
cd roadproof-route-analyzer
bash ./installer.sh
./start.sh
```

Paste either Google Maps link format when prompted. RoadProof shows the result
in colored terminal tables and saves the detailed Markdown report under
`reports/`. The first analysis of a supported-country corridor can take longer
while RoadProof downloads and caches the relevant official data; later runs
reuse the seven-day local cache.

## Overview

Multi-purpose, evidence-first road-type analysis for European routes.

RoadProof accepts a Google Maps route link, resolves its ordered route request,
and keeps that identity separate from an imported exact-track file. It is
designed to match supplied geometry against country-specific official road data
and report distance by defensible road categories. The same evidence layer can
support compliance testing, route design, fleet QA and research.

## Product status

RoadProof now combines a cross-platform terminal analyzer with a local web
interface backed by the same analysis engine. The analyzer:

- accepts short shared links and full Google Maps address-bar route URLs;
- resolves the supplied Google Maps route link safely;
- reads the route selected by Google instead of re-routing its waypoints;
- reconciles Google's exact route total with its maneuver distances;
- identifies the route by a link-independent maneuver-level evidence fingerprint;
- prints styled, adaptive Highway / Country / City / Unresolved tables on
  Windows, macOS and Linux;
- writes the same detailed result to a meaningful, uniquely named Markdown report;
- analyzes previously unseen routes wholly inside Denmark, Germany, Sweden or
  Belgium at runtime against the corresponding official data adapter;
- reuses a retained official-road analysis when an exact evidence fingerprint matches.

Runtime adapters cover routes wholly inside Denmark (`DK`), Germany (`DE`),
Sweden (`SE`) and Belgium (`BE`); they do not require a pre-extracted geometry
package for every route. A multi-country route can be split conservatively when
an imported exact track reconciles with Google distance/endpoints and contains
at most 1,000 edges. An edge is dispatched only when every point sampled at no
more than 2 km intervals passes one unambiguous supported-country boundary from
Eurostat GISCO Countries 2024; transitions, ambiguities, unsupported countries
and failed adapters remain `Unresolved`. GISCO geometry is an official
statistical reference dataset, not a legal border determination. Without a
reconciled track, cross-border road type remains entirely unresolved. RoadProof
never substitutes an invented classification.

Google's private directions response does not always include its country-code
array. When that happens, RoadProof may select one supported adapter only if
the country name exposed by the route URL and every maneuver coordinate pass a
single conservative country envelope. Overlapping or border cases remain
unresolved. This fallback selects an adapter; it is never treated as road-class
or legal-boundary evidence. The console and Markdown report show which country
signal was used.

The local web interface accepts the Google Maps link directly, runs automatic
per-maneuver geometry reconstruction and the same official country adapter as
the terminal, and displays the live Highway / Country / City / Unresolved
result. It does not require a GPX upload. Exact-track import remains an optional
terminal capability for workflows that already possess such a file.

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

## Runtime country adapters

Each adapter uses a seven-day local cache and records its adapter version,
official endpoints, retrieval time, inferred mappings and unresolved reasons
in the Markdown report.

### Denmark

For an all-Denmark route without a retained fingerprint match, RoadProof:

1. requests only the official data intersecting the route corridors from the
   public Vejdirektoratet Vejman WFS;
2. builds a local graph and matches every Google maneuver independently;
3. accepts endpoints only within 120 m and paths only when their length differs
   from Google's maneuver distance by at most `max(100 m, 6%)`;
4. assigns motorway and expressway from Vejman's signed `Motorvej` and
   `Motortrafikvej` values, followed by documented built-up/non-urban fields;
5. uses the official Plandata `Byzone`/`Landzone` polygons only as a fallback
   where Vejman lacks decisive attributes; and
6. leaves every failed or ambiguous maneuver `Unresolved` instead of spreading
   its distance across the matched classes.

Plandata's planning zones are official source data, but their translation to
the EU `City`/`Country` buckets is an explicit RoadProof inference. The exact
Google distance is likewise allocated in proportion to accepted official path
segments. The report labels both facts as inferred.

No API key is needed. Official WFS responses are cached for seven days under
`%LOCALAPPDATA%\RoadProof\cache\denmark-wfs` on Windows and the standard XDG
cache directory (normally `~/.cache/roadproof/denmark-wfs`) on macOS/Linux.

### Germany

The Germany adapter samples only Google maneuvers whose straight chord
reconciles with Google's distance and requires every official sample to agree.
It reads the nationwide GeoBasis-DE/BKG `basemap.de Web Vektor` tiles:

- `Verkehrslinie.klasse=Bundesautobahn` or `fahrbahn=Getrennt` becomes
  `Highway`;
- other public road lines in/near `Siedlungsflaeche` become `City`; and
- other public road lines outside that layer become `Country`.

The settlement-to-EU mapping and sampled-chord allocation are explicitly
reported as inferred. Mixed or missing samples remain `Unresolved`.

### Sweden

The Sweden adapter uses the public Trafikverket NVDB map service layers
`Vagtrafiknat`, `Motorvag`, `Motortrafikled` and `TattbebyggtOmrade`.
Motorway/motor-traffic-road samples become `Highway`; motor-road samples
inside/outside the official built-up layer become `City`/`Country`. Every
sample on an accepted maneuver must agree.

### Belgium

Belgium is dispatched across official regional sources:

- Flanders: Wegenregister road morphology plus derived built-up-area road
  zones supports all three buckets;
- Brussels: UrbIS ADM street sections support `City`; and
- Wallonia: PICC `Autoroute` supports `Highway`. Other Walloon road classes
  remain `Unresolved` because the adapter has not found a current public
  built-up-road regime suitable for a defensible `City`/`Country` split.

This regional limitation is shown in every affected report rather than hidden
behind a country-wide estimate.

## Multi-purpose profiles

RoadProof keeps classification evidence independent from downstream rules.
The interface currently provides:

- **Composition only** — reports the measured road mix without pass/fail claims;
- **EU ISA 2021/1958** — applies the published 400 km, 25% per road type and
  15% darkness profile.

Additional public or organization-specific profiles can be added without
changing the underlying road evidence.

## Supported route links

RoadProof accepts both common clipboard forms:

```text
https://maps.app.goo.gl/...
https://www.google.com/maps/dir/Origin/Destination/...
```

Legacy `https://goo.gl/maps/...` links and supported localized European Google
Maps domains are also recognized. Clipboard labels, surrounding quotes, angle
brackets and harmless whitespace are removed before the URL is validated.

## Usage options

After installation, you can pass a route link directly on Windows:

```powershell
.\start.ps1 "https://maps.app.goo.gl/rbGN7YeiTqY5E74n9"
```

`start.bat` opens the same PowerShell launcher and does not contain a second
implementation.

Pass either link format directly on macOS or Linux:

```bash
./start.sh "https://www.google.com/maps/dir/Origin/Destination/..."
```

The default report includes the EU ISA profile checks. For composition only:

```powershell
.\start.ps1 "https://maps.app.goo.gl/rbGN7YeiTqY5E74n9" -Profile composition
```

The equivalent terminal option is:

```bash
./start.sh "https://maps.app.goo.gl/rbGN7YeiTqY5E74n9" --profile composition
```

Import an exact track separately from the Google route evidence:

```powershell
.\start.ps1 "https://maps.app.goo.gl/..." -Track ".\selected-route.gpx" -Format all
```

The CLI accepts GPX, GeoJSON, and KML files up to 5 MB and uses the same
`roadproof-track-geometry-v1` canonical hash as the web interface. It compares
track length and endpoints with Google maneuver evidence, but reports only
`consistent_not_equivalent` or `conflict`; it never promotes similarity into an
exact-equivalence claim. Track geometry is not substituted for Google's hidden
selected polyline. When reconciliation succeeds, supported single-country and
cross-border adapters explicitly report `reconciled_imported_exact_track` as
their analysis basis and sample its edges; track-derived totals are uniformly
reconciled to Google's authoritative distance. Conflicting tracks are never
used for road classification.

When no exact track is supplied, RoadProof automatically asks the public
Project OSRM service to reconstruct road-following geometry between the Google
maneuver anchors. Each maneuver is accepted independently only when its OSRM
distance differs from Google's distance by no more than 12% or 200 metres,
whichever is larger. Rejected maneuvers retain the conservative chord path and
remain eligible to resolve only under the existing official-evidence gates.
This hybrid geometry improves sampling coverage, but is not claimed to be
Google's selected polyline and does not receive a Google geometry hash.

The request sends the ordered maneuver coordinates to
`router.project-osrm.org`. Use `--no-auto-geometry` (or
`-NoAutoGeometry` with `start.ps1`) to avoid that disclosure or to disable this
best-effort network step. `--offline` also disables automatic reconstruction.

Use `--strict-evidence` for automation that should return exit code 2 unless
the complete route distance is classified. Run the secure live-service check
after installation or when diagnosing connectivity:

```powershell
.\.venv\Scripts\python.exe -m roadproof --self-check --network-check
```

TLS verification is always enabled. Managed networks with a private CA can set
`ROADPROOF_CA_BUNDLE` to a readable PEM bundle; Windows installations otherwise
use the operating-system certificate stores.

Machine-readable results use the versioned `roadproof.result.v1` schema:

```powershell
.\start.ps1 "https://maps.app.goo.gl/..." -Format json -StrictEvidence
```

`console` preserves the traditional console and Markdown output, `markdown`
saves only the Markdown report, `json` writes a JSON artifact and emits clean
JSON to standard output, and `all` produces both artifacts plus the console
view.

Operational diagnostics are read-only:

```powershell
.\.venv\Scripts\python.exe -m roadproof --list-adapters
.\.venv\Scripts\python.exe -m roadproof --diagnose-network
.\.venv\Scripts\python.exe -m roadproof --inspect-cache --format json
```

`--offline` still resolves the supplied Google route, but prohibits requests
to official road services. Retained evidence and fresh local cache entries are
allowed; a cache miss remains explicitly unresolved. On Windows the launcher
exposes the same behavior as `-Offline`.

Route identity uses the versioned `maneuver-v2` fingerprint, including six
decimal places for maneuver anchors and an algorithm marker. RoadProof also
computes the previous `maneuver-v1` value and checks it when loading retained
evidence, so existing evidence packages continue to work. The maneuver
fingerprint remains distinct from an exact geometry hash; Google maneuver-only
results report that geometry identity as unavailable.

JSON and Markdown results include performance telemetry for sampling, aggregate
network time across concurrent workers, request attempts and retries, downloaded
bytes, cache hits/misses, and cache-write failures. Repeated identical official
service failures short-circuit queued samples without changing the conservative
classification gates.

The EU ISA profile result uses the versioned
`roadproof.profile-result.v1` schema. Each check is reported as `meets`,
`fails`, `insufficient_evidence`, or `not_measured`. Unresolved road distance is
not treated as a measured zero: a category fails only when the known distance
and all unresolved distance together still cannot reach the target; otherwise
the result remains `insufficient_evidence`. Darkness remains `not_measured`.

The deterministic test suite never depends on public-service availability.
`.github/workflows/live-services.yml` runs separate non-volatile TLS, endpoint,
and representative German vector-tile decoding checks every Tuesday and on
manual dispatch. The live suite can also be run locally:

```powershell
$env:ROADPROOF_LIVE_TESTS = "1"
.\.venv\Scripts\python.exe -m unittest tests.test_live_services -v
```

Reports are saved under `reports` with a meaningful, collision-safe name:

```text
roadproof-<origin>-to-<destination>-<timestamp>-<fingerprint>.md
```

Every launcher run also saves a plain-text diagnostic transcript under
`reports/logs/roadproof-session-<timestamp>.log`. It includes the early Google
resolution and official-adapter messages as well as the final tables. On
Windows, `start.ps1` additionally expands the legacy PowerShell console buffer
to 9,999 lines when the host permits it. The log remains available when Windows
Terminal or another host manages scrollback itself.

The Google resolver retries short-lived connection resets, timeouts, rate
limits, and server failures with bounded backoff. Permanent URL and redirect
validation failures are never retried.

Round trips use `<origin>-loop`; when Google does not expose endpoint labels,
the route name is used instead.
Set the standard `NO_COLOR` environment variable when plain terminal output is
preferred.

## Web interface

The responsive local web interface uses the same Python evidence engine as the
terminal. Complete the normal installer first so `.venv` exists, then run:

```bash
npm ci
npm run dev
```

`npm run dev` starts both the private localhost analysis API and the web
interface. Open the local URL printed by the development server and paste a
Google Maps route link; no GPX, API key or Google account is required. The
local API binds only to `127.0.0.1` and accepts requests only from the local web
origin. Use **Load stored Denmark pilot** only to inspect the legacy sample.

## Validate

```bash
python -m unittest tests.test_cli -v
npm run lint
npm run test:unit
npm run build
npm run validate:artifact
```

## Official sources

- [Commission Delegated Regulation (EU) 2021/1958](https://eur-lex.europa.eu/eli/reg_del/2021/1958/2023-09-21/eng), especially point 4.3.1.3.
- [Vejdirektoratet guidance for using Vejman data in external programs](https://vejman.scrollhelp.site/hjaelpecenter/anvende-stedfstelse-i-egne-programmer).
- [Vejman public WFS capabilities](https://geocloud.vd.dk/vejman-stamdata/wfs?service=WFS&request=GetCapabilities).
- [Plandata public WFS capabilities](https://geoserver.plandata.dk/geoserver/wfs?service=WFS&request=GetCapabilities).
- [GeoBasis-DE/BKG basemap.de Web Vektor](https://basemap.de/produkte-und-dienste/web-vektor/).
- [Trafikverket road-data description](https://bransch.trafikverket.se/tjanster/data-kartor-och-geodatatjanster/las-om-vara-data/vagdata/).
- [Flemish Wegenregister public WFS](https://geo.api.vlaanderen.be/Wegenregister/wfs?service=WFS&request=GetCapabilities).
- [Walloon regional-road catalog](https://geoportail.wallonie.be/catalogue/d26f16df-5326-4cd7-b768-709e75a25507.html).
- [Brussels UrbIS data description](https://be.brussels/en/about-region/urbis-data).

## Privacy and credentials

The terminal tool requests no Google login, Google API key, private token, or
device location. It sends the supplied public link to Google Maps and sends
only route corridors or conservative sample coordinates to the selected
country's public official services. The generated Markdown report and a
seven-day cache of public official responses are stored locally. Future
country adapters must use public official datasets or document any optional
credential separately.
