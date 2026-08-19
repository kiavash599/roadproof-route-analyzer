/**
 * RoadProof canonical model.
 *
 * A country adapter is responsible for translating official national road
 * attributes into one of these EU test buckets. This core deliberately does
 * not translate speed limits or OSM highway tags into confirmed categories.
 */
export type EuRoadBucket =
  | "motorway_expressway_dual_carriageway"
  | "urban_road_or_street"
  | "non_urban_road"
  | "unresolved";

export type RoadCategory = "Highway" | "City" | "Country" | "Unresolved";
export type EvidenceState = "confirmed" | "inferred" | "unresolved";

export interface SegmentEvidence {
  bucket: EuRoadBucket;
  state: EvidenceState;
  countryCode: string;
  sourceName?: string;
  sourceUrl?: string;
  sourceField?: string;
  sourceValue?: string;
  note?: string;
}

export interface ClassifiedSegment extends SegmentEvidence {
  roadCategory: RoadCategory;
}

const ROAD_CATEGORY_BY_BUCKET: Record<EuRoadBucket, RoadCategory> = {
  motorway_expressway_dual_carriageway: "Highway",
  urban_road_or_street: "City",
  non_urban_road: "Country",
  unresolved: "Unresolved",
};

export function classifySegment(evidence: SegmentEvidence): ClassifiedSegment {
  if (evidence.state === "unresolved" || evidence.bucket === "unresolved") {
    return { ...evidence, bucket: "unresolved", state: "unresolved", roadCategory: "Unresolved" };
  }

  return { ...evidence, roadCategory: ROAD_CATEGORY_BY_BUCKET[evidence.bucket] };
}

export function canBeConfirmed(evidence: SegmentEvidence): boolean {
  return Boolean(
    evidence.state === "confirmed" &&
    evidence.sourceName &&
    evidence.sourceUrl &&
    evidence.sourceField &&
    evidence.sourceValue,
  );
}
