import type { NextConfig } from "next";

const config: NextConfig = {
  output: "standalone",
  transpilePackages: ["@ecoroute/api-client"],
  allowedDevOrigins: ["127.0.0.1"],
};
export default config;
