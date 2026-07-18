import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "../components/Providers";
import { Shell } from "../components/Shell";

export const metadata: Metadata = {
  title: "EcoRoute Control Center",
  description: "Operational control plane for efficient, evidence-aware AI routing.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <Providers>
          <Shell>{children}</Shell>
        </Providers>
      </body>
    </html>
  );
}

