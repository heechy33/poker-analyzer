import { createServer } from "node:http";

const host = "127.0.0.1";
const port = 54321;

const user = {
  id: "00000000-0000-4000-8000-000000000001",
  aud: "authenticated",
  role: "authenticated",
  email: "study@example.com",
  phone: "",
  app_metadata: { provider: "email", providers: ["email"] },
  user_metadata: {},
  identities: [],
  created_at: "2026-01-01T00:00:00.000Z",
  updated_at: "2026-01-01T00:00:00.000Z",
};

const session = {
  access_token: "p0-acceptance-access-token",
  refresh_token: "p0-acceptance-refresh-token",
  expires_in: 2_147_483_647,
  expires_at: 4_102_444_800,
  token_type: "bearer",
  user,
};

function sendJson(response, status, body) {
  response.writeHead(status, {
    "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-client-info",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Origin": "*",
    "Content-Type": "application/json",
  });
  response.end(JSON.stringify(body));
}

const server = createServer((request, response) => {
  const url = new URL(request.url ?? "/", `http://${host}:${port}`);

  if (request.method === "OPTIONS") {
    response.writeHead(204, {
      "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-client-info",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Origin": "*",
    });
    response.end();
    return;
  }

  if (url.pathname === "/health") {
    sendJson(response, 200, { status: "ok" });
    return;
  }

  if (url.pathname === "/auth/v1/user" && request.method === "GET") {
    sendJson(response, 200, user);
    return;
  }

  if (url.pathname === "/auth/v1/token" && request.method === "POST") {
    sendJson(response, 200, session);
    return;
  }

  sendJson(response, 404, { message: "P0 acceptance auth mock: route not found" });
});

server.listen(port, host, () => {
  process.stdout.write(`P0 acceptance auth mock listening on http://${host}:${port}\n`);
});
