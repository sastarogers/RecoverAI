/** @type {import('next').NextConfig} */
const API_ORIGIN = process.env.NEXT_PUBLIC_API_ORIGIN || "http://localhost:8000";

const nextConfig = {
  reactStrictMode: true,
  // The browser talks to /api/* on its own origin; Next proxies to FastAPI. This keeps
  // the API base URL out of client bundles and avoids CORS entirely in development.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` }];
  },
};

module.exports = nextConfig;
