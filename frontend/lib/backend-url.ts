/**
 * Resolves the backend base URL, trying ports in order until one responds.
 *
 * Resolution order:
 *   1. BACKEND_URL env var (explicit override — no scanning)
 *   2. Scan localhost:8000–8888, picking the first port that answers /health
 *
 * The resolved URL is cached for the lifetime of the Next.js server process.
 */

const BACKEND_PORT_START = 8000;
const BACKEND_PORT_END = 8888;
const HEALTH_PATH = "/health";
const PROBE_TIMEOUT_MS = 300;

let cachedUrl: string | null = null;

async function probePort(port: number): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  try {
    const res = await fetch(`http://localhost:${port}${HEALTH_PATH}`, {
      signal: controller.signal,
    });
    return res.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

export async function resolveBackendUrl(): Promise<string> {
  // Explicit env var — trust it completely, skip scanning
  if (process.env.BACKEND_URL) return process.env.BACKEND_URL;

  // Return cached result from a previous probe
  if (cachedUrl) return cachedUrl;

  for (let port = BACKEND_PORT_START; port <= BACKEND_PORT_END; port++) {
    if (await probePort(port)) {
      cachedUrl = `http://localhost:${port}`;
      return cachedUrl;
    }
  }

  // Nothing found — fall back to the conventional default so the caller
  // gets a meaningful error rather than a silent undefined.
  return `http://localhost:${BACKEND_PORT_START}`;
}
