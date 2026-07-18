"use client";

import type { OverviewResponse } from "@ecoroute/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  Leaf,
  RefreshCw,
  Server,
  Zap,
} from "lucide-react";
import Link from "next/link";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../lib/api";

const fmt = new Intl.NumberFormat("en-CA", { maximumFractionDigits: 3 });

function formatUtcTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? "—"
    : date.toISOString().slice(11, 19) + " UTC";
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
  const gridIntensity = data.grid.intensity_gco2_kwh;
  const gridIntensityLabel =
    gridIntensity == null ? "—" : fmt.format(gridIntensity);
  const demoGrid = data.grid.source.startsWith("ecoroute-fixture:");
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
            <span className="toolbar-label">Live grid provider</span>
            <strong>{data.grid.source}</strong>
          </div>
        )}
        <div className="grid-reading">
          <Zap size={16} />
          <strong>{gridIntensityLabel}</strong> gCO₂e/kWh <span>{data.grid.zone}</span>
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

      <div className="metric-grid">
        <Metric
          label="Requests"
          value={fmt.format(data.requests)}
          detail={`${fmt.format(data.successRate * 100)}% successful`}
          trend="up"
        />
        <Metric
          label="Operational carbon"
          value={
            data.actualCarbonGrams == null
              ? "Unavailable"
              : `${fmt.format(data.actualCarbonGrams)} g`
          }
          detail={
            data.avoidedCarbonGrams == null
              ? "No verified grid/location attribution"
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
          detail={`${data.grid.zone} · ${data.grid.evidence}`}
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
                        : item.model}
                    </strong>
                    <small>
                      {item.id.slice(0, 13)}… · {formatUtcTime(item.time)}
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
          <strong>Evidence boundary:</strong> Fixture energy and grid data are
          simulated. Hosted request energy remains estimated until a supported
          self-hosted agent reports measurements.
        </span>
      </aside>
    </div>
  );
}
