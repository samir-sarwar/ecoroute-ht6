import type { Metadata } from "next";
import { Fraunces, Karla } from "next/font/google";
import "./globals.css";

const display = Fraunces({
  subsets: ["latin"],
  variable: "--next-display",
  display: "swap",
});

const body = Karla({
  subsets: ["latin"],
  variable: "--next-body",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Help & Support · Northstar Outfitters",
  description: "Customer care for Northstar Outfitters orders, returns, shipping, and exchanges.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${display.variable} ${body.variable}`}>
      <body>{children}</body>
    </html>
  );
}
