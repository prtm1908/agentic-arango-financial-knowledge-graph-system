export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const jobId = searchParams.get("jobId");

  if (!jobId) {
    return new Response(JSON.stringify({ error: "jobId is required" }), {
      status: 400,
      headers: { "Content-Type": "application/json" }
    });
  }

  const backendUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  try {
    const upstream = await fetch(`${backendUrl}/api/events/${encodeURIComponent(jobId)}`, {
      headers: { Accept: "text/event-stream" },
      next: { revalidate: 0 }
    });

    if (!upstream.ok || !upstream.body) {
      const text = await upstream.text();
      return new Response(text || "Upstream SSE failed", {
        status: upstream.status,
        headers: { "Content-Type": "text/plain" }
      });
    }

    // Use TransformStream for proper streaming without buffering
    const { readable, writable } = new TransformStream();
    const writer = writable.getWriter();
    const reader = upstream.body.getReader();

    // Push data as it arrives (don't wait for pull)
    (async () => {
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) {
            await writer.close();
            break;
          }
          await writer.write(value);
        }
      } catch {
        await writer.abort();
      }
    })();

    return new Response(readable, {
      status: upstream.status,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-transform, no-store",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
        "Transfer-Encoding": "chunked"
      }
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unexpected error";
    return new Response(message, {
      status: 502,
      headers: { "Content-Type": "text/plain" }
    });
  }
}
