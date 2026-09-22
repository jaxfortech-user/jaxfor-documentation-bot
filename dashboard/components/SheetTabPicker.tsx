"use client";

import { useState } from "react";

type SheetTab = { title: string; url: string };

export default function SheetTabPicker({
  spreadsheetUrl,
  tabs,
}: {
  spreadsheetUrl: string;
  tabs: SheetTab[];
}) {
  const [open, setOpen] = useState(false);

  // Only one vendor tab (or none yet) — a plain link is enough, no
  // need for a picker.
  if (tabs.length <= 1) {
    return (
      <a className="file-link" href={spreadsheetUrl} target="_blank" rel="noreferrer">
        Open extraction sheet →
      </a>
    );
  }

  return (
    <div style={{ position: "relative" }}>
      <button
        onClick={() => setOpen((v) => !v)}
        style={{
          background: "transparent",
          border: "1px solid var(--border)",
          borderRadius: 8,
          color: "var(--accent)",
          padding: "6px 12px",
          fontSize: 14,
          cursor: "pointer",
        }}
      >
        Open extraction sheet ({tabs.length} vendors) ▾
      </button>
      {open && (
        <>
          {/* Click-away layer */}
          <div
            onClick={() => setOpen(false)}
            style={{ position: "fixed", inset: 0, zIndex: 10 }}
          />
          <div
            style={{
              position: "absolute",
              right: 0,
              top: "calc(100% + 6px)",
              background: "var(--panel)",
              border: "1px solid var(--border)",
              borderRadius: 10,
              minWidth: 220,
              zIndex: 20,
              overflow: "hidden",
            }}
          >
            <a
              href={spreadsheetUrl}
              target="_blank"
              rel="noreferrer"
              style={{
                display: "block",
                padding: "10px 14px",
                fontSize: 13,
                color: "var(--muted)",
                borderBottom: "1px solid var(--border)",
                textDecoration: "none",
              }}
            >
              Whole spreadsheet
            </a>
            {tabs.map((tab) => (
              <a
                key={tab.url}
                href={tab.url}
                target="_blank"
                rel="noreferrer"
                style={{
                  display: "block",
                  padding: "10px 14px",
                  fontSize: 14,
                  color: "var(--text)",
                  textDecoration: "none",
                }}
              >
                {tab.title}
              </a>
            ))}
          </div>
        </>
      )}
    </div>
  );
}