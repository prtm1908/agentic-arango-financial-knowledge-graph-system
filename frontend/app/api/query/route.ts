export const runtime = "nodejs";

export async function POST(request: Request) {
  try {
    const payload = await request.json();
    const backendUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

    const upstream = await fetch(`${backendUrl}/api/query`, {
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
