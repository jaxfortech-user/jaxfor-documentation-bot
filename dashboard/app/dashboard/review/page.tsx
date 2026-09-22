import { fetchBackend } from "@/lib/backend";
import type { Invoice, SheetTabsResponse } from "@/lib/types";
import InvoiceTable from "@/components/InvoiceTable";
import SheetTabPicker from "@/components/SheetTabPicker";

export const dynamic = "force-dynamic";

export default async function ReviewQueuePage() {
  let invoices: Invoice[] = [];
  let sheetTabs: SheetTabsResponse | null = null;
  let error: string | null = null;

  try {
    // Sequential, not Promise.all — see the note in dashboard/page.tsx.
    const data = await fetchBackend("/api/review-queue");
    invoices = data.invoices;
    const tabsData: SheetTabsResponse = await fetchBackend("/api/sheet-tabs");
    sheetTabs = tabsData;
  } catch (err) {
    error = (err as Error).message;
  }

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
        <h1 className="page-title" style={{ marginBottom: 0 }}>
          Review Queue
        </h1>
        {sheetTabs && <SheetTabPicker spreadsheetUrl={sheetTabs.spreadsheet_url} tabs={sheetTabs.tabs} />}
      </div>
      {error && <div className="error-state">Couldn't load data from the backend: {error}</div>}
      <InvoiceTable invoices={invoices} />
    </div>
  );
}