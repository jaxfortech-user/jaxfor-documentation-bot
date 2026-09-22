import { auth } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";
import { fetchBackend } from "@/lib/backend";

export async function GET() {
  const { userId } = await auth();
  if (!userId) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  try {
    const data = await fetchBackend("/api/summary");
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json({ error: (err as Error).message }, { status: 502 });
  }
}
