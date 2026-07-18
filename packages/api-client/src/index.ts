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

export interface OverviewResponse {
  workspaceId: string;
  generatedAt: string;
  requests: number;
  successRate: number;
  cacheHitRate: number;
  actualCarbonGrams: number | null;
  avoidedCarbonGrams: number | null;
  carbonAccountedRequests: number;
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
