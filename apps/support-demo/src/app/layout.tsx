import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Help & Support · Northstar Outfitters",
  description: "Customer care for Northstar Outfitters orders, returns, shipping, and exchanges.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}

