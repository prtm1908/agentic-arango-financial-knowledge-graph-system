export const runtime = "nodejs";

const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type RouteContext = {
  params: Promise<{ chatId: string }>;
};

// POST /api/chats/[chatId]/query - Send message with chat context
export async function POST(request: Request, context: RouteContext) {
  try {
    const { chatId } = await context.params;
    const payload = await request.json();

    const upstream = await fetch(`${BACKEND_URL}/api/chats/${chatId}/query`, {
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
