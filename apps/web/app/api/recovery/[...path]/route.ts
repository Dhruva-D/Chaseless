import { NextRequest, NextResponse } from "next/server";

const API_INTERNAL_URL =
  process.env.API_INTERNAL_URL ?? "http://127.0.0.1:8000/api/v1";

async function proxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  const target = `${API_INTERNAL_URL}/${path.join("/")}${request.nextUrl.search}`;
  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  const idempotencyKey = request.headers.get("idempotency-key");
  if (contentType) headers.set("content-type", contentType);
  if (idempotencyKey) headers.set("idempotency-key", idempotencyKey);
  headers.set(
    "x-internal-token",
    process.env.INTERNAL_SERVICE_TOKEN ?? "development-service-token",
  );

  const body = request.method === "GET" ? undefined : await request.text();
  const response = await fetch(target, {
    method: request.method,
    headers,
    body,
    cache: "no-store",
  });
  return new NextResponse(response.body, {
    status: response.status,
    headers: {
      "content-type": response.headers.get("content-type") ?? "application/json",
    },
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
