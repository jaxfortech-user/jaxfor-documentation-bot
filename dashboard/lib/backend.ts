/**
 * Server-only helper for calling the Railway backend. Never import
 * this from a client component — JAXFOR_API_KEY is a server secret.
 */
export async function fetchBackend(path: string) {
  const base = process.env.JAXFOR_API_BASE_URL;
  const key = process.env.JAXFOR_API_KEY;

  if (!base || !key) {
    throw new Error("JAXFOR_API_BASE_URL / JAXFOR_API_KEY not configured");
  }

  const res = await fetch(`${base}${path}`, {
    headers: { "X-API-Key": key },
    cache: "no-store",
  });

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`Backend request failed (${res.status}): ${body}`);
  }

  return res.json();
}
