export type Invoice = {
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
};

export type Summary = {
  total_processed: number;
  needs_review_count: number;
  clean_count: number;
  by_vendor: Record<string, number>;
  total_amount_by_currency: Record<string, number>;
  generated_at: string;
};
