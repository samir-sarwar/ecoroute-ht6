export type EvidenceLevel = "measured" | "estimated" | "stale" | "simulated";

export interface GridReading {
  zone: string;
  intensity_gco2_kwh: number;
  observed_at: string;
  fetched_at: string;
  source: string;
  evidence: EvidenceLevel;
}

export interface OverviewResponse {
  workspaceId: string;
  generatedAt: string;
  requests: number;
  successRate: number;
  cacheHitRate: number;
  actualCarbonGrams: number;
  avoidedCarbonGrams: number;
  actualCostUsd: number;
  costDeltaUsd: number;
  grid: GridReading;
  routeDistribution: Array<{ route: string; count: number }>;
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
  }>;
}

export interface ListResponse<T> {
  items: T[];
  nextCursor?: string | null;
}

