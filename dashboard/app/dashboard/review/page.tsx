import { fetchBackend } from "@/lib/backend";
import type { Invoice, Summary } from "@/lib/types";
import InvoiceTable from "@/components/InvoiceTable";

export const dynamic = "force-dynamic";

export default async function ReviewQueuePage() {
  let invoices: Invoice[] = [];
  let spreadsheetUrl: string | null = null;
  let error: string | null = null;

  try {
    // Sequential, not Promise.all — see the note in dashboard/page.tsx.
    const data = await fetchBackend("/api/review-queue");
    invoices = data.invoices;
    const summary: Summary = await fetchBackend("/api/summary");
    spreadsheetUrl = summary.spreadsheet_url;
  } catch (err) {
    error = (err as Error).message;
  }

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
        <h1 className="page-title" style={{ marginBottom: 0 }}>
          Review Queue
        </h1>
        {spreadsheetUrl && (
          <a className="file-link" href={spreadsheetUrl} target="_blank" rel="noreferrer">
            Open extraction sheet →
          </a>
        )}
      </div>
      {error && <div className="error-state">Couldn't load data from the backend: {error}</div>}
      <InvoiceTable invoices={invoices} />
    </div>
  );
}