import { fetchBackend } from "@/lib/backend";
import type { Invoice } from "@/lib/types";
import InvoiceTable from "@/components/InvoiceTable";

export const dynamic = "force-dynamic";

export default async function ReviewQueuePage() {
  let invoices: Invoice[] = [];
  let error: string | null = null;

  try {
    const data = await fetchBackend("/api/review-queue");
    invoices = data.invoices;
  } catch (err) {
    error = (err as Error).message;
  }

  return (
    <div>
      <h1 className="page-title">Review Queue</h1>
      {error && <div className="error-state">Couldn't load data from the backend: {error}</div>}
      <InvoiceTable invoices={invoices} />
    </div>
  );
}
