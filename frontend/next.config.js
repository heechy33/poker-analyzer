/** @type {import('next').NextConfig} */
const nextConfig = {
  // Do not set COEP/COOP on all routes — that blocks browser fetch() to the FastAPI
  // backend (localhost:8000) unless every API response includes CORP headers.
  // WASM solver pages can opt into isolation later on specific routes if needed.
  async rewrites() {
    const backend =
      process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://127.0.0.1:8000";
    return [
      {
        // Browser calls same-origin /api/*; Next proxies to FastAPI (avoids CORS and
        // ad-blockers that block cross-origin URLs containing "upload").
        source: "/api/:path*",
        destination: `${backend}/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
