"use client";

import { ArrowUp, Check, CircleStop, Headphones, Package, RotateCcw, Send, ShieldCheck, Truck } from "lucide-react";
import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";

type Message = { id: string; role: "user" | "assistant"; content: string; state?: "streaming" | "error" };
type Order = { number: string; item: string; status: string; returnEligible: boolean };

const suggestions = [
  ["Returns", "What is your return window for unused items?", RotateCcw],
  ["Shipping", "How long does standard shipping take?", Truck],
  ["Damaged item", "My boots arrived damaged. What should I do?", ShieldCheck],
  ["Exchanges", "Can I exchange an item for another size?", Package],
] as const;

const demoOrder: Order = { number: "NS-10482", item: "Alpine Shell Jacket", status: "Delivered 12 days ago", returnEligible: true };

export function SupportChat() {
  const sessionId = useMemo(() => crypto.randomUUID(), []);
  const [messages, setMessages] = useState<Message[]>([{ id: "welcome", role: "assistant", content: "Hi! I’m here to help with returns, shipping, exchanges, and product issues. What can I help you with today?" }]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [order, setOrder] = useState<Order | null>(null);
  const controller = useRef<AbortController | null>(null);
  const messageEnd = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    messageEnd.current?.scrollIntoView({ block: "nearest" });
  }, [messages]);

  async function send(message = draft) {
    const content = message.trim();
    if (!content || sending) return;
    const user: Message = { id: crypto.randomUUID(), role: "user", content };
    const assistant: Message = { id: crypto.randomUUID(), role: "assistant", content: "", state: "streaming" };
    const next = [...messages, user];
    setMessages([...next, assistant]);
    setDraft("");
    setSending(true);
    controller.current = new AbortController();
    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: next.filter((item) => item.id !== "welcome").map(({ role, content }) => ({ role, content })),
          sessionId,
          messageId: user.id,
          orderNumber: order?.number ?? null,
        }),
        signal: controller.current.signal,
      });
      if (!response.ok || !response.body) throw new Error("unavailable");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let complete = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() ?? "";
        for (const event of events) {
          const line = event.split("\n").find((item) => item.startsWith("data: "));
          if (!line || line === "data: [DONE]") continue;
          try {
            const chunk = JSON.parse(line.slice(6));
            complete += chunk.choices?.[0]?.delta?.content ?? "";
            setMessages((current) => current.map((item) => item.id === assistant.id ? { ...item, content: complete } : item));
          } catch { /* wait for the next complete SSE frame */ }
        }
      }
      if (!complete.trim()) throw new Error("empty response");
      setMessages((current) => current.map((item) => item.id === assistant.id ? { ...item, content: complete.trim(), state: undefined } : item));
    } catch (error) {
      if ((error as Error).name === "AbortError") {
        setMessages((current) => current.filter((item) => item.id !== assistant.id));
      } else {
        setMessages((current) => current.map((item) => item.id === assistant.id ? { ...item, content: "Support is temporarily unavailable. Please try again in a moment.", state: "error" } : item));
      }
    } finally {
      setSending(false);
      controller.current = null;
    }
  }

  function submit(event: FormEvent) { event.preventDefault(); void send(); }
  function keyboard(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(); }
  }

  return (
    <div className="support-page">
      <header className="store-header"><div className="store-wordmark"><span>N</span><strong>Northstar Outfitters</strong></div><nav><a href="#help">Help center</a><button aria-label="Shopping bag"><Package size={19} /></button></nav></header>
      <main id="help" className="support-shell">
        <section className="intro"><div className="support-icon"><Headphones /></div><div><p className="kicker">CUSTOMER CARE</p><h1>Help &amp; Support</h1><p>Answers for orders, delivery, returns, and exchanges.</p></div></section>
        <div className="content-grid">
          <section className="conversation" aria-label="Support conversation">
            <div className="messages" aria-live="polite" aria-busy={sending}>
              {messages.map((message) => <div className={`message-row ${message.role}`} key={message.id}><div className="avatar">{message.role === "assistant" ? "N" : "You"}</div><div className={`bubble ${message.state ?? ""}`}>{message.content || <span className="typing"><i /><i /><i /></span>}{message.state === "error" ? <button onClick={() => send(messages.at(-2)?.content ?? "")}>Try again</button> : null}</div></div>)}
              <div ref={messageEnd} />
            </div>
            <form className="composer" onSubmit={submit}><textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={keyboard} placeholder="Ask about an order, return, or delivery…" rows={2} maxLength={4000} aria-label="Message customer support" /><div className="composer-footer"><span>Enter to send · Shift + Enter for a new line</span>{sending ? <button type="button" className="cancel" onClick={() => controller.current?.abort()}><CircleStop size={16} /> Cancel</button> : <button type="submit" className="send" disabled={!draft.trim()}><Send size={16} /> Send</button>}</div></form>
          </section>
          <aside className="support-aside">
            <section><h2>Popular questions</h2><div className="suggestions">{suggestions.map(([label, prompt, Icon]) => <button key={label} onClick={() => send(prompt)} disabled={sending}><span><Icon size={17} /></span><span><strong>{label}</strong><small>{prompt}</small></span><ArrowUp size={15} /></button>)}</div></section>
            <section className="order-lookup"><h2>Need help with an order?</h2>{order ? <div className="order-card"><div><span>ORDER {order.number}</span><strong>{order.item}</strong><small>{order.status}</small></div><div className="eligible"><Check size={14} /> Return eligible</div><button onClick={() => setOrder(null)}>Remove order</button></div> : <><p>Use a sample order to see personalized delivery and return help.</p><button onClick={() => setOrder(demoOrder)}>Load sample order</button></>}</section>
          </aside>
        </div>
      </main>
      <footer>© 2026 Northstar Outfitters · Fictional store for demonstration purposes</footer>
    </div>
  );
}
