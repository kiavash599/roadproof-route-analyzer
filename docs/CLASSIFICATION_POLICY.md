# RoadProof classification policy v1.1

## Authoritative test buckets

Commission Delegated Regulation (EU) 2021/1958, point 4.3.1.3, requires the
test drive to include these three road types, each representing at least 25%
of the total route distance:

1. urban roads and streets;
2. non-urban roads; and
3. motorways, expressways and dual carriageways.

RoadProof presents these as `City`, `Country` and `Highway`, respectively.
That presentation mapping is explicit in every report.

Primary source:
<https://eur-lex.europa.eu/eli/reg_del/2021/1958/2023-09-21/eng>

## Segment decision rules

1. Preserve the exact selected Google Maps route geometry. Re-routing from the
   same stops is not equivalent and is not accepted as the same route.
2. Split cross-border routes by country before road classification.
3. For each country, translate official national road-network or traffic-law
   attributes into the three EU buckets through a versioned country adapter.
4. `Highway` includes only segments proven to be motorway, expressway or dual
   carriageway under the applicable country adapter.
5. `City` includes only segments proven to be an urban road or street under the
   applicable country adapter.
6. `Country` includes only segments proven to be a non-urban road under the
   applicable country adapter.
7. Speed limit alone never determines road type.
8. OSM or another open map may support an `inferred` result, but cannot by
   itself create a `confirmed` national classification.
9. Missing, conflicting, stale or weak evidence produces `Unresolved`.
   Unresolved distance is never redistributed.

## Evidence states

- **Confirmed** — matched segment plus named official source, source URL,
  source field and value, retrieval time and adapter version.
- **Inferred** — non-official evidence supports a category, but the official
  evidence contract is incomplete.
- **Unresolved** — no defensible assignment is possible.

## Country adapters

The EU regulation defines the required buckets but does not define one
Europe-wide GIS schema. Each adapter must therefore document:

- official national source and licensing;
- dataset version or retrieval time;
- fields and accepted values for motorway, expressway and dual carriageway;
- fields and accepted values for urban and non-urban status;
- conflict precedence;
- geometry and map-matching tolerances;
- known gaps and their `Unresolved` behavior.

Denmark is the current runtime adapter. Support for another country is not
claimed until its adapter passes these requirements.

## Denmark adapter v1

The adapter retrieves current public features at runtime and records the
response timestamp and adapter version in each report. It uses:

- Vejdirektoratet Vejman WFS layer
  `vejman-stamdata:hastighedsgraenser` for official road geometry and road,
  sign and built-up-area attributes; and
- Plandata WFS layer `pdk:theme_pdk_zonekort_samlet_v` as a zone fallback when
  a Vejman road feature lacks a decisive attribute.

Classification precedence is:

1. Vejman `VEJTYPESKILTET` / `KODE_VEJTYPESKILTET` values for `Motorvej` or
   `Motortrafikvej` become `Highway`.
2. Vejman `HAST_GENEREL_HAST` / `KODE_HAST_GENEREL_HAST` values documenting
   inside built-up-area signs become `City`; values documenting outside those
   signs become `Country`. A numeric speed value by itself is never used.
3. Vejman `VEJSTIKLASSE` values explicitly ending in `By` or `Land` become
   `City` or `Country` when the preceding fields are absent.
4. An otherwise unclassified road edge inside an official Plandata `Byzone`
   becomes `City`; `Landzone` or `Sommerhusområde` becomes `Country`.
5. Missing or conflicting evidence remains `Unresolved`.

Step 4 is an operational RoadProof inference: planning-zone polygons are
official, but the EU regulation does not declare them to be road-class labels.
Reports therefore describe the official match and the EU-bucket mapping
separately instead of calling the final distance directly confirmed.

Each Google maneuver is matched to the official graph independently. Endpoint
snaps must be no farther than 120 m, and accepted official path length must be
within `max(100 m, 6%)` of the Google maneuver distance. Google distance is
then allocated in proportion to the accepted official edge classes. A failed
path is never redistributed.

Known gaps include disconnected or generalized official geometry, ambiguous
parallel roads, roads without decisive official attributes, and the absence of
a direct dual-carriageway proof in the selected layer. These remain
`Unresolved`. If the optional Plandata fallback is unavailable, decisive
Vejman matches can still be reported and all other affected edges remain
`Unresolved`.

Official endpoints:

- <https://geocloud.vd.dk/vejman-stamdata/wfs?service=WFS&request=GetCapabilities>
- <https://vejman.scrollhelp.site/hjaelpecenter/anvende-stedfstelse-i-egne-programmer>
- <https://geoserver.plandata.dk/geoserver/wfs?service=WFS&request=GetCapabilities>
