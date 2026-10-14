
export type Invoice = {
  // Existing dashboard fields
  vendor_tab: string;
  processed_at: string;
  source_file: string;
  document_type: string;
  vendor_name: string;
  invoice_number: string;
  invoice_date: string;
  due_date: string;
  po_number: string;
  currency: string;
  subtotal: string;
  tax_amount: string;
  total_amount: string;
  overall_confidence: string;
  needs_review: boolean;
  disagreements: string[];
  drive_file_id: string;
  drive_file_url: string | null;

  // Additional fields discovered dynamically by the backend
  customer_name?: string;
  customer_address?: string;
  vendor_address?: string;
  tax_registration_number?: string;
  payment_terms?: string;
  line_items?: string;

  // Other vendor-specific fields, normalized by the backend
  additional_fields?: Record<string, string>;

  // Allow other dynamically discovered fields at the top level
  [key: string]:
    | string
    | boolean
    | string[]
    | null
    | Record<string, string>
    | undefined;
};

export type Summary = {
  total_processed: number;
  needs_review_count: number;
  clean_count: number;
  by_vendor: Record<string, number>;
  total_amount_by_currency: Record<string, number>;
  generated_at: string;
  spreadsheet_url: string;
};

export type SheetTab = { title: string; url: string };

export type SheetTabsResponse = {
  spreadsheet_url: string;
  tabs: SheetTab[];
};
