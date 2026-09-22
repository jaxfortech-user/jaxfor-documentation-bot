import { fetchBackend } from "@/lib/backend";
import type { Invoice, Summary, SheetTabsResponse } from "@/lib/types";
import StatCard from "@/components/StatCard";
import InvoiceTable from "@/components/InvoiceTable";
import SheetTabPicker from "@/components/SheetTabPicker";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  let summary: Summary | null = null;
  let invoices: Invoice[] = [];
  let sheetTabs: SheetTabsResponse | null = null;
  let error: string | null = null;

  try {
    // Fetched sequentially rather than with Promise.all: the backend's
    // Google API client isn't safe under concurrent calls (see
    // app/oauth_drive.py), so avoiding overlapping requests here is a
    // cheap extra safeguard on top of that fix.
    const summaryData = await fetchBackend("/api/summary");
    const invoicesData = await fetchBackend("/api/invoices");
    const tabsData = await fetchBackend("/api/sheet-tabs");
    summary = summaryData;
    invoices = invoicesData.invoices;
    sheetTabs = tabsData;
  } catch (err) {
    error = (err as Error).message;
  }

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
        <h1 className="page-title" style={{ marginBottom: 0 }}>
          Processed Invoices
        </h1>
        {sheetTabs && <SheetTabPicker spreadsheetUrl={sheetTabs.spreadsheet_url} tabs={sheetTabs.tabs} />}
      </div>

      {error && <div className="error-state">Couldn't load data from the backend: {error}</div>}

      {summary && (
        <div className="stats-row">
          <StatCard label="Total Processed" value={summary.total_processed} />
          <StatCard label="Needs Review" value={summary.needs_review_count} />
          <StatCard label="Clean" value={summary.clean_count} />
          <StatCard label="Vendors" value={Object.keys(summary.by_vendor).length} />
        </div>
      )}

      <InvoiceTable invoices={invoices} />
    </div>
  );
}