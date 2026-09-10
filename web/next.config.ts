import path from "node:path";
import type { NextConfig } from "next";

const apiBaseUrl = (process.env.INTERNAL_API_BASE_URL ?? "http://127.0.0.1:8766")
  .replace(/\/+$/, "");
const allowedDevOrigins = (process.env.NEXT_ALLOWED_DEV_ORIGINS ?? "127.0.0.1")
  .split(",")
  .map((origin) => origin.trim())
  .filter(Boolean);

const nextConfig: NextConfig = {
  ...(allowedDevOrigins.length > 0 ? { allowedDevOrigins } : {}),
  // This repository also contains the Python backend at its root. Pin the
  // frontend workspace so an unrelated root lockfile cannot make Turbopack
  // resolve React/Next modules from the wrong node_modules directory.
  turbopack: {
    root: path.resolve(__dirname),
  },
  experimental: {
    // FastAPI accepts batches up to 2 GB; leave room for multipart metadata.
    proxyClientMaxBodySize: "2100mb",
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${apiBaseUrl}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
