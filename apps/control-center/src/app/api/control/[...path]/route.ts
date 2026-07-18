import type { NextRequest } from "next/server";

const gateway = process.env.ECOROUTE_GATEWAY_INTERNAL_URL ?? "http://localhost:8000";
const key = process.env.ECOROUTE_GATEWAY_KEY ?? "ecoroute-demo-key";

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const target = new URL(`/api/v1/${path.join("/")}`, gateway);
  request.nextUrl.searchParams.forEach((value, name) => target.searchParams.set(name, value));
  const headers = new Headers({ Authorization: `Bearer ${key}` });
  const idempotency = request.headers.get("Idempotency-Key");
  const lastEventId = request.headers.get("Last-Event-ID");
  if (idempotency) headers.set("Idempotency-Key", idempotency);
  if (lastEventId) headers.set("Last-Event-ID", lastEventId);
  if (request.method !== "GET" && request.method !== "HEAD") headers.set("Content-Type", "application/json");
  const response = await fetch(target, {
    method: request.method,
    headers,
    body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.text(),
    cache: "no-store",
  });
  const outgoing = new Headers();
  outgoing.set("Content-Type", response.headers.get("Content-Type") ?? "application/json");
  const requestId = response.headers.get("X-Request-Id");
  if (requestId) outgoing.set("X-Request-Id", requestId);
  return new Response(response.body, { status: response.status, headers: outgoing });
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const DELETE = proxy;

