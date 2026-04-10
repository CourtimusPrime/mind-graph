import { resolveBackendUrl } from "@/lib/backend-url";

export async function GET() {
  try {
    const BACKEND_URL = await resolveBackendUrl();
    const res = await fetch(`${BACKEND_URL}/api/nodes`);
    if (!res.ok) {
      return new Response(`Backend error: ${res.status}`, { status: 502 });
    }
    const data = await res.json();
    return Response.json(data);
  } catch (e) {
    return new Response(`Failed to reach backend: ${e}`, { status: 503 });
  }
}

export async function DELETE(request: Request) {
  try {
    const { label, name } = await request.json();
    const BACKEND_URL = await resolveBackendUrl();
    const res = await fetch(
      `${BACKEND_URL}/api/nodes/${encodeURIComponent(label)}/${encodeURIComponent(name)}`,
      { method: "DELETE" },
    );
    if (!res.ok) {
      return new Response(`Backend error: ${res.status}`, { status: 502 });
    }
    const data = await res.json();
    return Response.json(data);
  } catch (e) {
    return new Response(`Failed to reach backend: ${e}`, { status: 503 });
  }
}
