from prometheus_client import Counter, Gauge, Histogram

REQUESTS = Counter(
    "ecoroute_requests_total", "Gateway requests", ["logical_model", "route", "cache", "result"]
)
REQUEST_DURATION = Histogram(
    "ecoroute_request_duration_seconds", "Gateway request duration", ["logical_model", "route"]
)
TIME_TO_FIRST_TOKEN = Histogram(
    "ecoroute_time_to_first_token_seconds",
    "Gateway time to first model token",
    ["logical_model", "route"],
)
TOKENS = Counter("ecoroute_tokens_total", "Model tokens", ["direction", "endpoint"])
COST = Counter("ecoroute_cost_usd_total", "Model cost", ["endpoint"])
ENERGY = Counter("ecoroute_energy_kwh_total", "Attributed energy", ["endpoint", "evidence"])
CARBON = Counter(
    "ecoroute_operational_carbon_grams_total", "Operational carbon", ["endpoint", "evidence"]
)
AVOIDED_CARBON = Counter(
    "ecoroute_avoided_carbon_grams_total", "Avoided carbon", ["strategy", "evidence"]
)
ROUTER_DURATION = Histogram("ecoroute_router_duration_seconds", "Router duration")
ROUTER_CLASSIFICATIONS = Counter(
    "ecoroute_router_classifications_total",
    "Router classifications by execution source and outcome",
    ["source", "complexity", "risk", "rationale"],
)
QUALITY_FALLBACKS = Counter("ecoroute_quality_fallbacks_total", "Quality fallbacks", ["reason"])
CACHE_HITS = Counter("ecoroute_cache_hits_total", "Cache hits", ["kind"])
GRID_INTENSITY = Gauge(
    "ecoroute_grid_intensity_gco2_kwh", "Grid intensity", ["zone", "source", "evidence"]
)
AGENT_POWER = Gauge("ecoroute_agent_power_watts", "Agent power", ["agent_id", "device", "evidence"])
AGENT_OPTIMIZATION = Gauge(
    "ecoroute_agent_optimization_active", "Agent optimization profile", ["agent_id", "profile"]
)
