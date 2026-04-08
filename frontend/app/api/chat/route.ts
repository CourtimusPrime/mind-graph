import { type UIMessage } from "ai";
import { randomUUID } from "crypto";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

export async function POST(req: Request) {
  const { messages }: { messages: UIMessage[] } = await req.json();

  const lastMessage = messages[messages.length - 1];
  const messageText =
    typeof lastMessage?.content === "string"
      ? lastMessage.content
      : lastMessage?.content
          ?.filter((p: { type: string }) => p.type === "text")
          .map((p: { type: string; text: string }) => p.text)
          .join("") ?? "";

  const history = messages.slice(0, -1).map((m) => ({
    role: m.role,
    content:
      typeof m.content === "string"
        ? m.content
        : m.content
            ?.filter((p: { type: string }) => p.type === "text")
            .map((p: { type: string; text: string }) => p.text)
            .join("") ?? "",
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

  const messageId = randomUUID();
  const encoded = JSON.stringify(reply);
  const body = [
    `f:${JSON.stringify({ messageId })}`,
    `0:${encoded}`,
    `e:${JSON.stringify({ finishReason: "stop", usage: { promptTokens: 0, completionTokens: 0 }, isContinued: false })}`,
    `d:${JSON.stringify({ finishReason: "stop", usage: { promptTokens: 0, completionTokens: 0 } })}`,
  ].join("\n");

  return new Response(body, {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "x-vercel-ai-data-stream": "v1",
    },
  });
}
