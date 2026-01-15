export const runtime = "nodejs";

const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// GET /api/chats - List all chats
export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    const skip = searchParams.get("skip") || "0";
    const limit = searchParams.get("limit") || "20";

    const upstream = await fetch(
      `${BACKEND_URL}/api/chats?skip=${skip}&limit=${limit}`,
      { method: "GET" }
    );

    const text = await upstream.text();
    return new Response(text, {
      status: upstream.status,
      headers: { "Content-Type": "application/json" }
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unexpected error";
    return new Response(JSON.stringify({ error: message }), {
      status: 500,
      headers: { "Content-Type": "application/json" }
    });
  }
}

// POST /api/chats - Create new chat
export async function POST(request: Request) {
  try {
    const payload = await request.json();

    const upstream = await fetch(`${BACKEND_URL}/api/chats`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    const text = await upstream.text();
    return new Response(text, {
      status: upstream.status,
      headers: { "Content-Type": "application/json" }
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unexpected error";
    return new Response(JSON.stringify({ error: message }), {
      status: 500,
      headers: { "Content-Type": "application/json" }
    });
  }
}
