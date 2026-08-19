# RoadProof

Multi-purpose, evidence-first road-type analysis for European routes.

RoadProof accepts a Google Maps route link and is designed to preserve the
exact selected route, match its segments against country-specific official
road data, and report distance by defensible road categories. The same evidence
layer can support compliance testing, route design, fleet QA and research.

## Product status

This repository is an honest first product increment:

- polished, responsive route-intake and report UI;
- a verified Denmark example based on a reproducible route-analysis record;
- canonical Europe-wide road bucket and evidence types;
- explicit `Confirmed`, `Inferred`, and `Unresolved` handling;
- cross-border/country-adapter architecture;
- no false claim of pan-European official-data coverage.

The public prototype validates arbitrary Google Maps links, but only renders
kilometre results when a stored evidence package exists. Live exact-geometry
extraction and additional national adapters are the next backend milestones.

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

## Run locally

Requirements: Node.js 22.13 or newer.

```bash
npm ci
npm run dev
```

Open the local URL printed by the development server. Use **Load verified
Denmark example** to inspect the stored pilot report.

## Validate

```bash
npm run lint
npm run build
npm run validate:artifact
```

## Official source

- [Commission Delegated Regulation (EU) 2021/1958](https://eur-lex.europa.eu/eli/reg_del/2021/1958/2023-09-21/eng), especially point 4.3.1.3.

## Privacy and credentials

The current frontend requests no Google login, Google API key, private token,
or user location. Future country adapters must use public official datasets or
document any optional credential separately.
