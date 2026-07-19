export type EvidenceLevel = "measured" | "estimated" | "stale" | "simulated";

export interface GridReading {
  zone: string;
  intensity_gco2_kwh: number | null;
  observed_at: string | null;
  fetched_at: string | null;
  source: string;
  evidence: EvidenceLevel;
  metadata?: Record<string, unknown>;
}

export interface RouteEndpointSummary {
  id: string;
  name: string;
  provider: string;
  physicalModel: string;
  region: string;
  gridZone: string;
  qualityTier: string;
  energyEvidence: EvidenceLevel;
  processingLocationEvidence: string;
  gridAttribution: string;
  selfHosted: boolean;
  healthState: string;
  coefficientVersion: string;
}

export interface ImpactSeriesPoint {
  time: string;
  baselineEnergyKwh: number;
  actualEnergyKwh: number;
  baselineCarbonG: number | null;
  actualCarbonG: number | null;
  baselineCostUsd: number;
  actualCostUsd: number;
  carbonAccountingAvailable: boolean;
  carbonComparisonKind: "attributed" | "demo_counterfactual" | "unavailable";
}

export interface DemoRegionReading {
  region: string;
  zone: string;
  intensityGco2Kwh: number;
  observedAt: string;
  source: string;
  evidence: EvidenceLevel;
}

export interface DemoRegionRecommendation {
  mode: "demo_counterfactual";
  providerRoutingControlled: false;
  usesLiveGridData: boolean;
  reference: DemoRegionReading;
  target: DemoRegionReading;
  candidates: DemoRegionReading[];
  disclaimer: string;
}

export interface DemoCounterfactualImpact {
  mode: "demo_counterfactual";
  baselineCarbonG: number;
  targetCarbonG: number;
  rawCarbonDeltaG: number;
  avoidedCarbonG: number;
  reference: DemoRegionReading;
  target: DemoRegionReading;
  calculation: string;
  providerRoutingControlled: false;
  disclaimer: string;
  energy: {
    baseline: {
      energyKwh: number;
      source: string;
      simulatedFallback: boolean;
    };
    selected: {
      energyKwh: number;
      source: string;
      simulatedFallback: boolean;
    };
    routerEnergyKwh: number;
  };
}

export interface LatestRouteDecision {
  id: string;
  time: string;
  status: string;
  promptPreview: string | null;
  requestedModel: string;
  providerModel: string | null;
  providerCalled: boolean;
  executionMode: "live" | "cache" | "simulated";
  cache: string;
  fallback: boolean;
  durationMs: number | null;
  inputTokens: number;
  outputTokens: number | null;
  classification: Record<string, unknown> | null;
  gridState: string;
  routingGridState: string;
  selectedGridState: string;
  selectionReason: string;
  demoRegionRecommendation: DemoRegionRecommendation | null;
  selectedEndpoint: RouteEndpointSummary | null;
  baselineEndpoint: RouteEndpointSummary | null;
  candidates: Array<Record<string, unknown>>;
  impact: {
    baselineEnergyKwh: number;
    actualEnergyKwh: number;
    baselineCarbonG: number | null;
    actualCarbonG: number | null;
    rawCarbonDeltaG: number | null;
    baselineCostUsd: number;
    actualCostUsd: number;
    carbonAccountingAvailable: boolean;
    demoCounterfactual: DemoCounterfactualImpact | null;
    evidence: Record<string, unknown>;
  } | null;
}

export interface OverviewResponse {
  workspaceId: string;
  generatedAt: string;
  requests: number;
  successRate: number;
  cacheHitRate: number;
  actualCarbonGrams: number | null;
  avoidedCarbonGrams: number | null;
  carbonAccountedRequests: number;
  counterfactualCarbonGrams: number | null;
  counterfactualAvoidedCarbonGrams: number | null;
  actualCostUsd: number;
  costDeltaUsd: number;
  grid: GridReading;
  routeDistribution: Array<{ route: string; count: number }>;
  impactSeries: ImpactSeriesPoint[];
  latestDecision: LatestRouteDecision | null;
  connectedNodes: number;
  evidence: EvidenceLevel;
  recentRequests: Array<{
    id: string;
    time: string;
    model: string;
    status: string;
    cache: string;
    fallback: boolean;
    durationMs: number | null;
    route: string;
    provider: string | null;
    physicalModel: string | null;
    region: string | null;
    qualityTier: string | null;
  }>;
}

export interface ListResponse<T> {
  items: T[];
  nextCursor?: string | null;
}
