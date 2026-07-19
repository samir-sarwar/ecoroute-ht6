import { NextRequest } from "next/server";

const gateway = process.env.ECOROUTE_GATEWAY_INTERNAL_URL ?? "http://localhost:8000";
const key = process.env.ECOROUTE_SUPPORT_DEMO_GATEWAY_KEY ?? "ecoroute-demo-key";

async function proxy(path: string, init?: RequestInit) {
  const upstream = await fetch(`${gateway}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${key}`,
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  const payload = await upstream.json().catch(() => ({
    message: "Grid override is unavailable.",
  }));
  return Response.json(payload, { status: upstream.status });
}

export async function GET() {
  return proxy("/api/v1/demo/grid-override");
}

export async function POST(request: NextRequest) {
  const raw = await request.text();
  if (raw.length > 2_000) {
    return Response.json({ message: "Grid override request is too large." }, { status: 413 });
  }
  let body: { enabled?: unknown };
  try {
    body = raw ? JSON.parse(raw) : {};
  } catch {
    return Response.json({ message: "Choose a valid grid override." }, { status: 400 });
  }
  return proxy("/api/v1/demo/grid-override", {
    method: "POST",
    body: JSON.stringify({
      enabled: Boolean(body.enabled),
      scenario: "dirty",
    }),
  });
}
