"use client";

import type {
  LatestRouteDecision,
  OverviewResponse,
  RouteEndpointSummary,
} from "@ecoroute/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  Cloud,
  Cpu,
  GitBranch,
  Leaf,
  RefreshCw,
  Server,
  ShieldCheck,
  Zap,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../lib/api";

const fmt = new Intl.NumberFormat("en-CA", { maximumFractionDigits: 3 });
const precise = new Intl.NumberFormat("en-CA", { maximumSignificantDigits: 4 });

type ImpactMetric = "carbon" | "energy" | "cost";

type CandidateView = {
  endpoint_id?: string;
  endpointId?: string;
  name?: string;
  score?: number | null;
  excluded_reason?: string | null;
  excludedReason?: string | null;
  estimated_carbon_g?: number | null;
  estimatedCarbonG?: number | null;
  estimated_cost_usd?: number | string;
  estimatedCostUsd?: number | string;
  endpoint?: RouteEndpointSummary | null;
};

const impactMetricConfig = {
  carbon: {
    label: "Carbon",
    baselineKey: "baselineCarbonG",
    actualKey: "actualCarbonG",
    unit: "g CO₂e",
  },
  energy: {
    label: "Energy",
    baselineKey: "baselineEnergyKwh",
    actualKey: "actualEnergyKwh",
    unit: "kWh",
  },
  cost: {
    label: "Cost",
    baselineKey: "baselineCostUsd",
    actualKey: "actualCostUsd",
    unit: "USD",
  },
} as const;

function formatUtcTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? "—"
    : date.toISOString().slice(11, 19) + " UTC";
}

function formatImpact(value: number | null, unit: string) {
  if (value == null) return "Unavailable";
  if (unit === "USD") return `$${precise.format(value)}`;
  return `${precise.format(value)} ${unit}`;
}

function reductionLabel(baseline: number | null, actual: number | null) {
  if (baseline == null || actual == null || baseline === 0) return "No comparison";
  const percent = ((baseline - actual) / baseline) * 100;
  return `${percent >= 0 ? "↓" : "↑"} ${Math.abs(percent).toFixed(1)}% vs baseline`;
}

function ImpactReadout({
  label,
  baseline,
  actual,
  unit,
}: {
  label: string;
  baseline: number | null;
  actual: number | null;
  unit: string;
}) {
  return (
    <div className="impact-readout">
      <span>{label}</span>
      <strong>{formatImpact(actual, unit)}</strong>
      <small>{reductionLabel(baseline, actual)}</small>
      <div>
        <span>Baseline</span>
        <code>{formatImpact(baseline, unit)}</code>
      </div>
    </div>
  );
}

function LiveRoutePanel({ decision }: { decision: LatestRouteDecision | null }) {
  if (!decision) {
    return (
      <section className="panel live-route-panel">
        <div className="panel-heading">
          <div>
            <h2>Live routing trace</h2>
            <p>Logical alias → classifier → physical provider model</p>
          </div>
          <Activity size={18} />
        </div>
        <div className="empty-state compact">
          <GitBranch />
          <h3>Waiting for a completed request</h3>
          <p>Send a prompt from Northstar Support to reveal the full route.</p>
        </div>
      </section>
    );
  }

  const classification = decision.classification ?? {};
  const complexity = String(classification.complexity ?? "unknown");
  const task = String(classification.task_type ?? "unknown");
  const risk = String(classification.risk ?? "unknown");
  const selected = decision.selectedEndpoint;
  const executionLabel =
    decision.executionMode === "live"
      ? "LIVE PROVIDER"
      : decision.executionMode === "cache"
        ? "CACHE REUSE"
        : "SIMULATED PROVIDER";
  const candidates = decision.candidates as CandidateView[];
  const demoRegion = decision.demoRegionRecommendation;

  return (
    <section className="panel live-route-panel">
      <div className="panel-heading">
        <div>
          <h2>Live routing trace</h2>
          <p>{decision.promptPreview || "Prompt preview is redacted"}</p>
        </div>
        <span className={`execution-pill ${decision.executionMode}`}>
          <span /> {executionLabel}
        </span>
      </div>

      <div
        className={`route-chain ${demoRegion ? "with-demo-target" : ""}`}
        aria-label="Model routing path"
      >
        <article>
          <span>Client parameter</span>
          <Cloud />
          <code>model: {decision.requestedModel}</code>
          <small>Stable public alias</small>
        </article>
        <ArrowRight className="route-arrow" aria-hidden="true" />
        <article>
          <span>Router decision</span>
          <GitBranch />
          <strong>{complexity} · {task}</strong>
          <small>{risk} risk · {decision.routingGridState} routing grid</small>
        </article>
        <ArrowRight className="route-arrow" aria-hidden="true" />
        <article className="selected-route">
          <span>{decision.providerCalled ? "Provider parameter" : "Provider call"}</span>
          <Cpu />
          <code>
            {decision.providerCalled
              ? `model: ${decision.providerModel ?? "unknown"}`
              : "No upstream call"}
          </code>
          <small>
            {decision.providerCalled
              ? `${selected?.name ?? "unknown"} · ${selected?.provider ?? "unknown"}`
              : `Reused ${selected?.physicalModel ?? "cached response"}`}
          </small>
        </article>
        {demoRegion ? (
          <>
            <ArrowRight className="route-arrow" aria-hidden="true" />
            <article className="demo-target-route">
              <span>Demo green target</span>
              <Leaf />
              <strong>{demoRegion.target.region}</strong>
              <small>
                {demoRegion.target.zone} · {fmt.format(demoRegion.target.intensityGco2Kwh)} gCO₂e/kWh
              </small>
              <small>Counterfactual target · actual Azure region remains global</small>
            </article>
          </>
        ) : null}
      </div>

      <div className="route-facts">
        <span><b>Reason</b>{decision.selectionReason.replaceAll("_", " ")}</span>
        <span><b>Routing grid</b>{decision.routingGridState} (baseline)</span>
        <span><b>Selected grid</b>{decision.selectedGridState}</span>
        <span><b>Region</b>{selected?.region ?? "unknown"}</span>
        {demoRegion ? (
          <span><b>Demo target</b>{demoRegion.target.region} ({demoRegion.target.zone})</span>
        ) : null}
        <span><b>Tier</b>{selected?.qualityTier ?? "cache"}</span>
        <span><b>Cache</b>{decision.cache}</span>
        <span><b>Tokens</b>{decision.inputTokens} in · {decision.outputTokens ?? "—"} out</span>
        <span><b>Latency</b>{decision.durationMs ?? "—"} ms</span>
      </div>

      <div className="candidate-strip">
        {candidates.map((candidate) => {
          const endpointId = candidate.endpoint_id ?? candidate.endpointId;
          const excluded = candidate.excluded_reason ?? candidate.excludedReason;
          const isSelected = endpointId === selected?.id;
          const isBaseline = endpointId === decision.baselineEndpoint?.id;
          const carbon = candidate.estimated_carbon_g ?? candidate.estimatedCarbonG;
          const cost = candidate.estimated_cost_usd ?? candidate.estimatedCostUsd;
          return (
            <article
              className={isSelected ? "selected" : excluded ? "excluded" : ""}
              key={endpointId ?? candidate.name}
            >
              <div>
                <strong>{candidate.name ?? candidate.endpoint?.name}</strong>
                {isSelected ? <ShieldCheck size={14} /> : null}
                {isBaseline ? <span className="baseline-tag">baseline</span> : null}
              </div>
              <code>{candidate.endpoint?.physicalModel ?? "model metadata unavailable"}</code>
              <small>
                {excluded
                  ? String(excluded).replaceAll("_", " ")
                  : `score ${Number(candidate.score ?? 0).toFixed(3)}`}
              </small>
              <small>
                {carbon == null ? "carbon unavailable" : `${precise.format(Number(carbon))} g`} · $
                {precise.format(Number(cost ?? 0))}
              </small>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function Metric({
  label,
  value,
  detail,
  trend,
}: {
  label: string;
  value: string;
  detail: string;
  trend?: "up" | "down";
}) {
  return (
    <section className="metric">
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
      <div className="metric-detail">
        {trend === "up" ? (
          <ArrowUpRight size={14} />
        ) : trend === "down" ? (
          <ArrowDownRight size={14} />
        ) : null}
        {detail}
      </div>
    </section>
  );
}

export default function Overview() {
  const queryClient = useQueryClient();
  const [impactMetric, setImpactMetric] = useState<ImpactMetric>("carbon");
  const query = useQuery({
    queryKey: ["overview"],
    queryFn: () => api<OverviewResponse>("/overview?window=1h"),
  });
  const grid = useMutation({
    mutationFn: (scenario: string) =>
      api("/demo/grid-scenario", {
        method: "POST",
        body: JSON.stringify({ scenario }),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["overview"] }),
  });
  const failure = useMutation({
    mutationFn: () =>
      api("/demo/quality-failure", {
        method: "POST",
        body: JSON.stringify({ enabled: true }),
      }),
  });
  const impactChartData = useMemo(() => {
    const points = query.data?.impactSeries ?? [];
    const config = impactMetricConfig[impactMetric];
    let baseline = 0;
    let actual = 0;
    return points.flatMap((point) => {
      const pointBaseline = point[config.baselineKey];
      const pointActual = point[config.actualKey];
      if (pointBaseline == null || pointActual == null) return [];
      baseline += Number(pointBaseline);
      actual += Number(pointActual);
      return [{ time: formatUtcTime(point.time), baseline, actual }];
    });
  }, [impactMetric, query.data?.impactSeries]);

  if (query.isLoading)
    return (
      <div className="page">
        <div className="skeleton title" />
        <div className="metric-grid">
          {Array.from({ length: 6 }).map((_, i) => (
            <div className="skeleton metric" key={i} />
          ))}
        </div>
      </div>
    );
  if (query.error || !query.data)
    return (
      <div className="page">
        <div className="error-banner">
          <AlertTriangle /> Could not load operations: {query.error?.message}
          <button onClick={() => query.refetch()}>Retry</button>
        </div>
      </div>
    );
  const data = query.data;
  const demoRegion = data.latestDecision?.demoRegionRecommendation;
  const gridIntensity = demoRegion?.target.intensityGco2Kwh ?? data.grid.intensity_gco2_kwh;
  const gridIntensityLabel =
    gridIntensity == null ? "—" : fmt.format(gridIntensity);
  const gridZone = demoRegion?.target.zone ?? data.grid.zone;
  const gridEvidence = demoRegion?.target.evidence ?? data.grid.evidence;
  const demoGrid = data.grid.source.startsWith("ecoroute-fixture:");
  const counterfactual = data.latestDecision?.impact?.demoCounterfactual;
  const counterfactualUsesDemoEnergy = Boolean(
    counterfactual?.energy.baseline.simulatedFallback ||
      counterfactual?.energy.selected.simulatedFallback,
  );
  return (
    <div className="page">
      <header className="page-header">
        <div>
          <div className="eyebrow">OPERATIONS / LAST HOUR</div>
          <h1>Overview</h1>
          <p>
            Efficiency, reliability, and evidence across every routed request.
          </p>
        </div>
        <div className="header-status">
          <span className={`evidence ${data.evidence}`}>{data.evidence}</span>
          <span>Updated {formatUtcTime(data.generatedAt)}</span>
          <button
            className="icon-button"
            onClick={() => query.refetch()}
            aria-label="Refresh overview"
          >
            <RefreshCw size={16} />
          </button>
        </div>
      </header>

      <section className="demo-toolbar" aria-label={demoGrid ? "Demo controls" : "Grid status"}>
        {demoGrid ? (
          <div>
            <span className="toolbar-label">Demo grid</span>
            {["clean", "moderate", "dirty"].map((scenario) => (
              <button
                key={scenario}
                className={
                  data.grid.source.endsWith(scenario)
                    ? "segment active"
                    : "segment"
                }
                onClick={() => grid.mutate(scenario)}
              >
                {scenario}
              </button>
            ))}
          </div>
        ) : (
          <div>
            <span className="toolbar-label">
              {demoRegion ? "Live demo region scan" : "Live grid provider"}
            </span>
            <strong>
              {demoRegion
                ? `${demoRegion.candidates.length} candidate grids · target ${demoRegion.target.region}`
                : data.grid.source}
            </strong>
          </div>
        )}
        <div className="grid-reading">
          <Zap size={16} />
          <strong>{gridIntensityLabel}</strong> gCO₂e/kWh <span>{gridZone}</span>
        </div>
        {demoGrid ? (
          <button
            className="secondary-button"
            onClick={() => failure.mutate()}
            disabled={failure.isPending}
          >
            {failure.isSuccess
              ? "Next SLM check will fail"
              : "Force next quality failure"}
          </button>
        ) : null}
      </section>

      <div className="demo-stage-grid">
        <LiveRoutePanel decision={data.latestDecision} />
        <section className="panel impact-telemetry-panel">
          <div className="panel-heading">
            <div>
              <h2>Baseline vs EcoRoute</h2>
              <p>Cumulative estimated impact · last hour</p>
            </div>
            <div className="impact-metric-switch" aria-label="Impact chart metric">
              {(Object.keys(impactMetricConfig) as ImpactMetric[]).map((metric) => (
                <button
                  className={impactMetric === metric ? "active" : ""}
                  key={metric}
                  onClick={() => setImpactMetric(metric)}
                >
                  {impactMetricConfig[metric].label}
                </button>
              ))}
            </div>
          </div>
          {data.latestDecision?.impact ? (
            <div className="impact-readout-grid">
              <ImpactReadout
                label="Estimated energy"
                baseline={data.latestDecision.impact.baselineEnergyKwh}
                actual={data.latestDecision.impact.actualEnergyKwh}
                unit="kWh"
              />
              <ImpactReadout
                label="Estimated cost"
                baseline={data.latestDecision.impact.baselineCostUsd}
                actual={data.latestDecision.impact.actualCostUsd}
                unit="USD"
              />
              <ImpactReadout
                label={counterfactual ? "Demo counterfactual carbon" : "Operational carbon"}
                baseline={
                  data.latestDecision.impact.baselineCarbonG ??
                  counterfactual?.baselineCarbonG ??
                  null
                }
                actual={
                  data.latestDecision.impact.actualCarbonG ??
                  counterfactual?.targetCarbonG ??
                  null
                }
                unit="g CO₂e"
              />
            </div>
          ) : null}
          {impactChartData.length ? (
            <div className="impact-chart">
              <ResponsiveContainer width="100%" height={230}>
                <LineChart data={impactChartData} margin={{ left: 4, right: 16, top: 12 }}>
                  <CartesianGrid stroke="#e6e9e7" vertical={false} />
                  <XAxis dataKey="time" tick={{ fontSize: 9 }} minTickGap={26} />
                  <YAxis tick={{ fontSize: 9 }} width={58} />
                  <Tooltip />
                  <Legend />
                  <Line
                    dataKey="baseline"
                    name={`${impactMetric === "carbon" && counterfactual ? "Reference-region assumption" : "Normal baseline"} (${impactMetricConfig[impactMetric].unit})`}
                    stroke="#9a875f"
                    strokeWidth={2}
                    dot={false}
                  />
                  <Line
                    dataKey="actual"
                    name={`${impactMetric === "carbon" && counterfactual ? "Demo green target" : "EcoRoute"} (${impactMetricConfig[impactMetric].unit})`}
                    stroke="#4f6b33"
                    strokeWidth={2.5}
                    dot={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="empty-state compact impact-empty">
              <Leaf />
              <h3>No {impactMetric} series yet</h3>
              <p>
                Carbon requires valid grid and processing-location evidence; energy and cost appear after any completed request.
              </p>
            </div>
          )}
          <div className="impact-evidence-line">
            <AlertTriangle size={14} />
            {counterfactual
              ? `Counterfactual demo: live grid intensities × ${counterfactualUsesDemoEnergy ? "temporary tier-based demo energy assumptions" : "configured energy coefficients"}. Azure Global Standard does not confirm or let EcoRoute force the displayed target region.`
              : "Values use recorded tokens and configured provider prices/energy coefficients. They are not invoice or facility-meter readings."}
          </div>
        </section>
      </div>

      <div className="metric-grid">
        <Metric
          label="Requests"
          value={fmt.format(data.requests)}
          detail={`${fmt.format(data.successRate * 100)}% successful`}
          trend="up"
        />
        <Metric
          label={
            data.actualCarbonGrams == null && data.counterfactualCarbonGrams != null
              ? "Demo modeled carbon"
              : "Operational carbon"
          }
          value={
            data.actualCarbonGrams == null
              ? data.counterfactualCarbonGrams == null
                ? "Unavailable"
                : `${fmt.format(data.counterfactualCarbonGrams)} g`
              : `${fmt.format(data.actualCarbonGrams)} g`
          }
          detail={
            data.avoidedCarbonGrams == null
              ? data.counterfactualAvoidedCarbonGrams == null
                ? "No verified grid/location attribution"
                : `${fmt.format(data.counterfactualAvoidedCarbonGrams)} g counterfactual gain`
              : `${fmt.format(data.avoidedCarbonGrams)} g avoided`
          }
          trend="down"
        />
        <Metric
          label="Cache hit rate"
          value={`${fmt.format(data.cacheHitRate * 100)}%`}
          detail="Exact + semantic"
        />
        <Metric
          label="Current cost"
          value={`$${fmt.format(data.actualCostUsd)}`}
          detail={`${data.costDeltaUsd <= 0 ? "↓" : "↑"} $${fmt.format(Math.abs(data.costDeltaUsd))} vs baseline`}
        />
        <Metric
          label="Grid intensity"
          value={gridIntensityLabel}
          detail={`${gridZone} · ${gridEvidence}${demoRegion ? " · demo target" : ""}`}
        />
        <Metric
          label="Connected nodes"
          value={String(data.connectedNodes)}
          detail="Simulator counts as simulated"
        />
      </div>

      <div className="overview-grid">
        <section className="panel chart-panel">
          <div className="panel-heading">
            <div>
              <h2>Route distribution</h2>
              <p>Completed requests by physical route</p>
            </div>
            <Leaf size={19} />
          </div>
          {data.routeDistribution.length ? (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={data.routeDistribution}>
                <CartesianGrid stroke="#e6e9e7" vertical={false} />
                <XAxis dataKey="route" tick={{ fontSize: 11 }} />
                <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
                <Tooltip />
                <Bar dataKey="count" fill="#167a54" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="empty-state">
              <Leaf />
              <h3>No routes yet</h3>
              <p>
                Send a message from Northstar Support to populate this chart.
              </p>
            </div>
          )}
        </section>
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>Live decision feed</h2>
              <p>Redacted request outcomes</p>
            </div>
            <Link href="/request-audit">View audit →</Link>
          </div>
          <div className="feed">
            {data.recentRequests.length ? (
              data.recentRequests.map((item) => (
                <Link
                  href={`/request-audit?id=${item.id}`}
                  className="feed-row"
                  key={item.id}
                >
                  <span
                    className={
                      item.status === "completed"
                        ? "status-icon ok"
                        : "status-icon"
                    }
                  >
                    <Server size={14} />
                  </span>
                  <span className="feed-main">
                    <strong>
                      {item.cache !== "miss"
                        ? `${item.cache} cache`
                        : item.route}
                    </strong>
                    <small>
                      {item.physicalModel ?? item.model} · {item.region ?? "no region"} · {formatUtcTime(item.time)}
                    </small>
                  </span>
                  <span className="feed-meta">
                    {item.durationMs ?? "—"} ms
                    {item.fallback ? <em>fallback</em> : null}
                  </span>
                </Link>
              ))
            ) : (
              <div className="empty-state compact">
                <Server />
                <h3>Waiting for traffic</h3>
                <p>Live decisions will appear here.</p>
              </div>
            )}
          </div>
        </section>
      </div>
      <aside className="method-note">
        <AlertTriangle size={16} />
        <span>
          <strong>Evidence boundary:</strong>{" "}
          {demoGrid
            ? "Fixture provider, energy, and grid inputs are simulated."
            : "Provider calls and grid readings can be live, while hosted energy and cost savings remain calculated estimates."}{" "}
          Open a request in Audit to inspect its exact evidence sources.
        </span>
      </aside>
    </div>
  );
}
