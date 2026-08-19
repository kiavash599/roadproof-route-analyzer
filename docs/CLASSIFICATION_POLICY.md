# RoadProof classification policy v1.0

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

Denmark is the current pilot adapter. Support for another country is not
claimed until its adapter passes these requirements.
