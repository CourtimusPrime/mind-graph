import { type UIMessage } from "ai";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

function sseChunk(obj: unknown): string {
  return `data: ${JSON.stringify(obj)}\n\n`;
}

const extractText = (m: UIMessage): string =>
  m.parts
    .filter((p): p is { type: "text"; text: string } => p.type === "text")
    .map((p) => p.text)
    .join("");

export async function POST(req: Request) {
  const { messages }: { messages: UIMessage[] } = await req.json();

  const lastMessage = messages[messages.length - 1];
  const messageText = extractText(lastMessage);

  const history = messages.slice(0, -1).map((m) => ({
    role: m.role,
    content: extractText(m),
  }));

  const backendRes = await fetch(`${BACKEND_URL}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message: messageText,
      history,
      session_id: "default",
    }),
  });

  if (!backendRes.ok) {
    const text = await backendRes.text();
    return new Response(`Backend error: ${text}`, { status: 502 });
  }

  const { reply } = (await backendRes.json()) as { reply: string };

  const body = [
    sseChunk({ type: "text-start", id: "text-1" }),
    sseChunk({ type: "text-delta", id: "text-1", delta: reply }),
    sseChunk({ type: "finish-step" }),
    sseChunk({ type: "finish" }),
    "data: [DONE]\n\n",
  ].join("");

  return new Response(body, {
    headers: {
      "content-type": "text/event-stream",
      "cache-control": "no-cache",
      "x-vercel-ai-ui-message-stream": "v1",
    },
  });
}
