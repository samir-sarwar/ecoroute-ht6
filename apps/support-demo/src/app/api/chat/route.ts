import { NextRequest } from "next/server";

const gateway = process.env.ECOROUTE_GATEWAY_INTERNAL_URL ?? "http://localhost:8000";
const key = process.env.ECOROUTE_SUPPORT_DEMO_GATEWAY_KEY ?? "ecoroute-demo-key";
const systemPrompt = `You are the Northstar Outfitters policy assistant.
Use only the fictional policy facts below. Return exactly one JSON object with answer,
confidence, policy_ids, and needs_human. Never claim that you performed a customer record change.

exchange-stock: Exchanges depend on current inventory.
final-sale: Final-sale items cannot be returned except when defective.
refund-timing: Approved refunds may take 5-10 business days to appear.
returns-30-day: Unused items may be returned within 30 days.
shipping-delay: Escalate after 7 business days without carrier movement.
shipping-standard: Standard shipping estimate is 3-5 business days.`;

type TranscriptMessage = { role: "user" | "assistant"; content: string };
type RequestBody = {
  messages?: unknown;
  sessionId?: unknown;
  messageId?: unknown;
  orderNumber?: unknown;
};

const identifiers = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const syntheticOrders: Record<
  string,
  { number: string; item: string; status: string; returnEligible: boolean }
> = {
  "NS-10482": {
    number: "NS-10482",
    item: "Alpine Shell Jacket",
    status: "Delivered 12 days ago",
    returnEligible: true,
  },
};

function validatedMessages(value: unknown): TranscriptMessage[] | null {
  if (!Array.isArray(value) || value.length < 1 || value.length > 50) return null;
  const messages: TranscriptMessage[] = [];
  let totalCharacters = 0;
  for (const item of value) {
    if (
      !item ||
      typeof item !== "object" ||
      !("role" in item) ||
      !("content" in item) ||
      (item.role !== "user" && item.role !== "assistant") ||
      typeof item.content !== "string" ||
      item.content.trim().length < 1 ||
      item.content.length > 4000
    ) {
      return null;
    }
    totalCharacters += item.content.length;
    if (totalCharacters > 24_000) return null;
    messages.push({ role: item.role, content: item.content });
  }
  return messages.at(-1)?.role === "user" ? messages.slice(-12) : null;
}

export async function POST(request: NextRequest) {
  try {
    const raw = await request.text();
    if (raw.length > 50_000) {
      return Response.json({ message: "Please shorten the support request." }, { status: 413 });
    }
    const input = JSON.parse(raw) as RequestBody;
    const messages = validatedMessages(input.messages);
    if (
      !messages ||
      typeof input.sessionId !== "string" ||
      typeof input.messageId !== "string" ||
      !identifiers.test(input.sessionId) ||
      !identifiers.test(input.messageId)
    ) {
      return Response.json({ message: "Please enter a support question." }, { status: 400 });
    }
    const order =
      typeof input.orderNumber === "string" ? syntheticOrders[input.orderNumber] : undefined;
    if (input.orderNumber && !order) {
      return Response.json({ message: "The sample order is unavailable." }, { status: 400 });
    }
    if (order) {
      const last = messages[messages.length - 1];
      last.content += `\n\nSynthetic order context: order ${order.number}; item ${order.item}; delivery state ${order.status}; return eligible ${order.returnEligible}.`;
    }
    const upstream = await fetch(`${gateway}/v1/chat/completions`, {
      method: "POST",
      headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        model: "support-default",
        messages: [{ role: "system", content: systemPrompt }, ...messages],
        temperature: 0,
        stream: true,
        stream_options: { include_usage: true },
        metadata: {
          demo_session_id: input.sessionId,
          demo_message_id: input.messageId,
          client_app: "northstar-support-demo",
        },
      }),
      signal: request.signal,
    });
    if (!upstream.ok || !upstream.body) {
      return Response.json(
        { message: "Support is temporarily unavailable. Please try again." },
        { status: 503 },
      );
    }
    // Deliberately do not forward gateway headers or credentials to browser JavaScript.
    return new Response(upstream.body, {
      status: 200,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-transform",
        "X-Content-Type-Options": "nosniff",
      },
    });
  } catch {
    return Response.json(
      { message: "Support is temporarily unavailable. Please try again." },
      { status: 503 },
    );
  }
}
