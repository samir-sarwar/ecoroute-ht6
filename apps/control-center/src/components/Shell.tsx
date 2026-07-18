"use client";

import {
  Activity,
  Bot,
  Boxes,
  Database,
  FileChartColumn,
  Gauge,
  Menu,
  Network,
  Route,
  Settings,
  X,
} from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

const navigation = [
  ["/", "Overview", Gauge],
  ["/routing-policies", "Routing Policies", Route],
  ["/model-endpoints", "Model Endpoints", Boxes],
  ["/slm-studio", "SLM Studio", Bot],
  ["/semantic-cache", "Semantic Cache", Database],
  ["/self-hosted-nodes", "Self-Hosted Nodes", Network],
  ["/request-audit", "Request Audit", Activity],
  ["/impact-reports", "Impact Reports", FileChartColumn],
  ["/settings", "Settings", Settings],
] as const;

export function Shell({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    const events = new EventSource("/api/control/events");
    events.onopen = () => setConnected(true);
    events.onmessage = () => { void queryClient.invalidateQueries(); };
    events.onerror = () => setConnected(false);
    const reconciliation = window.setInterval(() => { void queryClient.invalidateQueries(); }, 30_000);
    return () => { events.close(); window.clearInterval(reconciliation); };
  }, [queryClient]);
  return (
    <div className="app-shell">
      <button className="mobile-menu" onClick={() => setOpen(!open)} aria-label="Toggle navigation">
        {open ? <X /> : <Menu />}
      </button>
      <aside className={open ? "sidebar open" : "sidebar"}>
        <div className="brand"><span className="brand-mark">E</span><span>EcoRoute</span></div>
        <div className="workspace-label">NORTHSTAR / DEMO</div>
        <nav aria-label="Control center navigation">
          {navigation.map(([href, label, Icon]) => (
            <Link key={href} href={href} className={path === href ? "nav-link active" : "nav-link"} onClick={() => setOpen(false)}>
              <Icon size={17} aria-hidden="true" /><span>{label}</span>
            </Link>
          ))}
        </nav>
        <div className="connection"><span className={connected ? "dot online" : "dot"} />{connected ? "Live events connected" : "Reconnecting"}</div>
      </aside>
      <main className="main-content">{children}</main>
    </div>
  );
}
