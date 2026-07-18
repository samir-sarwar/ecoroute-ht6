"use client";

import type { EvidenceLevel, ListResponse } from "@ecoroute/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Bot,
  Boxes,
  CheckCircle2,
  Database,
  Download,
  FileChartColumn,
  Network,
  Play,
  Plus,
  RefreshCw,
  Route,
  Search,
  Settings,
  ShieldAlert,
  Trash2,
  X,
} from "lucide-react";
import {
  FormEvent,
  ReactNode,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { api, ApiError, idempotencyKey } from "../lib/api";

type Item = Record<string, any>;
type List = ListResponse<Item>;

const evidence = (value?: string) => {
  const level: EvidenceLevel = [
    "measured",
    "estimated",
    "stale",
    "simulated",
  ].includes(value ?? "")
    ? (value as EvidenceLevel)
    : "estimated";
  return <span className={`evidence ${level}`}>{level}</span>;
};

function formatUtc(value?: string | number) {
  if (value === undefined || value === null || value === "") return "—";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "—";
  return date
    .toISOString()
    .replace("T", " ")
    .replace(/\.\d{3}Z$/, " UTC");
}

function ErrorBanner({
  error,
  retry,
}: {
  error: Error | null;
  retry?: () => void;
}) {
  if (!error) return null;
  const requestId = error instanceof ApiError ? error.requestId : null;
  return (
    <div className="error-banner" role="alert">
      <AlertTriangle />
      <span>
        {error.message}
        {requestId ? (
          <small>
            Request ID: <code>{requestId}</code>
          </small>
        ) : null}
      </span>
      {retry ? <button onClick={retry}>Retry</button> : null}
    </div>
  );
}

function LoadingTable() {
  return (
    <div className="table-skeleton" aria-label="Loading">
      <div />
      <div />
      <div />
      <div />
    </div>
  );
}

function Empty({
  icon,
  title,
  detail,
  action,
}: {
  icon: ReactNode;
  title: string;
  detail: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      {icon}
      <h3>{title}</h3>
      <p>{detail}</p>
      {action}
    </div>
  );
}

function Header({
  eyebrow,
  title,
  description,
  icon,
}: {
  eyebrow: string;
  title: string;
  description: string;
  icon: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      <div className="page-icon">{icon}</div>
    </header>
  );
}

function ConfirmAction({
  label,
  title,
  detail,
  danger = false,
  disabled = false,
  onConfirm,
}: {
  label: string;
  title: string;
  detail: string;
  danger?: boolean;
  disabled?: boolean;
  onConfirm: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  return (
    <>
      <button
        className={danger ? "danger-button" : "secondary-button"}
        disabled={disabled}
        onClick={() => dialog.current?.showModal()}
      >
        {label}
      </button>
      <dialog
        ref={dialog}
        className="confirm-dialog"
        onCancel={() => dialog.current?.close()}
      >
        <button
          className="dialog-close"
          aria-label="Close confirmation"
          onClick={() => dialog.current?.close()}
        >
          <X />
        </button>
        <ShieldAlert />
        <h2>{title}</h2>
        <p>{detail}</p>
        <div className="dialog-actions">
          <button
            className="secondary-button"
            onClick={() => dialog.current?.close()}
          >
            Cancel
          </button>
          <button
            className={danger ? "danger-button" : "primary-button"}
            onClick={() => {
              dialog.current?.close();
              onConfirm();
            }}
          >
            Confirm
          </button>
        </div>
      </dialog>
    </>
  );
}

function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint ? <small>{hint}</small> : null}
    </label>
  );
}

const presetWeights: Record<string, Item> = {
  eco: { carbon: 0.45, cost: 0.2, latency: 0.1, quality: 0.2, evidence: 0.05 },
  balanced: { carbon: 0.3, cost: 0.2, latency: 0.2, quality: 0.25, evidence: 0.05 },
  strict_quality: { carbon: 0.1, cost: 0.1, latency: 0.1, quality: 0.65, evidence: 0.05 },
  cost_saver: { carbon: 0.15, cost: 0.55, latency: 0.1, quality: 0.15, evidence: 0.05 },
};

function newPolicyConfig(name: string, endpointIds: string[], preset = "balanced") {
  return {
    name,
    preset,
    enabledEndpointIds: endpointIds,
    minRouterConfidence: 0.7,
    minSlmConfidence: 0.8,
    maxLatencyMs: 30_000,
    maxCostIncreasePct: preset === "balanced" ? 10 : 0,
    cleanThresholdGco2Kwh: 150,
    dirtyThresholdGco2Kwh: 400,
    semanticCacheEnabled: true,
    semanticCacheTaskTypes: ["policy_qa"],
    semanticSimilarityThreshold: 0.94,
    cacheTtlSeconds: 86_400,
    qualityFallbackEnabled: true,
    allowExperimentalModels: false,
    allowStaleCarbonMinutes: 60,
    allowedRegions: [],
    sensitiveRequiresSelfHosted: false,
    weights: presetWeights[preset] ?? presetWeights.balanced,
    taskRules: [],
    namespaceVersion: 1,
  };
}

function PoliciesView() {
  const client = useQueryClient();
  const policies = useQuery({
    queryKey: ["policies"],
    queryFn: () => api<List>("/policies"),
  });
  const models = useQuery({
    queryKey: ["logical-models"],
    queryFn: () => api<List>("/logical-models"),
  });
  const endpoints = useQuery({
    queryKey: ["model-endpoints"],
    queryFn: () => api<List>("/model-endpoints"),
  });
  const [selected, setSelected] = useState<string>();
  const [showCreate, setShowCreate] = useState(false);
  const [prompt, setPrompt] = useState(
    "What is the return window for an unused item?",
  );
  const current =
    policies.data?.items.find((item) => item.id === selected) ??
    policies.data?.items[0];
  const [draft, setDraft] = useState<Item | null>(null);
  const [taskRulesText, setTaskRulesText] = useState("[]");
  const [taskRulesError, setTaskRulesError] = useState("");
  const config = draft ?? current?.config;
  useEffect(() => {
    setTaskRulesText(JSON.stringify(current?.config?.taskRules ?? [], null, 2));
    setTaskRulesError("");
  }, [current?.id]);
  const simulation = useMutation({
    mutationFn: () => {
      if (!current) throw new Error("Select a policy first.");
      return api<Item>(`/policies/${current.id}/simulate`, {
        method: "POST",
        body: JSON.stringify({ prompt }),
      });
    },
  });
  const clone = useMutation({
    mutationFn: () => {
      if (!current) throw new Error("Select a policy first.");
      return api<Item>(`/policies/${current.id}/clone`, {
        method: "POST",
        body: JSON.stringify({ name: current.name, config }),
      });
    },
    onSuccess: (value: Item) => {
      setDraft(null);
      setSelected(value.id);
      void client.invalidateQueries({ queryKey: ["policies"] });
    },
  });
  const create = useMutation({
    mutationFn: (body: Item) =>
      api<Item>("/policies", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: (value) => {
      setSelected(value.id);
      setDraft(null);
      setShowCreate(false);
      void client.invalidateQueries({ queryKey: ["policies"] });
    },
  });
  const activate = useMutation({
    mutationFn: (modelId: string) => {
      if (!current) throw new Error("Select a policy first.");
      return api(`/logical-models/${modelId}/activate-policy`, {
        method: "POST",
        body: JSON.stringify({ policyId: current.id }),
      });
    },
    onSuccess: () =>
      void client.invalidateQueries({ queryKey: ["logical-models"] }),
  });
  const weights = config?.weights ?? {};
  const weightSum = Object.values(weights).reduce(
    (sum: number, value) => sum + Number(value),
    0,
  );
  const patch = (name: string, value: unknown) =>
    setDraft({ ...(config ?? {}), [name]: value });
  const patchWeight = (name: string, value: number) =>
    patch("weights", { ...weights, [name]: value });
  function submitNewPolicy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const name = String(data.get("name")).trim();
    const preset = String(data.get("preset"));
    create.mutate({
      name,
      config: newPolicyConfig(
        name,
        data.getAll("enabledEndpointIds") as string[],
        preset,
      ),
    });
  }
  return (
    <div className="page">
      <Header
        eyebrow="ROUTING / IMMUTABLE VERSIONS"
        title="Routing Policies"
        description="Edit a new version, simulate it, then activate it atomically."
        icon={<Route />}
      />
      <ErrorBanner
        error={
          (policies.error ??
            endpoints.error ??
            models.error ??
            create.error ??
            clone.error ??
            simulation.error ??
            activate.error) as Error | null
        }
        retry={() => policies.refetch()}
      />
      <div className="toolbar">
        <button
          className="primary-button"
          onClick={() => setShowCreate((value) => !value)}
        >
          <Plus /> New policy family
        </button>
        <span>
          Saving an edit creates a new immutable version in the selected family.
        </span>
      </div>
      {showCreate ? (
        <form className="panel create-form" onSubmit={submitNewPolicy}>
          <div className="panel-heading">
            <div>
              <h2>Create policy family</h2>
              <p>Start at version 1 with an explicit endpoint allowlist.</p>
            </div>
          </div>
          <div className="form-grid">
            <Field label="Policy name">
              <input name="name" required defaultValue="Balanced operations" />
            </Field>
            <Field label="Preset">
              <select name="preset" defaultValue="balanced">
                {[
                  "eco",
                  "balanced",
                  "strict_quality",
                  "cost_saver",
                  "custom",
                ].map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </Field>
          </div>
          <fieldset className="check-field">
            <legend>Endpoint allowlist</legend>
            <div className="check-grid">
              {endpoints.data?.items.map((item) => (
                <label key={item.id}>
                  <input
                    type="checkbox"
                    name="enabledEndpointIds"
                    value={item.id}
                    defaultChecked={item.enabled}
                  />
                  {item.name}
                </label>
              ))}
            </div>
          </fieldset>
          <div className="editor-actions">
            <button className="primary-button" disabled={create.isPending}>
              Create version 1
            </button>
            <button
              type="button"
              className="secondary-button"
              onClick={() => setShowCreate(false)}
            >
              Cancel
            </button>
          </div>
        </form>
      ) : null}
      <div className="split-layout">
        <section className="panel list-panel">
          <div className="panel-heading">
            <div>
              <h2>Policy families</h2>
              <p>{policies.data?.items.length ?? 0} immutable versions</p>
            </div>
            <button
              className="table-action"
              onClick={() => setShowCreate(true)}
            >
              <Plus /> New
            </button>
          </div>
          {policies.isLoading ? (
            <LoadingTable />
          ) : (
            policies.data?.items.map((item) => (
              <button
                key={item.id}
                className={`record-button ${current?.id === item.id ? "selected" : ""}`}
                onClick={() => {
                  setSelected(item.id);
                  setDraft(null);
                }}
              >
                <span>
                  <strong>{item.name}</strong>
                  <small>
                    {item.preset} preset · family {item.familyId.slice(0, 8)}
                  </small>
                </span>
                <em>v{item.versionNumber}</em>
              </button>
            ))
          )}
        </section>
        <section className="panel editor-panel">
          {current && config ? (
            <>
              <div className="panel-heading">
                <div>
                  <h2>
                    {current.name} · v{current.versionNumber}
                  </h2>
                  <p>
                    Changes create v{current.versionNumber + 1}; this version
                    remains unchanged.
                  </p>
                </div>
                <span className="status-pill">{config.preset}</span>
              </div>
              <div className="form-grid">
                <Field label="Preset">
                  <div className="profile-control policy-presets" role="group" aria-label="Policy preset">
                    {[
                      "eco",
                      "balanced",
                      "strict_quality",
                      "cost_saver",
                      "custom",
                    ].map((value) => (
                      <button
                        type="button"
                        key={value}
                        className={config.preset === value ? "active" : ""}
                        onClick={() => {
                          patch("preset", value);
                          if (value !== "custom") {
                            setDraft((currentDraft) => ({
                              ...(currentDraft ?? config),
                              preset: value,
                              weights: presetWeights[value],
                              maxCostIncreasePct:
                                value === "balanced" ? 10 : 0,
                            }));
                          }
                        }}
                      >
                        {value}
                      </button>
                    ))}
                  </div>
                </Field>
                <Field label="Maximum p95 latency (ms)">
                  <input
                    type="number"
                    min="1"
                    value={config.maxLatencyMs}
                    onChange={(event) =>
                      patch("maxLatencyMs", Number(event.target.value))
                    }
                  />
                </Field>
                <Field label="Maximum cost increase (%)">
                  <input
                    type="number"
                    min="0"
                    max="100"
                    value={config.maxCostIncreasePct}
                    onChange={(event) =>
                      patch("maxCostIncreasePct", Number(event.target.value))
                    }
                  />
                </Field>
                <Field label="Minimum router confidence">
                  <input
                    type="number"
                    min="0"
                    max="1"
                    step="0.01"
                    value={config.minRouterConfidence}
                    onChange={(event) =>
                      patch("minRouterConfidence", Number(event.target.value))
                    }
                  />
                </Field>
                <Field label="Minimum SLM confidence">
                  <input
                    type="number"
                    min="0"
                    max="1"
                    step="0.01"
                    value={config.minSlmConfidence}
                    onChange={(event) =>
                      patch("minSlmConfidence", Number(event.target.value))
                    }
                  />
                </Field>
                <Field label="Clean threshold">
                  <input
                    type="number"
                    value={config.cleanThresholdGco2Kwh}
                    onChange={(event) =>
                      patch("cleanThresholdGco2Kwh", Number(event.target.value))
                    }
                  />
                  <small>gCO₂e/kWh · Policy threshold</small>
                </Field>
                <Field label="Dirty threshold">
                  <input
                    type="number"
                    value={config.dirtyThresholdGco2Kwh}
                    onChange={(event) =>
                      patch("dirtyThresholdGco2Kwh", Number(event.target.value))
                    }
                  />
                  <small>gCO₂e/kWh · Policy threshold</small>
                </Field>
                <Field label="Semantic similarity">
                  <input
                    type="number"
                    min="0.9"
                    max="0.99"
                    step="0.01"
                    value={config.semanticSimilarityThreshold}
                    onChange={(event) =>
                      patch(
                        "semanticSimilarityThreshold",
                        Number(event.target.value),
                      )
                    }
                  />
                  {Number(config.semanticSimilarityThreshold) < 0.93 ? (
                    <small className="field-warning">
                      Below 0.93 increases false-hit risk.
                    </small>
                  ) : null}
                </Field>
                <Field label="Cache TTL (seconds)">
                  <input
                    type="number"
                    min="60"
                    value={config.cacheTtlSeconds}
                    onChange={(event) =>
                      patch("cacheTtlSeconds", Number(event.target.value))
                    }
                  />
                </Field>
                <Field label="Stale carbon allowance (minutes)">
                  <input
                    type="number"
                    min="0"
                    value={config.allowStaleCarbonMinutes}
                    onChange={(event) =>
                      patch(
                        "allowStaleCarbonMinutes",
                        Number(event.target.value),
                      )
                    }
                  />
                </Field>
                <Field
                  label="Semantic-cache task types"
                  hint="Comma-separated task types eligible for semantic reuse."
                >
                  <input
                    value={(config.semanticCacheTaskTypes ?? []).join(", ")}
                    onChange={(event) =>
                      patch(
                        "semanticCacheTaskTypes",
                        event.target.value
                          .split(",")
                          .map((value) => value.trim())
                          .filter(Boolean),
                      )
                    }
                  />
                </Field>
                <Field label="Allowed regions" hint="Blank permits every configured region.">
                  <input
                    value={(config.allowedRegions ?? []).join(", ")}
                    onChange={(event) =>
                      patch(
                        "allowedRegions",
                        event.target.value
                          .split(",")
                          .map((value) => value.trim())
                          .filter(Boolean),
                      )
                    }
                  />
                </Field>
                <Field label="Cache namespace version">
                  <input
                    type="number"
                    min="1"
                    value={config.namespaceVersion}
                    onChange={(event) =>
                      patch("namespaceVersion", Number(event.target.value))
                    }
                  />
                </Field>
              </div>
              <fieldset className="check-field">
                <legend>Endpoint allowlist</legend>
                <div className="check-grid">
                  {endpoints.data?.items.map((item) => (
                    <label key={item.id}>
                      <input
                        type="checkbox"
                        checked={(config.enabledEndpointIds ?? []).includes(
                          item.id,
                        )}
                        onChange={(event) =>
                          patch(
                            "enabledEndpointIds",
                            event.target.checked
                              ? [...(config.enabledEndpointIds ?? []), item.id]
                              : (config.enabledEndpointIds ?? []).filter(
                                  (id: string) => id !== item.id,
                                ),
                          )
                        }
                      />
                      {item.name}
                      <small>
                        {item.qualityTier} · {item.region}
                      </small>
                    </label>
                  ))}
                </div>
              </fieldset>
              <fieldset className="weight-editor">
                <legend>
                  Scoring weights{" "}
                  <span
                    className={
                      Math.abs(weightSum - 1) <= 0.001 ? "sum-ok" : "sum-error"
                    }
                  >
                    sum {weightSum.toFixed(2)}
                  </span>
                </legend>
                {["carbon", "cost", "latency", "quality", "evidence"].map(
                  (name) => (
                    <Field key={name} label={name}>
                      <input
                        type="number"
                        min="0"
                        max="1"
                        step="0.05"
                        value={weights[name]}
                        onChange={(event) =>
                          patchWeight(name, Number(event.target.value))
                        }
                      />
                    </Field>
                  ),
                )}
              </fieldset>
              <div className="toggle-row">
                <label>
                  <input
                    type="checkbox"
                    checked={config.semanticCacheEnabled}
                    onChange={(event) =>
                      patch("semanticCacheEnabled", event.target.checked)
                    }
                  />{" "}
                  Semantic cache
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={config.qualityFallbackEnabled}
                    onChange={(event) =>
                      patch("qualityFallbackEnabled", event.target.checked)
                    }
                  />{" "}
                  Quality fallback
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={config.allowExperimentalModels}
                    onChange={(event) =>
                      patch("allowExperimentalModels", event.target.checked)
                    }
                  />{" "}
                  Experimental models
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={config.sensitiveRequiresSelfHosted}
                    onChange={(event) =>
                      patch("sensitiveRequiresSelfHosted", event.target.checked)
                    }
                  />{" "}
                  Sensitive requests require self-hosted
                </label>
              </div>
              <Field
                label="Task rules (JSON)"
                hint="Rules match taskType and may set minimumQualityTier."
              >
                <textarea
                  rows={5}
                  value={taskRulesText}
                  onChange={(event) => {
                    setTaskRulesText(event.target.value);
                    try {
                      const value: unknown = JSON.parse(event.target.value);
                      if (!Array.isArray(value)) {
                        setTaskRulesError("Task rules must be a JSON array.");
                        return;
                      }
                      setTaskRulesError("");
                      patch("taskRules", value);
                    } catch {
                      setTaskRulesError("Enter valid JSON before saving.");
                    }
                  }}
                />
                {taskRulesError ? (
                  <small className="field-warning" role="alert">
                    {taskRulesError}
                  </small>
                ) : null}
              </Field>
              {draft ? (
                <div className="notice compact-notice">
                  <AlertTriangle />
                  Save this draft as a new immutable version before simulating
                  or activating it.
                </div>
              ) : null}
              <div className="editor-actions">
                <button
                  className="primary-button"
                  disabled={
                    !draft ||
                    clone.isPending ||
                    !!taskRulesError ||
                    Math.abs(weightSum - 1) > 0.001
                  }
                  onClick={() => clone.mutate()}
                >
                  <Plus /> Save new version
                </button>
                {models.data?.items.map((model) => (
                  <ConfirmAction
                    key={model.id}
                    label={`Activate for ${model.alias}`}
                    title="Activate immutable policy"
                    detail={`All new ${model.alias} requests will use ${current.name} v${current.versionNumber}. In-flight requests are unchanged.`}
                    disabled={!!draft}
                    onConfirm={() => activate.mutate(model.id)}
                  />
                ))}
              </div>
              <div className="simulator">
                <div className="panel-heading">
                  <div>
                    <h2>Dry-run simulator</h2>
                    <p>
                      No model call; prompt is redacted before classification.
                    </p>
                  </div>
                </div>
                <div className="inline-form">
                  <textarea
                    value={prompt}
                    onChange={(event) => setPrompt(event.target.value)}
                    rows={2}
                  />
                  <button
                    className="secondary-button"
                    onClick={() => simulation.mutate()}
                    disabled={simulation.isPending || !!draft}
                  >
                    <Play /> Simulate
                  </button>
                </div>
                {simulation.data ? (
                  <div className="simulation-result">
                    <div>
                      <strong>Selected</strong>
                      <span>{simulation.data.selectedEndpointId}</span>
                    </div>
                    <div>
                      <strong>Reason</strong>
                      <span>{simulation.data.selectionReason}</span>
                    </div>
                    <pre>
                      {JSON.stringify(simulation.data.classification, null, 2)}
                    </pre>
                    <div className="candidate-grid">
                      {simulation.data.candidates?.map((candidate: Item) => (
                        <div
                          key={candidate.endpoint_id ?? candidate.endpointId}
                          className={
                            (candidate.excluded_reason ??
                            candidate.excludedReason)
                              ? "candidate excluded"
                              : "candidate"
                          }
                        >
                          <strong>{candidate.name}</strong>
                          <span>
                            {candidate.excluded_reason ??
                              candidate.excludedReason ??
                              `score ${Number(candidate.score ?? 0).toFixed(3)}`}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>
            </>
          ) : (
            <Empty
              icon={<Route />}
              title="No policies"
              detail="Create the first immutable policy family, then simulate and activate it."
              action={
                <button
                  className="primary-button"
                  onClick={() => setShowCreate(true)}
                >
                  <Plus /> Create first policy
                </button>
              }
            />
          )}
        </section>
      </div>
    </div>
  );
}

function EndpointsView() {
  const client = useQueryClient();
  const endpoints = useQuery({
    queryKey: ["model-endpoints"],
    queryFn: () => api<List>("/model-endpoints"),
  });
  const logical = useQuery({
    queryKey: ["logical-models"],
    queryFn: () => api<List>("/logical-models"),
  });
  const policies = useQuery({
    queryKey: ["policies"],
    queryFn: () => api<List>("/policies"),
  });
  const [showCreate, setShowCreate] = useState(false);
  const [testResult, setTestResult] = useState<Item>();
  const [editingEndpoint, setEditingEndpoint] = useState<Item>();
  const [editingLogical, setEditingLogical] = useState<Item>();
  const test = useMutation({
    mutationFn: (id: string) =>
      api<Item>(`/model-endpoints/${id}/test`, { method: "POST", body: "{}" }),
    onSuccess: setTestResult,
  });
  const create = useMutation({
    mutationFn: (body: Item) =>
      api("/model-endpoints", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => {
      setShowCreate(false);
      void client.invalidateQueries({ queryKey: ["model-endpoints"] });
    },
  });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Item }) =>
      api<Item>(`/model-endpoints/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: (value) => {
      setEditingEndpoint(value);
      void client.invalidateQueries({ queryKey: ["model-endpoints"] });
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) =>
      api<void>(`/model-endpoints/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      setEditingEndpoint(undefined);
      void client.invalidateQueries({ queryKey: ["model-endpoints"] });
    },
  });
  const createLogical = useMutation({
    mutationFn: (body: Item) =>
      api("/logical-models", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () =>
      void client.invalidateQueries({ queryKey: ["logical-models"] }),
  });
  const updateLogical = useMutation({
    mutationFn: async ({
      id,
      body,
      policyId,
      currentPolicyId,
    }: {
      id: string;
      body: Item;
      policyId: string;
      currentPolicyId: string;
    }) => {
      const value = await api<Item>(`/logical-models/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      if (policyId !== currentPolicyId) {
        await api(`/logical-models/${id}/activate-policy`, {
          method: "POST",
          body: JSON.stringify({ policyId }),
        });
      }
      return { ...value, activePolicyId: policyId };
    },
    onSuccess: (value) => {
      setEditingLogical(value);
      void client.invalidateQueries({ queryKey: ["logical-models"] });
    },
  });
  function submitEndpoint(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const capabilities = data.getAll("capabilities");
    create.mutate({
      name: data.get("name"),
      provider: data.get("provider"),
      baseUrl: data.get("baseUrl"),
      credentialRef: data.get("credentialRef") || null,
      physicalModel: data.get("physicalModel"),
      azureDeploymentType: data.get("azureDeploymentType") || null,
      region: data.get("region"),
      gridZone: data.get("gridZone"),
      gridLookupMode: data.get("gridLookupMode"),
      gridDataCenterProvider: data.get("gridDataCenterProvider") || null,
      gridDataCenterRegion: data.get("gridDataCenterRegion") || null,
      processingLocationEvidence: data.get("processingLocationEvidence"),
      gridAttribution: data.get("gridAttribution"),
      qualityTier: data.get("qualityTier"),
      capabilities,
      contextWindowTokens: Number(data.get("contextWindowTokens")),
      inputUsdPerMillionTokens: Number(data.get("inputPrice")),
      outputUsdPerMillionTokens: Number(data.get("outputPrice")),
      fixedRequestKwh: Number(data.get("fixedKwh")),
      inputKwhPer1kTokens: Number(data.get("inputKwh")),
      outputKwhPer1kTokens: Number(data.get("outputKwh")),
      energyEvidence: data.get("energyEvidence"),
      latencyP50Ms: Number(data.get("p50")),
      latencyP95Ms: Number(data.get("p95")),
      selfHosted: data.get("selfHosted") === "on",
      slmProfileId: data.get("slmProfileId") || null,
      baselineConcurrency: Number(data.get("baselineConcurrency")),
      concurrencyTarget: Number(data.get("concurrencyTarget")),
      enabled: data.get("enabled") === "on",
    });
  }
  function submitLogical(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const endpointIds = data.getAll("endpointIds") as string[];
    createLogical.mutate({
      alias: data.get("alias"),
      displayName: data.get("displayName"),
      endpointIds,
      baselineEndpointId: data.get("baselineEndpointId"),
      requiredFallbackEndpointId: data.get("fallbackEndpointId"),
      activePolicyId: data.get("activePolicyId"),
    });
  }
  function submitEndpointEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editingEndpoint) return;
    const data = new FormData(event.currentTarget);
    update.mutate({
      id: editingEndpoint.id,
      body: {
        name: data.get("name"),
        provider: data.get("provider"),
        baseUrl: data.get("baseUrl"),
        credentialRef: data.get("credentialRef") || null,
        physicalModel: data.get("physicalModel"),
        azureDeploymentType: data.get("azureDeploymentType") || null,
        region: data.get("region"),
        gridZone: data.get("gridZone"),
        gridLookupMode: data.get("gridLookupMode"),
        gridDataCenterProvider: data.get("gridDataCenterProvider") || null,
        gridDataCenterRegion: data.get("gridDataCenterRegion") || null,
        processingLocationEvidence: data.get("processingLocationEvidence"),
        gridAttribution: data.get("gridAttribution"),
        qualityTier: data.get("qualityTier"),
        capabilities: data.getAll("capabilities"),
        contextWindowTokens: Number(data.get("contextWindowTokens")),
        inputUsdPerMillionTokens: Number(data.get("inputPrice")),
        outputUsdPerMillionTokens: Number(data.get("outputPrice")),
        fixedRequestKwh: Number(data.get("fixedKwh")),
        inputKwhPer1kTokens: Number(data.get("inputKwh")),
        outputKwhPer1kTokens: Number(data.get("outputKwh")),
        energyEvidence: data.get("energyEvidence"),
        latencyP50Ms: Number(data.get("p50")),
        latencyP95Ms: Number(data.get("p95")),
        selfHosted: data.get("selfHosted") === "on",
        slmProfileId: data.get("slmProfileId") || null,
        baselineConcurrency: Number(data.get("baselineConcurrency")),
        concurrencyTarget: Number(data.get("concurrencyTarget")),
        enabled: data.get("enabled") === "on",
      },
    });
  }
  function submitLogicalEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editingLogical) return;
    const data = new FormData(event.currentTarget);
    const policyId = String(data.get("activePolicyId"));
    updateLogical.mutate({
      id: editingLogical.id,
      currentPolicyId: editingLogical.activePolicyId,
      policyId,
      body: {
        displayName: data.get("displayName"),
        endpointIds: data.getAll("endpointIds"),
        baselineEndpointId: data.get("baselineEndpointId"),
        requiredFallbackEndpointId: data.get("fallbackEndpointId"),
        enabled: data.get("enabled") === "on",
      },
    });
  }
  return (
    <div className="page">
      <Header
        eyebrow="REGISTRY / EXPLICIT POOLS"
        title="Model Endpoints"
        description="Provider identity, processing-location evidence, grid attribution, energy coefficients, and logical mappings."
        icon={<Boxes />}
      />
      <ErrorBanner
        error={
          (endpoints.error ??
            logical.error ??
            policies.error ??
            create.error ??
            update.error ??
            remove.error ??
            createLogical.error ??
            updateLogical.error ??
            test.error) as Error | null
        }
        retry={() => endpoints.refetch()}
      />
      <div className="toolbar">
        <button
          className="primary-button"
          onClick={() => setShowCreate(!showCreate)}
        >
          <Plus /> Add endpoint
        </button>
        <span>
          Credentials are stored only as <code>env:VARIABLE_NAME</code>.
        </span>
      </div>
      {showCreate ? (
        <form className="panel create-form" onSubmit={submitEndpoint}>
          <div className="panel-heading">
            <div>
              <h2>Register physical endpoint</h2>
              <p>
                All capability and coefficient claims are operator supplied.
              </p>
            </div>
          </div>
          <div className="form-grid three">
            <Field label="Name">
              <input required name="name" />
            </Field>
            <Field label="Provider">
              <select name="provider">
                {[
                  "azure_openai",
                  "freesolo",
                  "gemini",
                  "openai",
                  "ollama",
                  "vllm",
                  "openai_compatible",
                  "fake",
                ].map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </Field>
            <Field label="Physical model">
              <input required name="physicalModel" />
            </Field>
            <Field
              label="Azure deployment type"
              hint="Required for Azure. Global and Data Zone deployments are intentionally excluded."
            >
              <select name="azureDeploymentType" defaultValue="">
                <option value="">Not Azure</option>
                <option value="standard">Standard (regional)</option>
                <option value="provisioned_managed">
                  Provisioned Managed (regional)
                </option>
              </select>
            </Field>
            <Field label="Base URL">
              <input
                required
                type="url"
                name="baseUrl"
                placeholder="https://service.example/v1"
              />
            </Field>
            <Field label="Credential reference">
              <input
                name="credentialRef"
                pattern="env:[A-Z][A-Z0-9_]*"
                placeholder="env:PROVIDER_API_KEY"
              />
            </Field>
            <Field label="Tier">
              <select name="qualityTier">
                {["specialized", "small", "standard", "frontier"].map(
                  (value) => (
                    <option key={value}>{value}</option>
                  ),
                )}
              </select>
            </Field>
            <Field label="Region">
              <input required name="region" />
            </Field>
            <Field label="Grid zone">
              <input required name="gridZone" />
            </Field>
            <Field label="Grid lookup">
              <select name="gridLookupMode" defaultValue="zone">
                <option value="zone">Exact zone key</option>
                <option value="data_center">Electricity Maps data center</option>
              </select>
            </Field>
            <Field label="Data-center provider" hint="Required for data-center lookup, e.g. gcp.">
              <input name="gridDataCenterProvider" />
            </Field>
            <Field label="Data-center region" hint="Provider region identifier, e.g. europe-west1.">
              <input name="gridDataCenterRegion" />
            </Field>
            <Field label="Processing-location evidence">
              <select name="processingLocationEvidence" defaultValue="unknown">
                {[
                  "provider_contract",
                  "operator_declared",
                  "self_hosted",
                  "unknown",
                  "simulated",
                ].map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </Field>
            <Field label="Grid attribution">
              <select name="gridAttribution" defaultValue="unknown">
                {[
                  "electricity_maps_data_center",
                  "physical_grid",
                  "regional_proxy",
                  "operator_declared",
                  "unknown",
                  "simulated",
                ].map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </Field>
            <Field label="Context window">
              <input
                required
                type="number"
                min="1"
                name="contextWindowTokens"
                defaultValue="32768"
              />
            </Field>
            <Field label="Input $ / 1M tokens">
              <input
                required
                type="number"
                min="0"
                step="0.000001"
                name="inputPrice"
                defaultValue="0"
              />
            </Field>
            <Field label="Output $ / 1M tokens">
              <input
                required
                type="number"
                min="0"
                step="0.000001"
                name="outputPrice"
                defaultValue="0"
              />
            </Field>
            <Field label="Fixed kWh/request">
              <input
                required
                type="number"
                min="0"
                step="0.000001"
                name="fixedKwh"
                defaultValue="0"
              />
            </Field>
            <Field label="Input kWh/1K">
              <input
                required
                type="number"
                min="0"
                step="0.000001"
                name="inputKwh"
                defaultValue="0"
              />
            </Field>
            <Field label="Output kWh/1K">
              <input
                required
                type="number"
                min="0"
                step="0.000001"
                name="outputKwh"
                defaultValue="0"
              />
            </Field>
            <Field label="Energy evidence">
              <select name="energyEvidence" defaultValue="estimated">
                {["measured", "estimated", "stale", "simulated"].map(
                  (value) => (
                    <option key={value}>{value}</option>
                  ),
                )}
              </select>
            </Field>
            <Field label="p50 latency (ms)">
              <input
                required
                type="number"
                min="0"
                name="p50"
                defaultValue="250"
              />
            </Field>
            <Field label="p95 latency (ms)">
              <input
                required
                type="number"
                min="0"
                name="p95"
                defaultValue="1000"
              />
            </Field>
            <Field label="Baseline concurrency">
              <input
                required
                type="number"
                min="1"
                name="baselineConcurrency"
                defaultValue="16"
              />
            </Field>
            <Field label="Concurrency target">
              <input
                required
                type="number"
                min="1"
                name="concurrencyTarget"
                defaultValue="16"
              />
            </Field>
            <Field label="SLM profile ID">
              <input name="slmProfileId" />
            </Field>
          </div>
          <fieldset className="check-field">
            <legend>Capabilities</legend>
            <div className="toggle-row">
              {["text", "json_schema", "tools", "vision", "streaming"].map(
                (value) => (
                  <label key={value}>
                    <input
                      type="checkbox"
                      name="capabilities"
                      value={value}
                      defaultChecked={value === "text" || value === "streaming"}
                    />{" "}
                    {value}
                  </label>
                ),
              )}
              <label>
                <input type="checkbox" name="selfHosted" /> Self-hosted
              </label>
              <label>
                <input type="checkbox" name="enabled" defaultChecked /> Enabled
              </label>
            </div>
          </fieldset>
          <div className="editor-actions">
            <button className="primary-button" disabled={create.isPending}>
              Create endpoint
            </button>
            <button
              type="button"
              className="secondary-button"
              onClick={() => setShowCreate(false)}
            >
              Cancel
            </button>
          </div>
        </form>
      ) : null}
      <section className="panel data-panel">
        <div className="panel-heading">
          <div>
            <h2>Physical endpoints</h2>
            <p>
              Testing is an explicit, non-generating model-list/health probe.
            </p>
          </div>
          <span className="stale-time">Reconciles every 30 seconds</span>
        </div>
        {endpoints.isLoading ? (
          <LoadingTable />
        ) : endpoints.data?.items.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {[
                    "Name",
                    "Provider",
                    "Physical model",
                    "Region",
                    "Grid claim",
                    "Tier",
                    "Health",
                    "p95",
                    "Input price",
                    "Output price",
                    "Energy",
                    "Node",
                    "Enabled",
                    "Actions",
                  ].map((value) => (
                    <th key={value}>{value}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {endpoints.data.items.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <strong>{item.name}</strong>
                    </td>
                    <td>
                      {item.provider}
                      {item.azureDeploymentType
                        ? ` · ${item.azureDeploymentType}`
                        : ""}
                    </td>
                    <td>{item.physicalModel}</td>
                    <td>{item.region}</td>
                    <td>{item.gridAttribution ?? "unknown"}</td>
                    <td>{item.qualityTier}</td>
                    <td>
                      <span className={`status-pill ${item.healthState}`}>
                        {item.healthState}
                      </span>
                    </td>
                    <td>{item.latencyP95Ms} ms</td>
                    <td>${item.inputUsdPerMillionTokens}</td>
                    <td>${item.outputUsdPerMillionTokens}</td>
                    <td>{evidence(item.energyEvidence)}</td>
                    <td>{item.nodeAgentId ?? "—"}</td>
                    <td>{item.enabled ? "Enabled" : "Disabled"}</td>
                    <td>
                      <div className="inline-form">
                        <button
                          className="table-action"
                          onClick={() => test.mutate(item.id)}
                        >
                          Test
                        </button>
                        <button
                          className="table-action"
                          onClick={() => setEditingEndpoint(item)}
                        >
                          Edit
                        </button>
                        <ConfirmAction
                          label="Delete"
                          title={`Delete ${item.name}?`}
                          detail="This soft-deletes the endpoint. Endpoints still referenced by a logical model must be detached first."
                          danger
                          disabled={remove.isPending}
                          onConfirm={() => remove.mutate(item.id)}
                        />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            icon={<Boxes />}
            title="No endpoints"
            detail="Register a fake or real endpoint to create a logical pool."
            action={
              <button
                className="primary-button"
                onClick={() => setShowCreate(true)}
              >
                Add endpoint
              </button>
            }
          />
        )}
      </section>
      {testResult ? (
        <div
          className={`notice ${
            testResult.status === "healthy" && testResult.carbonAccountingAvailable
              ? ""
              : "warning"
          }`}
        >
          <CheckCircle2 />
          <strong>{testResult.status}</strong>
          <span>
            {testResult.providerModel ?? testResult.error} ·{" "}
            {testResult.latencyMs} ms ·{" "}
            {testResult.carbonAccountingAvailable
              ? `${testResult.grid?.intensity_gco2_kwh} gCO₂e/kWh (${testResult.gridAttribution})`
              : "carbon accounting unavailable"}
          </span>
        </div>
      ) : null}
      {editingEndpoint ? (
        <form
          key={editingEndpoint.id}
          className="panel create-form"
          onSubmit={submitEndpointEdit}
        >
          <div className="panel-heading">
            <div>
              <h2>Edit endpoint</h2>
              <p>
                Every registry field is explicit. Saving revalidates credential
                references, SLM ownership, concurrency, and URL safety.
              </p>
            </div>
          </div>
          <div className="form-grid three">
            <Field label="Name">
              <input required name="name" defaultValue={editingEndpoint.name} />
            </Field>
            <Field label="Provider">
              <select name="provider" defaultValue={editingEndpoint.provider}>
                {[
                  "azure_openai",
                  "freesolo",
                  "gemini",
                  "openai",
                  "ollama",
                  "vllm",
                  "openai_compatible",
                  "fake",
                ].map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </Field>
            <Field label="Physical model">
              <input
                required
                name="physicalModel"
                defaultValue={editingEndpoint.physicalModel}
              />
            </Field>
            <Field
              label="Azure deployment type"
              hint="Only single-region Standard or Provisioned Managed deployments qualify."
            >
              <select
                name="azureDeploymentType"
                defaultValue={editingEndpoint.azureDeploymentType ?? ""}
              >
                <option value="">Not Azure</option>
                <option value="standard">Standard (regional)</option>
                <option value="provisioned_managed">
                  Provisioned Managed (regional)
                </option>
              </select>
            </Field>
            <Field label="Base URL">
              <input
                required
                type="url"
                name="baseUrl"
                defaultValue={editingEndpoint.baseUrl}
              />
            </Field>
            <Field label="Credential reference">
              <input
                name="credentialRef"
                pattern="env:[A-Z][A-Z0-9_]*"
                defaultValue={editingEndpoint.credentialRef ?? ""}
              />
            </Field>
            <Field label="Tier">
              <select
                name="qualityTier"
                defaultValue={editingEndpoint.qualityTier}
              >
                {["specialized", "small", "standard", "frontier"].map(
                  (value) => (
                    <option key={value}>{value}</option>
                  ),
                )}
              </select>
            </Field>
            <Field label="Region">
              <input
                required
                name="region"
                defaultValue={editingEndpoint.region}
              />
            </Field>
            <Field label="Grid zone">
              <input
                required
                name="gridZone"
                defaultValue={editingEndpoint.gridZone}
              />
            </Field>
            <Field label="Grid lookup">
              <select
                name="gridLookupMode"
                defaultValue={editingEndpoint.gridLookupMode ?? "zone"}
              >
                <option value="zone">Exact zone key</option>
                <option value="data_center">Electricity Maps data center</option>
              </select>
            </Field>
            <Field label="Data-center provider">
              <input
                name="gridDataCenterProvider"
                defaultValue={editingEndpoint.gridDataCenterProvider ?? ""}
              />
            </Field>
            <Field label="Data-center region">
              <input
                name="gridDataCenterRegion"
                defaultValue={editingEndpoint.gridDataCenterRegion ?? ""}
              />
            </Field>
            <Field label="Processing-location evidence">
              <select
                name="processingLocationEvidence"
                defaultValue={editingEndpoint.processingLocationEvidence ?? "unknown"}
              >
                {[
                  "provider_contract",
                  "operator_declared",
                  "self_hosted",
                  "unknown",
                  "simulated",
                ].map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </Field>
            <Field label="Grid attribution">
              <select
                name="gridAttribution"
                defaultValue={editingEndpoint.gridAttribution ?? "unknown"}
              >
                {[
                  "electricity_maps_data_center",
                  "physical_grid",
                  "regional_proxy",
                  "operator_declared",
                  "unknown",
                  "simulated",
                ].map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </Field>
            <Field label="Context window">
              <input
                required
                type="number"
                min="1"
                name="contextWindowTokens"
                defaultValue={editingEndpoint.contextWindowTokens}
              />
            </Field>
            <Field label="Input $ / 1M tokens">
              <input
                required
                type="number"
                min="0"
                step="0.000001"
                name="inputPrice"
                defaultValue={editingEndpoint.inputUsdPerMillionTokens}
              />
            </Field>
            <Field label="Output $ / 1M tokens">
              <input
                required
                type="number"
                min="0"
                step="0.000001"
                name="outputPrice"
                defaultValue={editingEndpoint.outputUsdPerMillionTokens}
              />
            </Field>
            <Field label="Fixed kWh/request">
              <input
                required
                type="number"
                min="0"
                step="0.000001"
                name="fixedKwh"
                defaultValue={editingEndpoint.fixedRequestKwh}
              />
            </Field>
            <Field label="Input kWh/1K">
              <input
                required
                type="number"
                min="0"
                step="0.000001"
                name="inputKwh"
                defaultValue={editingEndpoint.inputKwhPer1kTokens}
              />
            </Field>
            <Field label="Output kWh/1K">
              <input
                required
                type="number"
                min="0"
                step="0.000001"
                name="outputKwh"
                defaultValue={editingEndpoint.outputKwhPer1kTokens}
              />
            </Field>
            <Field label="Energy evidence">
              <select
                name="energyEvidence"
                defaultValue={editingEndpoint.energyEvidence}
              >
                {["measured", "estimated", "stale", "simulated"].map(
                  (value) => (
                    <option key={value}>{value}</option>
                  ),
                )}
              </select>
            </Field>
            <Field label="p50 latency (ms)">
              <input
                required
                type="number"
                min="0"
                name="p50"
                defaultValue={editingEndpoint.latencyP50Ms}
              />
            </Field>
            <Field label="p95 latency (ms)">
              <input
                required
                type="number"
                min="0"
                name="p95"
                defaultValue={editingEndpoint.latencyP95Ms}
              />
            </Field>
            <Field label="Baseline concurrency">
              <input
                required
                type="number"
                min="1"
                name="baselineConcurrency"
                defaultValue={editingEndpoint.baselineConcurrency}
              />
            </Field>
            <Field label="Concurrency target">
              <input
                required
                type="number"
                min="1"
                name="concurrencyTarget"
                defaultValue={editingEndpoint.concurrencyTarget}
              />
            </Field>
            <Field label="SLM profile ID">
              <input
                name="slmProfileId"
                defaultValue={editingEndpoint.slmProfileId ?? ""}
              />
            </Field>
          </div>
          <fieldset className="check-field">
            <legend>Capabilities and state</legend>
            <div className="toggle-row">
              {["text", "json_schema", "tools", "vision", "streaming"].map(
                (value) => (
                  <label key={value}>
                    <input
                      type="checkbox"
                      name="capabilities"
                      value={value}
                      defaultChecked={editingEndpoint.capabilities?.includes(
                        value,
                      )}
                    />{" "}
                    {value}
                  </label>
                ),
              )}
              <label>
                <input
                  type="checkbox"
                  name="selfHosted"
                  defaultChecked={editingEndpoint.selfHosted}
                />{" "}
                Self-hosted
              </label>
              <label>
                <input
                  type="checkbox"
                  name="enabled"
                  defaultChecked={editingEndpoint.enabled}
                />{" "}
                Enabled
              </label>
            </div>
          </fieldset>
          <div className="editor-actions">
            <button className="primary-button" disabled={update.isPending}>
              Save endpoint
            </button>
            <button
              type="button"
              className="secondary-button"
              onClick={() => setEditingEndpoint(undefined)}
            >
              Close
            </button>
          </div>
        </form>
      ) : null}
      <section className="panel create-form">
        <div className="panel-heading">
          <div>
            <h2>Logical model mappings</h2>
            <p>
              Clients see aliases only; every pool, baseline, and fallback is
              explicit.
            </p>
          </div>
        </div>
        <div className="logical-grid">
          {logical.data?.items.map((item) => (
            <div className="logical-card" key={item.id}>
              <strong>{item.alias}</strong>
              <small>{item.displayName}</small>
              <span>{item.endpointIds?.length} endpoints</span>
              <dl>
                <dt>Baseline</dt>
                <dd>{item.baselineEndpointId}</dd>
                <dt>Fallback</dt>
                <dd>{item.requiredFallbackEndpointId}</dd>
                <dt>Policy</dt>
                <dd>{item.activePolicyId}</dd>
              </dl>
              <button
                className="table-action"
                onClick={() => setEditingLogical(item)}
              >
                Edit mapping
              </button>
            </div>
          ))}
        </div>
        {editingLogical ? (
          <form
            key={editingLogical.id}
            onSubmit={submitLogicalEdit}
            className="form-grid"
          >
            <Field label="Alias (immutable client contract)">
              <input value={editingLogical.alias} disabled />
            </Field>
            <Field label="Display name">
              <input
                required
                name="displayName"
                defaultValue={editingLogical.displayName}
              />
            </Field>
            <Field label="Baseline">
              <select
                required
                name="baselineEndpointId"
                defaultValue={editingLogical.baselineEndpointId}
              >
                {endpoints.data?.items.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Required fallback">
              <select
                required
                name="fallbackEndpointId"
                defaultValue={editingLogical.requiredFallbackEndpointId}
              >
                {endpoints.data?.items
                  .filter(
                    (item) => item.qualityTier === "frontier" && item.enabled,
                  )
                  .map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
              </select>
            </Field>
            <Field label="Active policy">
              <select
                required
                name="activePolicyId"
                defaultValue={editingLogical.activePolicyId}
              >
                {policies.data?.items.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} v{item.versionNumber}
                  </option>
                ))}
              </select>
            </Field>
            <fieldset className="check-field">
              <legend>Endpoint pool</legend>
              {endpoints.data?.items.map((item) => (
                <label key={item.id}>
                  <input
                    type="checkbox"
                    name="endpointIds"
                    value={item.id}
                    defaultChecked={editingLogical.endpointIds?.includes(item.id)}
                  />{" "}
                  {item.name}
                </label>
              ))}
              <label>
                <input
                  type="checkbox"
                  name="enabled"
                  defaultChecked={editingLogical.enabled}
                />{" "}
                Logical model enabled
              </label>
            </fieldset>
            <div className="editor-actions">
              <button
                className="primary-button"
                disabled={updateLogical.isPending}
              >
                Save mapping
              </button>
              <button
                type="button"
                className="secondary-button"
                onClick={() => setEditingLogical(undefined)}
              >
                Close
              </button>
            </div>
          </form>
        ) : null}
        <form onSubmit={submitLogical} className="form-grid">
          <Field label="Alias">
            <input required name="alias" />
          </Field>
          <Field label="Display name">
            <input required name="displayName" />
          </Field>
          <Field label="Baseline">
            <select required name="baselineEndpointId">
              {endpoints.data?.items.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Required fallback">
            <select required name="fallbackEndpointId">
              {endpoints.data?.items
                .filter(
                  (item) => item.qualityTier === "frontier" && item.enabled,
                )
                .map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
            </select>
          </Field>
          <Field label="Active policy">
            <select required name="activePolicyId">
              {policies.data?.items.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name} v{item.versionNumber}
                </option>
              ))}
            </select>
          </Field>
          <fieldset className="check-field">
            <legend>Endpoint pool</legend>
            {endpoints.data?.items.map((item) => (
              <label key={item.id}>
                <input type="checkbox" name="endpointIds" value={item.id} />{" "}
                {item.name}
              </label>
            ))}
          </fieldset>
          <button className="primary-button" disabled={createLogical.isPending}>
            Create logical model
          </button>
        </form>
      </section>
    </div>
  );
}

const supportExampleSeed = [
  {
    input: "What is the return window for an unused item?",
    output: {
      answer: "Unused items may be returned within 30 days.",
      confidence: 0.98,
      policy_ids: ["returns-30-day"],
      needs_human: false,
    },
    metadata: {
      task_type: "policy_qa",
      difficulty: "easy",
      paraphrase_group: "returns-window",
      source: "operator_reviewed",
    },
  },
];

const routerExampleSeed = [
  {
    input: "Summarize this public shipping policy in two bullets.",
    output: {
      complexity: "easy",
      task_type: "summarization",
      risk: "low",
      predicted_output_tokens: 96,
      required_capabilities: ["text"],
    },
    metadata: {
      difficulty: "easy",
      paraphrase_group: "shipping-summary",
      source: "operator_reviewed",
    },
  },
];

function SlmStudioView() {
  const client = useQueryClient();
  const profiles = useQuery({
    queryKey: ["slm-profiles"],
    queryFn: () => api<List>("/slm-profiles"),
  });
  const runs = useQuery({
    queryKey: ["training-runs"],
    queryFn: () => api<List>("/training-runs"),
  });
  const [step, setStep] = useState(0);
  const [profileId, setProfileId] = useState("");
  const [datasetId, setDatasetId] = useState("");
  const [runId, setRunId] = useState("");
  const [trainingKind, setTrainingKind] = useState<"support_slm" | "router">(
    "support_slm",
  );
  const [manualExamples, setManualExamples] = useState(
    JSON.stringify(supportExampleSeed, null, 2),
  );
  const profile = useQuery({
    queryKey: ["slm-profile", profileId],
    queryFn: () => api<Item>(`/slm-profiles/${profileId}`),
    enabled: !!profileId,
  });
  const dataset = useQuery({
    queryKey: ["dataset", datasetId],
    queryFn: () => api<Item>(`/datasets/${datasetId}`),
    enabled: !!datasetId,
  });
  const examples = useQuery({
    queryKey: ["examples", datasetId],
    queryFn: () => api<List>(`/datasets/${datasetId}/examples?limit=200`),
    enabled: !!datasetId,
  });
  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api<Item>(`/training-runs/${runId}`),
    enabled: !!runId,
    refetchInterval: (query) =>
      ["validating", "training", "evaluating", "deploying"].includes(
        query.state.data?.status,
      )
        ? 3000
        : false,
  });
  const createProfile = useMutation({
    mutationFn: (body: Item) =>
      api<Item>("/slm-profiles", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: (value) => {
      setProfileId(value.id);
      setStep(1);
      void client.invalidateQueries({ queryKey: ["slm-profiles"] });
    },
  });
  const updateProfile = useMutation({
    mutationFn: (body: Item) =>
      api<Item>(`/slm-profiles/${profileId}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void profile.refetch();
      void client.invalidateQueries({ queryKey: ["slm-profiles"] });
    },
  });
  const generate = useMutation({
    mutationFn: () =>
      api<Item>(`/slm-profiles/${profileId}/generate-dataset`, {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey("dataset") },
        body: JSON.stringify({
          target: 150,
          distribution: { difficulty: { easy: 50, medium: 70, hard: 30 } },
        }),
      }),
    onSuccess: (value) => {
      setDatasetId(value.datasetId);
      setStep(3);
    },
  });
  const importDataset = useMutation({
    mutationFn: () => {
      const parsed: unknown = JSON.parse(manualExamples);
      if (!Array.isArray(parsed))
        throw new Error("Manual examples must be a JSON array.");
      return api<Item>("/datasets/import", {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey("manual-dataset") },
        body: JSON.stringify({
          kind: trainingKind,
          ...(trainingKind === "support_slm"
            ? { slmProfileId: profileId }
            : {}),
          examples: parsed,
        }),
      });
    },
    onSuccess: (value) => {
      setDatasetId(value.id);
      setStep(3);
    },
  });
  const approve = useMutation({
    mutationFn: () =>
      api(`/datasets/${datasetId}/approve`, {
        method: "POST",
        body: JSON.stringify({ confirm: true }),
      }),
    onSuccess: () => {
      setStep(4);
      void dataset.refetch();
    },
  });
  const review = useMutation({
    mutationFn: ({ id, state }: { id: string; state: string }) =>
      api(`/datasets/${datasetId}/examples/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ review: state }),
      }),
    onSuccess: () => void examples.refetch(),
  });
  const editExample = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Item }) => {
      const output: unknown = JSON.parse(String(body.output));
      if (!output || typeof output !== "object" || Array.isArray(output)) {
        throw new Error("Example output must be a JSON object.");
      }
      return api<Item>(`/datasets/${datasetId}/examples/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ ...body, output }),
      });
    },
    onSuccess: (value) => {
      if (value.datasetId !== datasetId) setDatasetId(value.datasetId);
      void examples.refetch();
      void dataset.refetch();
    },
  });
  const createRun = useMutation({
    mutationFn: (body: Item) =>
      api<Item>("/training-runs", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: (value) => {
      setRunId(value.id);
      void client.invalidateQueries({ queryKey: ["training-runs"] });
    },
  });
  const launch = useMutation({
    mutationFn: (body: Item) =>
      api<Item>(`/training-runs/${runId}/launch`, {
        method: "POST",
        headers: {
          "Idempotency-Key": idempotencyKey(
            body.confirm ? "launch" : "validate",
          ),
        },
        body: JSON.stringify(body),
      }),
    onSuccess: () => void run.refetch(),
  });
  const deploy = useMutation({
    mutationFn: (experimental: boolean) =>
      api(`/training-runs/${runId}/deploy`, {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey("deploy") },
        body: JSON.stringify({ experimental }),
      }),
    onSuccess: () => {
      setStep(6);
      void run.refetch();
    },
  });
  const importRun = useMutation({
    mutationFn: (body: Item) => {
      const evalMetrics: unknown = JSON.parse(String(body.evalMetrics));
      if (
        !evalMetrics ||
        typeof evalMetrics !== "object" ||
        Array.isArray(evalMetrics)
      ) {
        throw new Error("Evaluation metrics must be a JSON object.");
      }
      return api<Item>("/training-runs/import", {
        method: "POST",
        body: JSON.stringify({ ...body, evalMetrics }),
      });
    },
    onSuccess: (value) => {
      setRunId(value.id);
      void client.invalidateQueries({ queryKey: ["training-runs"] });
    },
  });
  function submitProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const policyKey = String(data.get("policyKey"));
    createProfile.mutate({
      name: data.get("name"),
      businessName: data.get("businessName"),
      description: data.get("description"),
      definition: {
        allowed_tasks: data.getAll("tasks"),
        forbidden_topics: String(data.get("forbidden"))
          .split("\n")
          .filter(Boolean),
        supported_languages: [data.get("language")],
        tone: data.get("tone"),
        output_contract: "support_answer_v1",
      },
      policyDocuments: [
        {
          policyKey,
          title: data.get("policyTitle"),
          content: data.get("policyContent"),
        },
      ],
    });
  }
  function submitRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    createRun.mutate({
      datasetId,
      kind: trainingKind,
      algorithm: data.get("algorithm"),
      baseModel:
        trainingKind === "router"
          ? "Qwen/Qwen3.5-2B"
          : "Qwen/Qwen3.5-4B",
    });
  }
  function submitExample(event: FormEvent<HTMLFormElement>, id: string) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    editExample.mutate({
      id,
      body: {
        input: data.get("input"),
        output: String(data.get("output")),
        split: data.get("split"),
      },
    });
  }
  function submitProfileEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const documents: Item[] = (profile.data?.policyDocuments ?? []).map(
      (_document: Item, index: number) => ({
        policyKey: data.get(`policyKey-${index}`),
        title: data.get(`policyTitle-${index}`),
        content: data.get(`policyContent-${index}`),
      }),
    );
    const newKey = String(data.get("newPolicyKey") ?? "").trim();
    if (newKey) {
      documents.push({
        policyKey: newKey,
        title: data.get("newPolicyTitle") || newKey,
        content: data.get("newPolicyContent"),
      });
    }
    updateProfile.mutate({
      name: data.get("name"),
      businessName: data.get("businessName"),
      description: data.get("description"),
      definition: {
        ...profile.data?.definition,
        allowed_tasks: data.getAll("tasks"),
        forbidden_topics: String(data.get("forbidden"))
          .split("\n")
          .map((value) => value.trim())
          .filter(Boolean),
        supported_languages: [data.get("language")],
        tone: data.get("tone"),
      },
      policyDocuments: documents,
    });
  }
  function submitImportedRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const status = String(data.get("status"));
    importRun.mutate({
      datasetId,
      kind: trainingKind,
      algorithm: "sft",
      baseModel:
        trainingKind === "router"
          ? "Qwen/Qwen3.5-2B"
          : "Qwen/Qwen3.5-4B",
      freesoloRunId: data.get("freesoloRunId"),
      status,
      evalMetrics: String(data.get("evalMetrics") || "{}"),
      deploymentBaseUrl: data.get("deploymentBaseUrl") || undefined,
      deployedModelId: data.get("deployedModelId") || undefined,
    });
  }
  const selectedProfile =
    profile.data ?? profiles.data?.items.find((item) => item.id === profileId);
  function changeTrainingKind(value: "support_slm" | "router") {
    setTrainingKind(value);
    setDatasetId("");
    setRunId("");
    setManualExamples(
      JSON.stringify(
        value === "router" ? routerExampleSeed : supportExampleSeed,
        null,
        2,
      ),
    );
    setStep(0);
  }
  return (
    <div className="page">
      <Header
        eyebrow="SLM STUDIO / REVIEWED LIFECYCLE"
        title="SLM Studio"
        description="Define, generate, review, quote, evaluate, and deploy without starting spend implicitly."
        icon={<Bot />}
      />
      <div className="notice">
        <strong>Integrations are ready, inactive.</strong>
        <span>
          Gemini needs <code>GEMINI_API_KEY</code>; FreeSOLO needs credentials
          and a confirmed quote. No key is present in the browser.
        </span>
      </div>
      <ErrorBanner
        error={
          (profiles.error ??
            profile.error ??
            createProfile.error ??
            updateProfile.error ??
            generate.error ??
            importDataset.error ??
            approve.error ??
            review.error ??
            editExample.error ??
            createRun.error ??
            launch.error ??
            deploy.error ??
            importRun.error) as Error | null
        }
        retry={() => profiles.refetch()}
      />
      <div
        className="toolbar studio-kind"
        role="group"
        aria-label="Training profile"
      >
        <span>Training profile</span>
        <button
          className={
            trainingKind === "support_slm" ? "segment active" : "segment"
          }
          onClick={() => changeTrainingKind("support_slm")}
        >
          Support SLM · Qwen 4B
        </button>
        <button
          className={trainingKind === "router" ? "segment active" : "segment"}
          onClick={() => changeTrainingKind("router")}
        >
          Prompt router · Qwen 2B
        </button>
      </div>
      <ol className="wizard-steps">
        {[
          "Define",
          "Policies",
          "Generate",
          "Review",
          "Train",
          "Evaluate",
          "Deploy",
        ].map((label, index) => (
          <li
            key={label}
            className={
              index === step ? "active" : index < step ? "complete" : ""
            }
          >
            <button onClick={() => setStep(index)}>
              <span>{index + 1}</span>
              {label}
            </button>
          </li>
        ))}
      </ol>
      <div className="studio-layout">
        <aside className="panel studio-aside">
          <div className="panel-heading">
            <div>
              <h2>Profiles</h2>
              <p>
                {trainingKind === "support_slm"
                  ? "Content-versioned support domains"
                  : "Locked classification contract"}
              </p>
            </div>
          </div>
          {trainingKind === "router" ? (
            <div className="notice compact-notice">
              <Route />
              Router examples use the fixed complexity, task, risk, token, and
              capability target schema.
            </div>
          ) : profiles.isLoading ? (
            <LoadingTable />
          ) : profiles.data?.items.length ? (
            profiles.data?.items.map((item) => (
              <button
                className={`record-button ${profileId === item.id ? "selected" : ""}`}
                key={item.id}
                onClick={() => setProfileId(item.id)}
              >
                <span>
                  <strong>{item.name}</strong>
                  <small>{item.businessName}</small>
                </span>
                <em>v{item.contentVersion}</em>
              </button>
            ))
          ) : (
            <Empty
              icon={<Bot />}
              title="No support profiles"
              detail="Define the first reviewed business-policy domain."
              action={
                <button className="primary-button" onClick={() => setStep(0)}>
                  Define profile
                </button>
              }
            />
          )}
          <div className="panel-heading">
            <div>
              <h2>Datasets & runs</h2>
            </div>
          </div>
          <Field label="Dataset ID">
            <input
              value={datasetId}
              onChange={(event) => setDatasetId(event.target.value)}
              placeholder="Paste generated dataset ID"
            />
          </Field>
          <Field label="Training run">
            <select
              value={runId}
              onChange={(event) => {
                setRunId(event.target.value);
                const selectedRun = runs.data?.items.find(
                  (item) => item.id === event.target.value,
                );
                if (selectedRun?.kind === "router") setTrainingKind("router");
                if (selectedRun?.kind === "support_slm")
                  setTrainingKind("support_slm");
              }}
            >
              <option value="">Select run</option>
              {runs.data?.items.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.status} · {item.baseModel}
                </option>
              ))}
            </select>
          </Field>
        </aside>
        <section className="panel studio-main">
          {step === 0 && trainingKind === "router" ? (
            <div className="detail-pane">
              <h2>Define prompt-router target</h2>
              <p>
                The router is trained only on redacted request features and
                emits the fail-closed classification contract shown below. It
                never receives business answers or provider credentials.
              </p>
              <pre>
                {JSON.stringify(
                  {
                    complexity: "easy | medium | hard",
                    task_type:
                      "policy_qa | summarization | classification | extraction | reply_draft | code | math | legal | medical | financial | general",
                    risk: "low | medium | high",
                    predicted_output_tokens: "integer",
                    required_capabilities: ["text"],
                  },
                  null,
                  2,
                )}
              </pre>
              <button className="primary-button" onClick={() => setStep(1)}>
                Use locked router contract
              </button>
            </div>
          ) : null}
          {step === 0 && trainingKind === "support_slm" ? (
            <form className="create-form" onSubmit={submitProfile}>
              <div className="panel-heading">
                <div>
                  <h2>Define support domain</h2>
                  <p>
                    Public policy tasks only; mutations and high-risk topics
                    remain out of domain.
                  </p>
                </div>
              </div>
              <div className="form-grid">
                <Field label="Profile name">
                  <input
                    required
                    name="name"
                    defaultValue="Northstar Support"
                  />
                </Field>
                <Field label="Business name">
                  <input
                    required
                    name="businessName"
                    defaultValue="Northstar Outfitters"
                  />
                </Field>
                <Field label="Description">
                  <textarea
                    required
                    name="description"
                    defaultValue="Fictional e-commerce support policy assistant."
                  />
                </Field>
                <Field label="Language">
                  <select name="language">
                    <option value="en">English</option>
                  </select>
                </Field>
                <Field label="Tone">
                  <input name="tone" defaultValue="clear, calm, and concise" />
                </Field>
                <Field label="Forbidden topics">
                  <textarea
                    name="forbidden"
                    defaultValue={
                      "legal advice\npayments\naccount mutations\nmedical advice"
                    }
                  />
                </Field>
              </div>
              <fieldset className="check-field">
                <legend>Allowed tasks</legend>
                <div className="toggle-row">
                  {[
                    "policy_qa",
                    "summarization",
                    "classification",
                    "extraction",
                    "reply_draft",
                  ].map((task) => (
                    <label key={task}>
                      <input
                        defaultChecked
                        name="tasks"
                        type="checkbox"
                        value={task}
                      />
                      {task}
                    </label>
                  ))}
                </div>
              </fieldset>
              <div className="policy-document">
                <Field label="First policy key">
                  <input
                    required
                    name="policyKey"
                    defaultValue="returns-30-day"
                    pattern="[a-z0-9-]+"
                  />
                </Field>
                <Field label="Title">
                  <input
                    required
                    name="policyTitle"
                    defaultValue="Returns within 30 days"
                  />
                </Field>
                <Field label="Policy fact">
                  <textarea
                    required
                    name="policyContent"
                    defaultValue="Unused items may be returned within 30 days."
                  />
                </Field>
              </div>
              <button
                className="primary-button"
                disabled={createProfile.isPending}
              >
                Save profile and policy
              </button>
            </form>
          ) : null}
          {step === 1 && trainingKind === "router" ? (
            <div className="detail-pane">
              <h2>Router safety policy</h2>
              <p>
                Invalid output, confidence below policy, unknown labels, or a
                missing deployment all fail closed to deterministic routing.
                High-risk and tool requests remain frontier-only.
              </p>
              <button className="primary-button" onClick={() => setStep(2)}>
                Continue to examples
              </button>
            </div>
          ) : null}
          {step === 1 && trainingKind === "support_slm" ? (
            <div className="detail-pane">
              <h2>Versioned policies</h2>
              {selectedProfile ? (
                <form
                  key={`${selectedProfile.id}:${selectedProfile.contentVersion}`}
                  className="profile-editor"
                  onSubmit={submitProfileEdit}
                >
                  <div className="form-grid">
                    <Field label="Profile name">
                      <input
                        name="name"
                        required
                        defaultValue={selectedProfile.name}
                      />
                    </Field>
                    <Field label="Business name">
                      <input
                        name="businessName"
                        required
                        defaultValue={selectedProfile.businessName}
                      />
                    </Field>
                    <Field label="Description">
                      <textarea
                        name="description"
                        required
                        defaultValue={selectedProfile.description}
                      />
                    </Field>
                    <Field label="Language">
                      <select
                        name="language"
                        defaultValue={
                          selectedProfile.definition?.supported_languages?.[0] ??
                          "en"
                        }
                      >
                        <option value="en">English</option>
                      </select>
                    </Field>
                    <Field label="Tone">
                      <input
                        name="tone"
                        defaultValue={selectedProfile.definition?.tone ?? ""}
                      />
                    </Field>
                    <Field label="Forbidden topics">
                      <textarea
                        name="forbidden"
                        defaultValue={(
                          selectedProfile.definition?.forbidden_topics ?? []
                        ).join("\n")}
                      />
                    </Field>
                  </div>
                  <fieldset className="check-field">
                    <legend>Allowed tasks</legend>
                    <div className="toggle-row">
                      {[
                        "policy_qa",
                        "summarization",
                        "classification",
                        "extraction",
                        "reply_draft",
                      ].map((task) => (
                        <label key={task}>
                          <input
                            name="tasks"
                            type="checkbox"
                            value={task}
                            defaultChecked={selectedProfile.definition?.allowed_tasks?.includes(
                              task,
                            )}
                          />
                          {task}
                        </label>
                      ))}
                    </div>
                  </fieldset>
                  <div className="policy-editor-list">
                    {profile.isLoading ? <LoadingTable /> : null}
                    {(profile.data?.policyDocuments ?? []).map(
                      (document: Item, index: number) => (
                        <fieldset key={document.id} className="policy-document">
                          <legend>
                            {document.policyKey} · v{document.version}
                          </legend>
                          <Field label="Policy key">
                            <input
                              name={`policyKey-${index}`}
                              defaultValue={document.policyKey}
                              readOnly
                            />
                          </Field>
                          <Field label="Title">
                            <input
                              name={`policyTitle-${index}`}
                              defaultValue={document.title}
                              required
                            />
                          </Field>
                          <Field label="Policy content">
                            <textarea
                              name={`policyContent-${index}`}
                              defaultValue={document.content}
                              required
                            />
                          </Field>
                        </fieldset>
                      ),
                    )}
                    <fieldset className="policy-document">
                      <legend>Add policy document</legend>
                      <Field label="Policy key">
                        <input name="newPolicyKey" pattern="[a-z0-9-]+" />
                      </Field>
                      <Field label="Title">
                        <input name="newPolicyTitle" />
                      </Field>
                      <Field label="Policy content">
                        <textarea name="newPolicyContent" />
                      </Field>
                    </fieldset>
                  </div>
                  <p>
                    Changed policy content creates a new content version and
                    invalidates its semantic namespace.
                  </p>
                  <div className="editor-actions">
                    <button
                      className="secondary-button"
                      disabled={updateProfile.isPending || profile.isLoading}
                    >
                      Save content version
                    </button>
                    <button
                      type="button"
                      className="primary-button"
                      onClick={() => setStep(2)}
                    >
                      Policies validated
                    </button>
                  </div>
                </form>
              ) : (
                <Empty
                  icon={<Bot />}
                  title="Select a profile"
                  detail="Choose a profile before editing its policies."
                />
              )}
            </div>
          ) : null}
          {step === 2 ? (
            <div className="detail-pane">
              <h2>Generate candidate examples</h2>
              <p>
                {trainingKind === "support_slm"
                  ? "Gemini runs offline in batches of at most 50. Every record is normalized, embedded locally, deduplicated, scanned, balanced, and left unapproved."
                  : "Router datasets use the same local validation, deduplication, grouped split, review, approval, and immutable manifest pipeline."}
              </p>
              <div className="metric-grid compact-metrics">
                <div className="metric">
                  <span>Target</span>
                  <strong>150</strong>
                  <small>demo bounded</small>
                </div>
                <div className="metric">
                  <span>Splits</span>
                  <strong>70/15/15</strong>
                  <small>by paraphrase group</small>
                </div>
                <div className="metric">
                  <span>Model</span>
                  <strong>
                    {trainingKind === "router" ? "Router JSON" : "Gemini"}
                  </strong>
                  <small>structured targets</small>
                </div>
              </div>
              {trainingKind === "support_slm" ? (
                <button
                  className="primary-button"
                  disabled={!profileId || generate.isPending}
                  onClick={() => generate.mutate()}
                >
                  <Play /> Generate dataset
                </button>
              ) : null}
              {generate.data && !generate.data.geminiConfigured ? (
                <div className="error-banner">
                  <AlertTriangle />
                  Gemini is not configured. Add the key later, then generate a
                  new version.
                </div>
              ) : null}
              <div className="policy-document">
                <h3>Import reviewed examples without Gemini</h3>
                <p>
                  This local path is available now. Inputs receive the same
                  schema, secret/PII, duplicate, embedding, split, and approval
                  checks as generated data.
                </p>
                <Field label="Canonical examples (JSON array)">
                  <textarea
                    rows={14}
                    value={manualExamples}
                    onChange={(event) => setManualExamples(event.target.value)}
                  />
                </Field>
                <button
                  className="secondary-button"
                  disabled={
                    (trainingKind === "support_slm" && !profileId) ||
                    importDataset.isPending
                  }
                  onClick={() => importDataset.mutate()}
                >
                  <Download /> Import for review
                </button>
              </div>
            </div>
          ) : null}
          {step === 3 ? (
            <div className="detail-pane">
              <div className="panel-heading">
                <div>
                  <h2>Review dataset</h2>
                  <p>
                    {dataset.data?.exampleCount ?? 0} examples · manifest{" "}
                    {dataset.data?.manifestSha256?.slice(0, 12) ?? "pending"}
                  </p>
                </div>
                {dataset.data ? (
                  <span className="status-pill">{dataset.data.status}</span>
                ) : null}
              </div>
              {dataset.data ? (
                <div className="dataset-summary">
                  <span>
                    Splits: {JSON.stringify(dataset.data.distribution ?? {})}
                  </span>
                  <span>
                    Review: {JSON.stringify(dataset.data.reviews ?? {})}
                  </span>
                  {dataset.data.generationConfig?.duplicateWarnings ? (
                    <span className="field-warning">
                      Duplicate warnings:{" "}
                      {dataset.data.generationConfig.duplicateWarnings}
                    </span>
                  ) : null}
                </div>
              ) : null}
              {examples.isLoading ? (
                <LoadingTable />
              ) : examples.data?.items.length ? (
                <div className="review-list">
                  {examples.data.items.map((item) => (
                    <article key={item.id}>
                      <div>
                        <strong>{item.externalId}</strong>
                        <span>
                          {item.split} · {item.metadata?.difficulty} ·{" "}
                          {item.metadata?.task_type}
                        </span>
                      </div>
                      <p>{item.input}</p>
                      <pre>{JSON.stringify(item.output)}</pre>
                      <details>
                        <summary>Edit reviewed record</summary>
                        <form
                          onSubmit={(event) => submitExample(event, item.id)}
                        >
                          <Field label="Input">
                            <textarea
                              name="input"
                              defaultValue={item.input}
                              required
                            />
                          </Field>
                          <Field label="Output JSON">
                            <textarea
                              name="output"
                              defaultValue={JSON.stringify(
                                item.output,
                                null,
                                2,
                              )}
                              required
                            />
                          </Field>
                          <Field label="Split">
                            <select name="split" defaultValue={item.split}>
                              <option>train</option>
                              <option>eval</option>
                              <option>test</option>
                            </select>
                          </Field>
                          <button disabled={editExample.isPending}>
                            Save edit
                          </button>
                        </form>
                      </details>
                      <div>
                        <button
                          onClick={() =>
                            review.mutate({ id: item.id, state: "approved" })
                          }
                        >
                          Approve
                        </button>
                        <button
                          onClick={() =>
                            review.mutate({ id: item.id, state: "rejected" })
                          }
                        >
                          Reject
                        </button>
                      </div>
                    </article>
                  ))}
                </div>
              ) : (
                <Empty
                  icon={<Database />}
                  title="No examples yet"
                  detail="The generation job may still be running. Refresh after the worker completes."
                  action={
                    <button
                      className="secondary-button"
                      onClick={() => {
                        void dataset.refetch();
                        void examples.refetch();
                      }}
                    >
                      <RefreshCw /> Refresh
                    </button>
                  }
                />
              )}
              <ConfirmAction
                label="Freeze and approve version"
                title="Approve immutable dataset"
                detail="The manifest will be recomputed and this dataset version will become read-only. Later edits create a new version."
                onConfirm={() => approve.mutate()}
                disabled={!examples.data?.items.length}
              />
            </div>
          ) : null}
          {step === 4 ? (
            <div className="detail-pane">
              <h2>Configure and quote FreeSOLO training</h2>
              <form className="form-grid" onSubmit={submitRun}>
                <Field label="Recipe">
                  <select name="algorithm">
                    <option>sft</option>
                  </select>
                </Field>
                <Field label="Base model">
                  <input
                    required
                    name="baseModel"
                    value={
                      trainingKind === "router"
                        ? "Qwen/Qwen3.5-2B"
                        : "Qwen/Qwen3.5-4B"
                    }
                    readOnly
                  />
                </Field>
                <button
                  className="primary-button"
                  disabled={!datasetId || createRun.isPending}
                >
                  Create run
                </button>
              </form>
              {run.data ? (
                <div className="run-card">
                  <strong>{run.data.status}</strong>
                  <span>
                    FreeSOLO configured: {String(run.data.freesoloConfigured)}
                  </span>
                  <pre>{run.data.renderedConfig}</pre>
                  {run.data.status === "approved" ? (
                    <button
                      className="secondary-button"
                      onClick={() => launch.mutate({ confirm: false })}
                    >
                      Validate & request quote
                    </button>
                  ) : null}
                  {run.data.quoteId ? (
                    <ConfirmAction
                      label={`Launch for $${run.data.costQuoteUsd}`}
                      title="Confirm training spend"
                      detail={`Quote ${run.data.quoteId} will be checked again by the worker before launch.`}
                      onConfirm={() =>
                        launch.mutate({
                          confirm: true,
                          quoteId: run.data.quoteId,
                        })
                      }
                    />
                  ) : null}
                </div>
              ) : null}
              <p>
                {trainingKind === "router" ? "GRPO" : "OPD"} remains available
                through the API after a completed SFT parent passes its gates;
                it cannot be launched without that parent run ID.
              </p>
            </div>
          ) : null}
          {step === 5 ? (
            <div className="detail-pane">
              <h2>Evaluation gates</h2>
              {run.data ? (
                <>
                  <div
                    className={
                      run.data.evaluationGates?.passed
                        ? "gate-summary passed"
                        : "gate-summary failed"
                    }
                  >
                    <strong>
                      {run.data.evaluationGates?.passed
                        ? "All deployment gates passed"
                        : "Deployment gates are not satisfied"}
                    </strong>
                  </div>
                  <div className="gate-grid">
                    {Object.entries(run.data.evaluationGates?.checks ?? {}).map(
                      ([name, passed]) => (
                        <div key={name}>
                          <span>{String(passed) === "true" ? "✓" : "×"}</span>
                          {name}
                        </div>
                      ),
                    )}
                  </div>
                  <pre>{JSON.stringify(run.data.evalMetrics, null, 2)}</pre>
                  {run.data.evalMetrics?.parentComparison ? (
                    <section>
                      <h3>Parent-run comparison</h3>
                      <pre>
                        {JSON.stringify(
                          run.data.evalMetrics.parentComparison,
                          null,
                          2,
                        )}
                      </pre>
                    </section>
                  ) : null}
                  {run.data.evalMetrics?.failedExamples?.length ? (
                    <section>
                      <h3>Failed examples</h3>
                      <pre>
                        {JSON.stringify(
                          run.data.evalMetrics.failedExamples,
                          null,
                          2,
                        )}
                      </pre>
                    </section>
                  ) : null}
                  <button
                    className="primary-button"
                    disabled={!run.data.evaluationGates?.passed}
                    onClick={() => setStep(6)}
                  >
                    Continue to deploy
                  </button>
                </>
              ) : (
                <Empty
                  icon={<Bot />}
                  title="Select a training run"
                  detail="Evaluation metrics appear after a completed or imported FreeSOLO run."
                />
              )}
            </div>
          ) : null}
          {step === 6 ? (
            <div className="detail-pane">
              <h2>Deploy or import</h2>
              {run.data ? (
                <>
                  <p>
                    Deploy creates an explicit physical endpoint identity from
                    the FreeSOLO deployment result. Failed gates require an
                    experimental import and an opt-in routing policy.
                  </p>
                  <ConfirmAction
                    label="Deploy eligible run"
                    title="Deploy model"
                    detail="FreeSOLO will run a deployment dry-run first. The returned base URL and model ID are registered without exposing credentials."
                    onConfirm={() => deploy.mutate(false)}
                    disabled={
                      run.data.status !== "completed" ||
                      !run.data.evaluationGates?.passed
                    }
                  />
                  <dl className="details">
                    <dt>Status</dt>
                    <dd>{run.data.status}</dd>
                    <dt>Deployment URL</dt>
                    <dd>{run.data.deploymentBaseUrl ?? "—"}</dd>
                    <dt>Model ID</dt>
                    <dd>{run.data.deployedModelId ?? "—"}</dd>
                  </dl>
                </>
              ) : (
                <Empty
                  icon={<Bot />}
                  title="Select a run"
                  detail="Completed seeded run IDs can also be imported through the API."
                />
              )}
              <form className="form-grid" onSubmit={submitImportedRun}>
                <h3>Import a completed FreeSOLO run later</h3>
                <Field label="FreeSOLO run ID">
                  <input name="freesoloRunId" required />
                </Field>
                <Field label="Status">
                  <select name="status">
                    <option>completed</option>
                    <option>deployed</option>
                    <option>experimental</option>
                  </select>
                </Field>
                <Field label="Evaluation metrics (JSON)">
                  <textarea name="evalMetrics" defaultValue="{}" required />
                </Field>
                <Field label="Deployment base URL (deployed only)">
                  <input type="url" name="deploymentBaseUrl" />
                </Field>
                <Field label="Deployed model ID (deployed only)">
                  <input name="deployedModelId" />
                </Field>
                <button
                  className="secondary-button"
                  disabled={!datasetId || importRun.isPending}
                >
                  Import completed run
                </button>
              </form>
            </div>
          ) : null}
        </section>
      </div>
    </div>
  );
}

function CacheView() {
  const client = useQueryClient();
  const stats = useQuery({
    queryKey: ["cache-stats"],
    queryFn: () => api<Item>("/cache/stats"),
  });
  const entries = useQuery({
    queryKey: ["cache-entries"],
    queryFn: () => api<List>("/cache/entries"),
  });
  const logicalModels = useQuery({
    queryKey: ["logical-models"],
    queryFn: () => api<List>("/logical-models"),
  });
  const slmProfiles = useQuery({
    queryKey: ["slm-profiles"],
    queryFn: () => api<List>("/slm-profiles"),
  });
  const [bulkScope, setBulkScope] = useState<
    "all" | "logical_model" | "slm_profile"
  >("all");
  const [bulkTarget, setBulkTarget] = useState("");
  const [preview, setPreview] = useState<Item>();
  const invalidate = useMutation({
    mutationFn: (id: string) =>
      api(`/cache/entries/${id}/invalidate`, { method: "POST", body: "{}" }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["cache-stats"] });
      void client.invalidateQueries({ queryKey: ["cache-entries"] });
    },
  });
  const bulk = useMutation({
    mutationFn: (scope: Item) =>
      api<Item>("/cache/invalidate", {
        method: "POST",
        body: JSON.stringify(scope),
      }),
    onSuccess: () => {
      setPreview(undefined);
      void client.invalidateQueries({ queryKey: ["cache-stats"] });
      void client.invalidateQueries({ queryKey: ["cache-entries"] });
    },
  });
  const scopeBody = useMemo(
    () => ({
      scope: bulkScope,
      ...(bulkScope === "logical_model"
        ? { logicalModelId: bulkTarget }
        : bulkScope === "slm_profile"
          ? { slmProfileId: bulkTarget }
          : {}),
    }),
    [bulkScope, bulkTarget],
  );
  const previewBulk = useMutation({
    mutationFn: () =>
      api<Item>("/cache/invalidate", {
        method: "POST",
        body: JSON.stringify({ ...scopeBody, confirm: false }),
      }),
    onSuccess: (value) => setPreview(value.preview),
  });
  return (
    <div className="page">
      <Header
        eyebrow="CACHE / CONSERVATIVE REUSE"
        title="Semantic Cache"
        description="Exact and semantic entries, current capacity policy, savings, and audited invalidation."
        icon={<Database />}
      />
      <ErrorBanner
        error={
          (stats.error ??
            entries.error ??
            logicalModels.error ??
            slmProfiles.error ??
            invalidate.error ??
            previewBulk.error ??
            bulk.error) as Error | null
        }
        retry={() => {
          void stats.refetch();
          void entries.refetch();
        }}
      />
      <div className="metric-grid">
        <div className="metric">
          <span>Entries</span>
          <strong>{stats.data?.entries ?? "—"}</strong>
          <small>active, non-expired</small>
        </div>
        <div className="metric">
          <span>Exact hits</span>
          <strong>{stats.data?.exactHits ?? "—"}</strong>
          <small>full fingerprint match</small>
        </div>
        <div className="metric">
          <span>Semantic hits</span>
          <strong>{stats.data?.semanticHits ?? "—"}</strong>
          <small>threshold + margin passed</small>
        </div>
        <div className="metric">
          <span>Hit rate</span>
          <strong>
            {stats.data?.hitRate
              ? `${(stats.data.hitRate * 100).toFixed(1)}%`
              : "—"}
          </strong>
          <small>selected window</small>
        </div>
        <div className="metric">
          <span>Estimated savings</span>
          <strong>{stats.data?.estimatedSavingsGrams ?? 0} g</strong>
          <small>operational carbon</small>
        </div>
        <div className="metric">
          <span>Capacity target</span>
          <strong>{stats.data?.capacityTargetPct ?? 75}%</strong>
          <small>{stats.data?.gridPolicy ?? "dynamic TTL"}</small>
        </div>
        <div className="metric">
          <span>Grid state</span>
          <strong>{stats.data?.gridState ?? "—"}</strong>
          <small>{stats.data?.gridIntensityGco2Kwh ?? "—"} gCO₂e/kWh</small>
        </div>
      </div>
      <div className="filter-bar" aria-label="Bulk cache invalidation">
        <Field label="Invalidation scope">
          <select
            value={bulkScope}
            onChange={(event) => {
              setBulkScope(
                event.target.value as
                  | "all"
                  | "logical_model"
                  | "slm_profile",
              );
              setBulkTarget("");
              setPreview(undefined);
            }}
          >
            <option value="all">All entries</option>
            <option value="logical_model">Logical model</option>
            <option value="slm_profile">SLM profile</option>
          </select>
        </Field>
        {bulkScope === "logical_model" ? (
          <Field label="Logical model">
            <select
              value={bulkTarget}
              onChange={(event) => {
                setBulkTarget(event.target.value);
                setPreview(undefined);
              }}
            >
              <option value="">Select model</option>
              {logicalModels.data?.items.map((item) => (
                <option value={item.id} key={item.id}>
                  {item.alias}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
        {bulkScope === "slm_profile" ? (
          <Field label="SLM profile">
            <select
              value={bulkTarget}
              onChange={(event) => {
                setBulkTarget(event.target.value);
                setPreview(undefined);
              }}
            >
              <option value="">Select profile</option>
              {slmProfiles.data?.items.map((item) => (
                <option value={item.id} key={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
        <button
          className="secondary-button"
          disabled={
            previewBulk.isPending || (bulkScope !== "all" && !bulkTarget)
          }
          onClick={() => previewBulk.mutate()}
        >
          Preview impact
        </button>
        {preview ? (
          <div className="invalidation-preview">
            <span>
              <strong>{preview.expectedCount}</strong> entries match this scope.
            </span>
            <ConfirmAction
              danger
              label="Confirm invalidation"
              title={`Invalidate ${preview.expectedCount} cache entries?`}
              detail="The preview count is checked transactionally. If the cache changes, you will be asked to preview again. Audit rows are retained."
              onConfirm={() =>
                bulk.mutate({
                  ...scopeBody,
                  confirm: true,
                  expectedCount: preview.expectedCount,
                })
              }
            />
          </div>
        ) : null}
      </div>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>Active and invalidated entries</h2>
            <p>Only redacted previews and context fingerprints are shown.</p>
          </div>
          <span className="stale-time">Preview required for bulk changes</span>
        </div>
        {entries.isLoading ? (
          <LoadingTable />
        ) : entries.data?.items.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Fingerprint</th>
                  <th>Redacted preview</th>
                  <th>Hits</th>
                  <th>Expires</th>
                  <th>Status</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {entries.data.items.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <code>{item.fingerprint.slice(0, 16)}</code>
                    </td>
                    <td>{item.redactedPreview}</td>
                    <td>{item.hitCount}</td>
                    <td>{formatUtc(item.expiresAt)}</td>
                    <td>{item.invalidatedAt ? "Invalidated" : "Active"}</td>
                    <td>
                      <ConfirmAction
                        danger
                        label="Invalidate"
                        title="Invalidate cache entry"
                        detail="The entry will no longer be eligible for exact or semantic hits."
                        onConfirm={() => invalidate.mutate(item.id)}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            icon={<Database />}
            title="No reusable answers"
            detail="Eligible deterministic public-policy responses will appear after successful requests."
          />
        )}
      </section>
      <aside className="method-note">
        <AlertTriangle />
        <span>
          Semantic lookup requires matching context hashes, language, response
          format, a top score ≥ policy threshold, and a 0.02 margin over the
          second result.
        </span>
      </aside>
    </div>
  );
}

function NodesView() {
  const client = useQueryClient();
  const agents = useQuery({
    queryKey: ["agents"],
    queryFn: () => api<List>("/agents"),
    refetchInterval: 5000,
  });
  const endpoints = useQuery({
    queryKey: ["model-endpoints"],
    queryFn: () => api<List>("/model-endpoints"),
  });
  const benchmarks = useQuery({
    queryKey: ["benchmarks"],
    queryFn: () => api<List>("/benchmarks"),
    refetchInterval: 5000,
  });
  const [agentId, setAgentId] = useState("");
  const [confirmPowerLimit, setConfirmPowerLimit] = useState(false);
  const [confirmExperimental, setConfirmExperimental] = useState(false);
  const detail = useQuery({
    queryKey: ["agent", agentId],
    queryFn: () => api<Item>(`/agents/${agentId}`),
    enabled: !!agentId,
    refetchInterval: 3000,
  });
  const profile = useMutation({
    mutationFn: (value: string) =>
      api(`/agents/${agentId}/desired-profile`, {
        method: "POST",
        body: JSON.stringify({
          profile: value,
          confirmPowerLimit,
          confirmExperimental,
        }),
      }),
    onSuccess: () => {
      setConfirmPowerLimit(false);
      setConfirmExperimental(false);
      void detail.refetch();
      void client.invalidateQueries({ queryKey: ["agents"] });
    },
  });
  const benchmark = useMutation({
    mutationFn: (endpointId: string) =>
      api<Item>("/benchmarks", {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey("benchmark") },
        body: JSON.stringify({
          agentId,
          endpointId,
          profile: "eco",
          promptIds: ["returns", "shipping", "exchange", "delay"],
          phaseSeconds: 30,
          warmupSeconds: 5,
          cooldownSeconds: 5,
          concurrency: 2,
        }),
      }),
    onSuccess: () =>
      void client.invalidateQueries({ queryKey: ["benchmarks"] }),
  });
  const selected = agents.data?.items.find((item) => item.id === agentId);
  const latest = detail.data?.telemetry?.at(-1);
  return (
    <div className="page">
      <Header
        eyebrow="NODES / TRANSACTIONAL CONTROLS"
        title="Self-Hosted Nodes"
        description="Capabilities, measured or simulated telemetry, reversible profiles, and reproducible benchmarks."
        icon={<Network />}
      />
      <ErrorBanner
        error={
          (agents.error ??
            detail.error ??
            profile.error ??
            benchmark.error) as Error | null
        }
        retry={() => agents.refetch()}
      />
      <div className="split-layout nodes">
        <section className="panel list-panel">
          <div className="panel-heading">
            <div>
              <h2>Node agents</h2>
              <p>Offline after 15 seconds</p>
            </div>
          </div>
          {agents.isLoading ? (
            <LoadingTable />
          ) : agents.data?.items.length ? (
            agents.data?.items.map((item) => (
              <button
                key={item.id}
                className={`record-button ${agentId === item.id ? "selected" : ""}`}
                onClick={() => {
                  setAgentId(item.id);
                  setConfirmPowerLimit(false);
                  setConfirmExperimental(false);
                }}
              >
                <span>
                  <strong>{item.hostname}</strong>
                  <small>
                    {item.platform} · {item.activeProfile}
                  </small>
                </span>
                <em>{item.status}</em>
              </button>
            ))
          ) : (
            <Empty
              icon={<Network />}
              title="No connected nodes"
              detail="Start the simulator or register a Linux node agent to inspect controls and telemetry."
            />
          )}
        </section>
        <section className="panel node-detail">
          {detail.data && selected ? (
            <>
              {selected.evidence === "simulated" ? (
                <div className="simulated-banner">
                  <AlertTriangle /> Simulated host · values are not measurements
                </div>
              ) : null}
              <div className="panel-heading">
                <div>
                  <h2>{detail.data.hostname}</h2>
                  <p>
                    {detail.data.platform} {detail.data.kernelVersion} · agent{" "}
                    {detail.data.agentVersion}
                  </p>
                </div>
                {evidence(detail.data.evidence)}
              </div>
              <div
                className="profile-control"
                role="group"
                aria-label="Optimization profile"
              >
                {["off", "observe", "balanced", "eco"].map((value) =>
                  value === detail.data.desiredProfile ? (
                    <button key={value} className="active">
                      {value}
                    </button>
                  ) : (
                    <ConfirmAction
                      key={value}
                      label={value}
                      title={`Set ${value} profile`}
                      detail="The agent snapshots participating controls, applies them in risk order, verifies every value, and rolls back on failure. Power and experimental controls are separately confirmed."
                      onConfirm={() => profile.mutate(value)}
                    />
                  ),
                )}
              </div>
              {detail.data.approvedControls?.includes("nvml_power_limit") ||
              detail.data.approvedControls?.some((control: string) =>
                ["sched_ext", "napi_netdev_genl"].includes(control),
              ) ? (
                <fieldset className="check-field">
                  <legend>High-risk control confirmations</legend>
                  <div className="toggle-row">
                    {detail.data.approvedControls.includes(
                      "nvml_power_limit",
                    ) ? (
                      <label>
                        <input
                          type="checkbox"
                          checked={confirmPowerLimit}
                          onChange={(event) =>
                            setConfirmPowerLimit(event.target.checked)
                          }
                        />{" "}
                        Confirm GPU power-limit change for the next profile
                        request
                      </label>
                    ) : null}
                    {detail.data.approvedControls.some((control: string) =>
                      ["sched_ext", "napi_netdev_genl"].includes(control),
                    ) ? (
                      <label>
                        <input
                          type="checkbox"
                          checked={confirmExperimental}
                          onChange={(event) =>
                            setConfirmExperimental(event.target.checked)
                          }
                        />{" "}
                        Confirm experimental sched_ext / NAPI controls for the
                        next eco request
                      </label>
                    ) : null}
                  </div>
                  <small>
                    Confirmations are one-shot and reset after a successful
                    desired-state request or when changing nodes.
                  </small>
                </fieldset>
              ) : null}
              <div className="telemetry-grid">
                {[
                  ["CPU", latest?.cpu_percent ?? latest?.cpuPercent, "%"],
                  [
                    "Memory",
                    latest?.memory_percent ?? latest?.memoryPercent,
                    "%",
                  ],
                  ["GPU power", latest?.gpu?.[0]?.power_watts, " W"],
                  ["GPU temperature", latest?.gpu?.[0]?.temperature_c, " °C"],
                  [
                    "Network RX",
                    latest?.network_rx_bytes ?? latest?.networkRxBytes,
                    " B",
                  ],
                  ["Energy", latest?.gpu?.[0]?.total_energy_mj, " mJ"],
                ].map(([label, value, unit]) => (
                  <div key={String(label)}>
                    <span>{label}</span>
                    <strong>
                      {value ?? "—"}
                      {value !== undefined ? unit : ""}
                    </strong>
                  </div>
                ))}
              </div>
              <section className="capability-matrix">
                <h3>Capability matrix</h3>
                {Object.entries(detail.data.capabilities ?? {}).map(
                  ([name, enabled]) => (
                    <div key={name}>
                      <span>{enabled ? "✓" : "—"}</span>
                      <strong>{name}</strong>
                      <small>
                        {enabled ? "Detected" : "Unavailable; control disabled"}
                      </small>
                    </div>
                  ),
                )}
              </section>
              <section className="timeline">
                <h3>Apply / verify / rollback events</h3>
                {detail.data.events?.map((event: Item) => (
                  <div key={event.id}>
                    <span className={`timeline-dot ${event.status}`} />
                    <strong>
                      {event.control} · {event.action}
                    </strong>
                    <small>
                      {formatUtc(event.createdAt)} · {event.status}
                    </small>
                  </div>
                ))}
              </section>
              <section className="benchmark-panel">
                <h3>Benchmark</h3>
                <p>
                  Same prompts, endpoint, concurrency, output cap, and grid
                  fixture. The demo uses 30-second phases; simulator results
                  remain simulated.
                </p>
                <div className="inline-form">
                  <select id="benchmark-endpoint">
                    {endpoints.data?.items
                      .filter(
                        (item) =>
                          item.selfHosted &&
                          (selected.evidence === "simulated" ||
                            item.nodeAgentId === agentId),
                      )
                      .map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.name}
                        </option>
                      ))}
                  </select>
                  <button
                    className="primary-button"
                    onClick={() => {
                      const element = document.querySelector<HTMLSelectElement>(
                        "#benchmark-endpoint",
                      );
                      if (element?.value) benchmark.mutate(element.value);
                    }}
                  >
                    <Play /> Start benchmark
                  </button>
                </div>
                {benchmarks.data?.items
                  .filter((item) => item.agentId === agentId)
                  .map((item) => (
                    <article className="benchmark-result" key={item.id}>
                      <div>
                        <strong>{item.status}</strong>
                        {evidence(item.evidence)}
                      </div>
                      {item.comparison ? (
                        <dl>
                          <dt>Throughput</dt>
                          <dd>{item.comparison.throughputChangePct}%</dd>
                          <dt>p95 latency</dt>
                          <dd>{item.comparison.p95LatencyChangePct}%</dd>
                          <dt>Energy/request</dt>
                          <dd>
                            {item.comparison.energyPerRequestChangePct == null
                              ? "Unavailable"
                              : `${item.comparison.energyPerRequestChangePct}%`}
                          </dd>
                          <dt>Quality</dt>
                          <dd>{item.comparison.qualityChange}</dd>
                          {item.comparison.backgroundCpuChangePct != null ? (
                            <>
                              <dt>Background CPU</dt>
                              <dd>{item.comparison.backgroundCpuChangePct}%</dd>
                            </>
                          ) : null}
                          {item.comparison.optimizedBackgroundThrottledUsec !=
                          null ? (
                            <>
                              <dt>Kernel throttling</dt>
                              <dd>
                                {Math.round(
                                  item.comparison
                                    .optimizedBackgroundThrottledUsec / 1000,
                                )} ms
                              </dd>
                            </>
                          ) : null}
                        </dl>
                      ) : (
                        <p>Waiting for phases…</p>
                      )}
                    </article>
                  ))}
              </section>
            </>
          ) : (
            <Empty
              icon={<Network />}
              title="Select a node"
              detail="Inspect telemetry, controls, and benchmarks for a connected simulator or Linux agent."
            />
          )}
        </section>
      </div>
    </div>
  );
}

function AuditView() {
  const [search, setSearch] = useState("");
  const [cacheFilter, setCacheFilter] = useState("");
  const [riskFilter, setRiskFilter] = useState("");
  const [fallbackFilter, setFallbackFilter] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const requests = useQuery({
    queryKey: ["requests"],
    queryFn: () => api<List>("/requests?limit=200"),
    refetchInterval: 5000,
  });
  const detail = useQuery({
    queryKey: ["request", selectedId],
    queryFn: () => api<Item>(`/requests/${selectedId}`),
    enabled: !!selectedId,
  });
  const items = useMemo(
    () =>
      requests.data?.items.filter((item) => {
        if (
          !JSON.stringify(item).toLowerCase().includes(search.toLowerCase())
        )
          return false;
        if (cacheFilter && item.cache !== cacheFilter) return false;
        if (riskFilter && item.routerClassification?.risk !== riskFilter)
          return false;
        if (
          fallbackFilter &&
          String(Boolean(item.fallbackUsed)) !== fallbackFilter
        )
          return false;
        return true;
      }) ?? [],
    [cacheFilter, fallbackFilter, requests.data, riskFilter, search],
  );
  return (
    <div className="page">
      <Header
        eyebrow="AUDIT / REDACTED DECISIONS"
        title="Request Audit"
        description="Candidate exclusions, scores, attempts, fallback, and evidence without raw prompts."
        icon={<Search />}
      />
      <ErrorBanner
        error={(requests.error ?? detail.error) as Error | null}
        retry={() => requests.refetch()}
      />
      <div className="filter-bar">
        <Field label="Filter requests">
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="ID, route, risk, cache, session…"
          />
        </Field>
        <Field label="Cache result">
          <select
            value={cacheFilter}
            onChange={(event) => setCacheFilter(event.target.value)}
          >
            <option value="">All cache states</option>
            <option value="miss">miss</option>
            <option value="exact">exact</option>
            <option value="semantic">semantic</option>
            <option value="bypass">bypass</option>
          </select>
        </Field>
        <Field label="Risk">
          <select
            value={riskFilter}
            onChange={(event) => setRiskFilter(event.target.value)}
          >
            <option value="">All risk levels</option>
            <option value="low">low</option>
            <option value="medium">medium</option>
            <option value="high">high</option>
          </select>
        </Field>
        <Field label="Quality fallback">
          <select
            value={fallbackFilter}
            onChange={(event) => setFallbackFilter(event.target.value)}
          >
            <option value="">All requests</option>
            <option value="true">used</option>
            <option value="false">not used</option>
          </select>
        </Field>
        <span>
          {items.length} matching records · reconciles every 5 seconds
        </span>
      </div>
      <div className="audit-layout">
        <section className="panel audit-list">
          {requests.isLoading ? (
            <LoadingTable />
          ) : items.length ? (
            items.map((item) => (
              <button
                className={selectedId === item.id ? "selected" : ""}
                key={item.id}
                onClick={() => setSelectedId(item.id)}
              >
                <span
                  className={`status-icon ${item.status === "completed" ? "ok" : ""}`}
                >
                  <Route />
                </span>
                <span>
                  <strong>
                    {item.logicalModel} · {item.cache}
                  </strong>
                  <small>
                    {item.redactedPreview || "No stored prompt preview"}
                  </small>
                  <em>
                    {formatUtc(item.startedAt)} · {item.durationMs ?? "—"} ms
                  </em>
                </span>
                <span>
                  {item.routerClassification?.complexity ?? "—"}
                  <small>{item.routerClassification?.risk ?? "—"}</small>
                  <small>{item.route ?? item.endpoint ?? "unknown"}</small>
                  <em>
                    ${Number(item.costUsd ?? 0).toFixed(5)} ·{" "}
                    {item.carbonAccountingAvailable
                      ? `${Number(item.carbonGrams).toFixed(3)} g`
                      : "carbon unavailable"}
                  </em>
                  {evidence(item.evidence)}
                </span>
              </button>
            ))
          ) : (
            <Empty
              icon={<Search />}
              title="No requests"
              detail="Send a message from the Northstar support demo."
            />
          )}
        </section>
        <aside className={`panel audit-drawer ${selectedId ? "open" : ""}`}>
          {detail.data ? (
            <>
              <div className="panel-heading">
                <div>
                  <h2>Decision timeline</h2>
                  <p>
                    <code>{detail.data.id}</code>
                  </p>
                </div>
                <button
                  className="icon-button"
                  onClick={() => setSelectedId("")}
                  aria-label="Close audit detail"
                >
                  <X />
                </button>
              </div>
              <div className="audit-summary">
                <div>
                  <span>Status</span>
                  <strong>{detail.data.status}</strong>
                </div>
                <div>
                  <span>Cache</span>
                  <strong>{detail.data.cache}</strong>
                </div>
                <div>
                  <span>Fallback</span>
                  <strong>{String(detail.data.fallbackUsed)}</strong>
                </div>
                <div>
                  <span>Duration</span>
                  <strong>{detail.data.durationMs} ms</strong>
                </div>
              </div>
              <section>
                <h3>1 · Safety & router</h3>
                <pre>
                  {JSON.stringify(
                    {
                      features: detail.data.requestFeatures,
                      classification: detail.data.routerClassification,
                    },
                    null,
                    2,
                  )}
                </pre>
              </section>
              <section>
                <h3>2 · Candidate selection</h3>
                {detail.data.routeDecision?.candidates?.map(
                  (candidate: Item) => {
                    const carbon =
                      candidate.estimated_carbon_g ??
                      candidate.estimatedCarbonG;
                    return (
                    <div
                      className={`candidate ${(candidate.excluded_reason ?? candidate.excludedReason) ? "excluded" : ""}`}
                      key={candidate.endpoint_id ?? candidate.endpointId}
                    >
                      <strong>{candidate.name}</strong>
                      <span>
                        {candidate.excluded_reason ??
                          candidate.excludedReason ??
                          `score ${candidate.score}`}
                      </span>
                      <small>
                        {carbon == null ? "carbon unavailable" : `${carbon} g`} · $
                        {candidate.estimated_cost_usd ??
                          candidate.estimatedCostUsd}
                      </small>
                    </div>
                    );
                  },
                )}
              </section>
              <section>
                <h3>3 · Provider attempts</h3>
                {detail.data.attempts?.map((attempt: Item) => (
                  <div className="timeline-row" key={attempt.number}>
                    <strong>
                      Attempt {attempt.number} · {attempt.purpose}
                    </strong>
                    <span>
                      {attempt.status} · {attempt.durationMs} ms
                    </span>
                    <small>{attempt.qualityVerdict?.reason}</small>
                  </div>
                ))}
              </section>
              <section>
                <h3>4 · Impact attribution</h3>
                {detail.data.impact?.map((value: Item) => (
                  <dl className="details" key={value.strategy}>
                    <dt>Strategy</dt>
                    <dd>{value.strategy}</dd>
                    <dt>Baseline → actual carbon</dt>
                    <dd>
                      {value.carbonAccountingAvailable
                        ? `${value.baselineCarbonG} → ${value.actualCarbonG} g`
                        : "Unavailable — grid/location evidence was insufficient"}
                    </dd>
                    <dt>Energy</dt>
                    <dd>
                      {value.baselineEnergyKwh} → {value.actualEnergyKwh} kWh
                    </dd>
                    <dt>Evidence</dt>
                    <dd>{JSON.stringify(value.evidence)}</dd>
                  </dl>
                ))}
              </section>
            </>
          ) : (
            <Empty
              icon={<Search />}
              title="Choose a request"
              detail="The detail drawer reconstructs the redacted routing timeline."
            />
          )}
        </aside>
      </div>
    </div>
  );
}

function ReportsView() {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [evidenceFilter, setEvidenceFilter] = useState("");
  const [routeFilter, setRouteFilter] = useState("");
  const [logicalModelId, setLogicalModelId] = useState("");
  const [endpointId, setEndpointId] = useState("");
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<Error | null>(null);
  const logicalModels = useQuery({
    queryKey: ["logical-models"],
    queryFn: () => api<List>("/logical-models"),
  });
  const endpoints = useQuery({
    queryKey: ["model-endpoints"],
    queryFn: () => api<List>("/model-endpoints"),
  });
  const filters = useMemo(() => {
    const value: Item = {};
    if (from) value.from = `${from}:00Z`;
    if (to) value.to = `${to}:00Z`;
    if (evidenceFilter) value.evidence = evidenceFilter;
    if (routeFilter) value.route = routeFilter;
    if (logicalModelId) value.logicalModelId = logicalModelId;
    if (endpointId) value.endpointId = endpointId;
    return value;
  }, [endpointId, evidenceFilter, from, logicalModelId, routeFilter, to]);
  const queryString = useMemo(() => {
    const parameters = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) =>
      parameters.set(key, String(value)),
    );
    return parameters.toString();
  }, [filters]);
  const summary = useQuery({
    queryKey: ["report-summary", queryString],
    queryFn: () =>
      api<Item>(`/reports/summary${queryString ? `?${queryString}` : ""}`),
  });
  async function downloadImpact() {
    setExporting(true);
    setExportError(null);
    try {
      const response = await fetch("/api/control/reports/impact-framework", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(filters),
      });
      if (!response.ok) throw new Error("Impact export failed");
      const blob = await response.blob();
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = "ecoroute-impact.yml";
      link.click();
      URL.revokeObjectURL(link.href);
    } catch (error) {
      setExportError(
        error instanceof Error ? error : new Error("Impact export failed"),
      );
    } finally {
      setExporting(false);
    }
  }
  const baseline = Number(summary.data?.baselineCarbonGrams ?? 0);
  const actual = Number(summary.data?.actualCarbonGrams ?? 0);
  const carbonAvailable = Boolean(
    summary.data && summary.data.carbonOutcome !== "unavailable",
  );
  const evidenceCounts = summary.data?.evidenceCounts ?? {};
  return (
    <div className="page">
      <Header
        eyebrow="IMPACT / OPERATIONAL BOUNDARY"
        title="Impact Reports"
        description="Baseline comparisons, evidence coverage, cost trade-offs, and interoperable exports."
        icon={<FileChartColumn />}
      />
      <ErrorBanner
        error={
          (summary.error ??
            logicalModels.error ??
            endpoints.error ??
            exportError) as Error | null
        }
        retry={() => summary.refetch()}
      />
      <div className="filter-bar">
        <Field label="UTC start">
          <input
            type="datetime-local"
            value={from}
            onChange={(event) => setFrom(event.target.value)}
          />
        </Field>
        <Field label="UTC end">
          <input
            type="datetime-local"
            value={to}
            onChange={(event) => setTo(event.target.value)}
          />
        </Field>
        <Field label="Evidence">
          <select
            value={evidenceFilter}
            onChange={(event) => setEvidenceFilter(event.target.value)}
          >
            <option value="">All evidence</option>
            <option>measured</option>
            <option>estimated</option>
            <option>stale</option>
            <option>simulated</option>
          </select>
        </Field>
        <Field label="Logical model">
          <select
            value={logicalModelId}
            onChange={(event) => setLogicalModelId(event.target.value)}
          >
            <option value="">All logical models</option>
            {logicalModels.data?.items.map((item) => (
              <option value={item.id} key={item.id}>
                {item.alias}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Endpoint">
          <select
            value={endpointId}
            onChange={(event) => setEndpointId(event.target.value)}
          >
            <option value="">All endpoints</option>
            {endpoints.data?.items.map((item) => (
              <option value={item.id} key={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Route">
          <input
            value={routeFilter}
            onChange={(event) => setRouteFilter(event.target.value)}
            placeholder="endpoint name or cache"
          />
        </Field>
        <a
          className="secondary-button"
          href={`/api/control/reports/requests.csv${queryString ? `?${queryString}` : ""}`}
        >
          <Download /> Request CSV
        </a>
        <button
          className="primary-button"
          disabled={exporting}
          onClick={downloadImpact}
        >
          <Download /> Impact Framework YAML
        </button>
      </div>
      {summary.isLoading ? <LoadingTable /> : null}
      <div className="metric-grid">
        <div className="metric">
          <span>Baseline carbon</span>
          <strong>{carbonAvailable ? `${baseline.toFixed(3)} g` : "Unavailable"}</strong>
          <small>configured baseline endpoints</small>
        </div>
        <div className="metric">
          <span>Actual carbon</span>
          <strong>{carbonAvailable ? `${actual.toFixed(3)} g` : "Unavailable"}</strong>
          <small>operational attribution</small>
        </div>
        <div className="metric">
          <span>
            {summary.data?.carbonOutcome === "increase"
              ? "Carbon increase"
              : summary.data?.carbonOutcome === "unavailable"
                ? "Carbon unavailable"
              : "Avoided"}
          </span>
          <strong>
            {carbonAvailable
              ? `${Math.abs(Number(summary.data?.rawCarbonDeltaGrams ?? 0)).toFixed(3)} g`
              : "Unavailable"}
          </strong>
          <small>
            {summary.data?.carbonUnavailableRequests ?? 0} request(s) excluded
            for missing grid/location evidence
          </small>
        </div>
        <div className="metric">
          <span>Cost delta</span>
          <strong>${Number(summary.data?.costDeltaUsd ?? 0).toFixed(6)}</strong>
          <small>actual minus baseline</small>
        </div>
        <div className="metric">
          <span>Quality fallbacks</span>
          <strong>{summary.data?.qualityFallbacks ?? 0}</strong>
          <small>
            {summary.data?.successfulRequests ?? 0} successful requests
          </small>
        </div>
      </div>
      <div className="overview-grid">
        <section className="panel report-chart">
          <div className="panel-heading">
            <div>
              <h2>Baseline vs actual operational carbon</h2>
              <p>
                Selected UTC range · generated{" "}
                {formatUtc(summary.data?.generatedAt)}
              </p>
            </div>
          </div>
          <div className="comparison-bars">
            <div>
              <span>Baseline</span>
              <i style={{ width: carbonAvailable ? "100%" : "0%" }} />
              <strong>
                {carbonAvailable ? `${baseline.toFixed(3)} g` : "Unavailable"}
              </strong>
            </div>
            <div>
              <span>Actual</span>
              <i
                className="actual"
                style={{
                  width: `${carbonAvailable && baseline ? Math.min(100, (actual / baseline) * 100) : 0}%`,
                }}
              />
              <strong>
                {carbonAvailable ? `${actual.toFixed(3)} g` : "Unavailable"}
              </strong>
            </div>
          </div>
          <div className="evidence-summary">
            {Object.entries(evidenceCounts).map(([level, count]) => (
              <span key={level}>
                {evidence(level)} {String(count)}
              </span>
            ))}
          </div>
        </section>
        <section className="panel methodology">
          <div className="panel-heading">
            <div>
              <h2>Methodology & uncertainty</h2>
              <p>{summary.data?.methodologyVersion ?? "ecoroute-v2"}</p>
            </div>
          </div>
          <ul>
            <li>Operational energy only; embodied carbon is out of scope.</li>
            <li>
              Hosted inference uses configured coefficients and is labeled
              estimated.
            </li>
            <li>
              Self-hosted measurements use NVML/RAPL attribution where
              available.
            </li>
            <li>Simulated fixtures are excluded from measurement claims.</li>
            <li>
              Exports include generation time, filters, methodology, and
              evidence counts.
            </li>
          </ul>
        </section>
      </div>
    </div>
  );
}

function SettingsView() {
  const client = useQueryClient();
  const zones = useQuery({
    queryKey: ["carbon-zones"],
    queryFn: () => api<List & { provider?: string }>("/carbon/zones"),
  });
  const refresh = useMutation({
    mutationFn: () =>
      api("/carbon/refresh", {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey("carbon") },
        body: JSON.stringify({}),
      }),
    onSuccess: () =>
      void client.invalidateQueries({ queryKey: ["carbon-zones"] }),
  });
  return (
    <div className="page">
      <Header
        eyebrow="SETTINGS / INTEGRATION STATUS"
        title="Settings"
        description="Carbon evidence, optional adapters, security boundaries, and local runtime configuration."
        icon={<Settings />}
      />
      <ErrorBanner
        error={(zones.error ?? refresh.error) as Error | null}
        retry={() => zones.refetch()}
      />
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>Carbon zones</h2>
            <p>
              Background refresh every two minutes; request path uses a
              five-minute cache. Provider: {zones.data?.provider ?? "unknown"}.
            </p>
          </div>
          <button className="secondary-button" onClick={() => refresh.mutate()}>
            <RefreshCw /> Refresh
          </button>
        </div>
        {zones.isLoading ? (
          <LoadingTable />
        ) : zones.data?.items.length ? (
          <div className="settings-grid">
            {zones.data.items.map((item) => (
              <article key={`${item.zone}|${item.lookupKey ?? "zone"}`}>
                <div>
                  <strong>{item.zone}</strong>
                  {evidence(item.evidence)}
                </div>
                <span>
                  {item.intensityGco2Kwh ?? item.intensity_gco2_kwh} gCO₂e/kWh
                </span>
                <small>
                  {item.lookupKey ?? "zone"} · {item.source} · observed{" "}
                  {formatUtc(item.observedAt ?? item.observed_at)} ·{" "}
                  {item.freshnessSeconds ?? 0}s old
                  {item.metadata?.is_estimated ? " · provider-estimated" : ""}
                </small>
              </article>
            ))}
          </div>
        ) : (
          <Empty
            icon={<Settings />}
            title="No carbon readings"
            detail="Refresh the configured zones to collect a timestamped reading."
            action={
              <button
                className="secondary-button"
                onClick={() => refresh.mutate()}
              >
                <RefreshCw /> Refresh zones
              </button>
            }
          />
        )}
      </section>
      <section className="panel settings-section">
        <div className="panel-heading">
          <div>
            <h2>Optional integrations</h2>
            <p>
              Blank credentials disable live adapters; the deterministic fake
              remains available.
            </p>
          </div>
        </div>
        <div className="integration-list">
          {[
            [
              "Gemini dataset generation",
              "GEMINI_API_KEY",
              "Configured in the worker only",
            ],
            [
              "FreeSOLO training",
              "FREESOLO_API_KEY",
              "CLI subprocess allowlist; FREESOLO_ORG is optional legacy metadata",
            ],
            [
              "FreeSOLO router",
              "FREESOLO_ROUTER_BASE_URL + MODEL_ID",
              "Fails closed to deterministic routing",
            ],
            [
              "OpenAI / Gemini providers",
              "env: credential references",
              "Resolved server-side through LiteLLM",
            ],
            [
              "Electricity data",
              "ELECTRICITY_MAPS_API_KEY or CARBON_AWARE_BASE_URL",
              "Electricity Maps v4 is preferred in auto mode; timestamped and stale-safe",
            ],
            [
              "Hugging Face export",
              "HF_TOKEN + repository",
              "Explicit export action only",
            ],
          ].map(([name, variable, detail]) => (
            <div key={name}>
              <span className="status-dot" />
              <strong>{name}</strong>
              <code>{variable}</code>
              <small>{detail}</small>
            </div>
          ))}
        </div>
      </section>
      <aside className="method-note">
        <ShieldAlert />
        <span>
          Gateway and node-agent tokens are separate. Provider secrets never
          enter normal database configuration, events, browser bundles, or
          exports.
        </span>
      </aside>
    </div>
  );
}

const metadata = {
  "routing-policies": [
    "Routing Policies",
    "Immutable policy versions, thresholds, cost protections, and scoring weights.",
    <Route key="route" />,
  ],
  "model-endpoints": [
    "Model Endpoints",
    "Explicitly approved providers, regions, capabilities, prices, and evidence.",
    <Boxes key="boxes" />,
  ],
  "slm-studio": [
    "SLM Studio",
    "Gemini dataset preparation and FreeSOLO lifecycle controls.",
    <Bot key="bot" />,
  ],
  "semantic-cache": [
    "Semantic Cache",
    "Conservative reuse boundaries and estimated avoided work.",
    <Database key="database" />,
  ],
  "self-hosted-nodes": [
    "Self-Hosted Nodes",
    "Measured or simulated telemetry and reversible optimization.",
    <Network key="network" />,
  ],
  "request-audit": [
    "Request Audit",
    "Redacted routing and impact evidence.",
    <Search key="search" />,
  ],
  "impact-reports": [
    "Impact Reports",
    "Operational energy, carbon, cost, and uncertainty.",
    <FileChartColumn key="report" />,
  ],
  settings: [
    "Settings",
    "Runtime and integration state.",
    <Settings key="settings" />,
  ],
} as const;

export function SectionView({ section }: { section: string }) {
  if (section === "routing-policies") return <PoliciesView />;
  if (section === "model-endpoints") return <EndpointsView />;
  if (section === "slm-studio") return <SlmStudioView />;
  if (section === "semantic-cache") return <CacheView />;
  if (section === "self-hosted-nodes") return <NodesView />;
  if (section === "request-audit") return <AuditView />;
  if (section === "impact-reports") return <ReportsView />;
  if (section === "settings") return <SettingsView />;
  const value = metadata.settings;
  return (
    <div className="page">
      <Header
        eyebrow="CONTROL CENTER"
        title={value[0]}
        description={value[1]}
        icon={value[2]}
      />
    </div>
  );
}
