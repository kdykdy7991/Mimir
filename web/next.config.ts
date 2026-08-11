import type { NextConfig } from "next";

const apiBaseUrl = (process.env.INTERNAL_API_BASE_URL ?? "http://127.0.0.1:8766")
  .replace(/\/+$/, "");
const allowedDevOrigins = (process.env.NEXT_ALLOWED_DEV_ORIGINS ?? "")
  .split(",")
  .map((origin) => origin.trim())
  .filter(Boolean);

const nextConfig: NextConfig = {
  ...(allowedDevOrigins.length > 0 ? { allowedDevOrigins } : {}),
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
