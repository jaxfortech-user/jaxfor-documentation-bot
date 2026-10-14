"use client";

import { Fragment, useState } from "react";
import type { Invoice } from "@/lib/types";

type DetailField = {
  label: string;
  value: unknown;
};

const STANDARD_DETAIL_FIELDS: {
  key: string;
  label: string;
}[] = [
  { key: "customer_name", label: "Customer Name" },
  { key: "customer_address", label: "Customer Address" },
  { key: "vendor_address", label: "Vendor Address" },
  { key: "tax_registration_number", label: "Tax Registration Number" },
  { key: "payment_terms", label: "Payment Terms" },
  { key: "po_number", label: "Purchase Order Number" },
  { key: "due_date", label: "Due Date" },
  { key: "subtotal", label: "Subtotal" },
  { key: "tax_amount", label: "Tax Amount" },
  { key: "document_type", label: "Document Type" },
  { key: "disagreements", label: "Extraction Disagreements" },
  { key: "line_items", label: "Line Items" },
];

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "";
  }

  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }

  if (typeof value === "object") {
    return JSON.stringify(value, null, 2);
  }

  return String(value);
}

function formatLabel(key: string): string {
  return key
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function getDetailFields(inv: Invoice): DetailField[] {
  const fields: DetailField[] = [];

  for (const field of STANDARD_DETAIL_FIELDS) {
    const value = inv[field.key];

    if (displayValue(value)) {
      fields.push({
        label: field.label,
        value,
      });
    }
  }

  const additionalFields = inv.additional_fields;

  if (
    additionalFields &&
    typeof additionalFields === "object" &&
    !Array.isArray(additionalFields)
  ) {
    const standardKeys = new Set(
      STANDARD_DETAIL_FIELDS.map((field) => field.key.toLowerCase()),
    );

    for (const [key, value] of Object.entries(additionalFields)) {
      if (standardKeys.has(key.toLowerCase())) {
        continue;
      }

      if (!displayValue(value)) {
        continue;
      }

      fields.push({
        label: formatLabel(key),
        value,
      });
    }
  }

  return fields;
}

export default function InvoiceTable({
  invoices,
}: {
  invoices: Invoice[];
}) {
  const [expandedRows, setExpandedRows] = useState<Set<string>>(
    () => new Set(),
  );

  function toggleDetails(rowKey: string) {
    setExpandedRows((current) => {
      const next = new Set(current);

      if (next.has(rowKey)) {
        next.delete(rowKey);
      } else {
        next.add(rowKey);
      }

      return next;
    });
  }

  if (invoices.length === 0) {
    return <div className="empty-state">No invoices here yet.</div>;
  }

  return (
    <div className="invoice-table-wrap">
      <table className="invoice-table">
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
            <th>Details</th>
          </tr>
        </thead>

        <tbody>
          {invoices.map((inv, i) => {
            const rowKey = `${
              inv.drive_file_id || inv.invoice_number || "invoice"
            }-${i}`;

            const isExpanded = expandedRows.has(rowKey);
            const detailFields = getDetailFields(inv);

            return (
              <Fragment key={rowKey}>
                <tr>
                  <td>{formatDate(inv.processed_at)}</td>

                  <td>{inv.vendor_name || inv.vendor_tab || "—"}</td>

                  <td>{inv.invoice_number || "—"}</td>

                  <td>{inv.invoice_date || "—"}</td>

                  <td>
                    {inv.total_amount
                      ? `${inv.total_amount} ${inv.currency || ""}`.trim()
                      : "—"}
                  </td>

                  <td>{inv.overall_confidence || "—"}</td>

                  <td>
                    {inv.needs_review ? (
                      <span className="badge badge-review">
                        Needs Review
                      </span>
                    ) : (
                      <span className="badge badge-ok">Clean</span>
                    )}
                  </td>

                  <td>
                    {inv.drive_file_url ? (
                      <a
                        className="file-link"
                        href={inv.drive_file_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {inv.source_file || "Open file"}
                      </a>
                    ) : (
                      inv.source_file || "—"
                    )}
                  </td>

                  <td>
                    <button
                      type="button"
                      className="details-toggle"
                      onClick={() => toggleDetails(rowKey)}
                      aria-expanded={isExpanded}
                      aria-controls={`details-${i}`}
                    >
                      {isExpanded ? "Hide details" : "View details"}
                      <span aria-hidden="true">
                        {isExpanded ? " ↑" : " ↓"}
                      </span>
                    </button>
                  </td>
                </tr>

                {isExpanded && (
                  <tr className="invoice-details-row">
                    <td colSpan={9}>
                      <section
                        id={`details-${i}`}
                        className="invoice-details-panel"
                        aria-label={`Invoice details: ${
                          inv.invoice_number || inv.source_file || "Invoice"
                        }`}
                      >
                        <div className="invoice-details-heading">
                          <div>
                            <h3>Invoice Details</h3>
                            <p>
                              {inv.vendor_name ||
                                inv.vendor_tab ||
                                "Unknown vendor"}
                              {inv.invoice_number
                                ? ` · Invoice ${inv.invoice_number}`
                                : ""}
                            </p>
                          </div>

                          <span className="invoice-details-count">
                            {detailFields.length} additional{" "}
                            {detailFields.length === 1 ? "field" : "fields"}
                          </span>
                        </div>

                        {detailFields.length > 0 ? (
                          <dl className="invoice-details-grid">
                            {detailFields.map((field) => (
                              <div
                                className="invoice-detail-item"
                                key={field.label}
                              >
                                <dt>{field.label}</dt>
                                <dd>
                                  <span className="invoice-detail-value">
                                    {displayValue(field.value)}
                                  </span>
                                </dd>
                              </div>
                            ))}
                          </dl>
                        ) : (
                          <p className="invoice-details-empty">
                            No additional invoice fields were extracted for
                            this record.
                          </p>
                        )}
                      </section>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Format dates deterministically in UTC.
 * Explicit UTC getters and fixed English month names ensure that
 * server and browser render exactly the same text.
 */

function formatDate(iso: string) {
  if (!iso) return "—";

  const date = new Date(iso);

  if (Number.isNaN(date.getTime())) {
    return iso;
  }

  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Dubai",
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  })
    .format(date)
    .replace(",", "");
}
