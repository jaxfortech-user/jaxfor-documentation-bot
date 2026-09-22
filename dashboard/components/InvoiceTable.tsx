import type { Invoice } from "@/lib/types";

export default function InvoiceTable({ invoices }: { invoices: Invoice[] }) {
  if (invoices.length === 0) {
    return <div className="empty-state">No invoices here yet.</div>;
  }

  return (
    <table>
      <thead>
        <tr>
          <th>Processed</th>
          <th>Vendor</th>
          <th>Invoice #</th>
          <th>Date</th>
          <th>Total</th>
          <th>Confidence</th>
          <th>Status</th>
          <th>File</th>
        </tr>
      </thead>
      <tbody>
        {invoices.map((inv, i) => (
          <tr key={`${inv.drive_file_id}-${i}`}>
            <td>{formatDate(inv.processed_at)}</td>
            <td>{inv.vendor_name || inv.vendor_tab}</td>
            <td>{inv.invoice_number || "—"}</td>
            <td>{inv.invoice_date || "—"}</td>
            <td>
              {inv.total_amount ? `${inv.total_amount} ${inv.currency}` : "—"}
            </td>
            <td>{inv.overall_confidence || "—"}</td>
            <td>
              {inv.needs_review ? (
                <span className="badge badge-review">Needs Review</span>
              ) : (
                <span className="badge badge-ok">Clean</span>
              )}
            </td>
            <td>
              {inv.drive_file_url ? (
                <a className="file-link" href={inv.drive_file_url} target="_blank" rel="noreferrer">
                  {inv.source_file}
                </a>
              ) : (
                inv.source_file
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function formatDate(iso: string) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}
