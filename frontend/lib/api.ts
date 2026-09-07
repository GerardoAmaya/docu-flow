// Cliente de la API. Todo pasa por aqui para que el manejo de errores y la
// URL base vivan en un solo lugar.

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type DocumentStatus =
  | "pending"
  | "ocr_running"
  | "extracting"
  | "embedding"
  | "needs_review"
  | "completed"
  | "failed";

export interface DocumentSummary {
  id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  status: DocumentStatus;
  page_count: number | null;
  uploaded_at: string;
  error_message: string | null;
}

export interface BBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface ExtractedFieldData {
  id: string;
  document_id: string;
  field_name: string;
  value_text: string | null;
  corrected_value: string | null;
  confidence: number;
  page_number: number | null;
  bbox: BBox | null;
  source_snippet: string | null;
  needs_review: boolean;
  reviewed_at: string | null;
  reviewed_by: string | null;
}

export interface PageSummary {
  id: string;
  page_number: number;
  width_px: number | null;
  height_px: number | null;
  ocr_confidence: number | null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${body.slice(0, 200)}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  listDocuments: () =>
    request<{ items: DocumentSummary[]; total: number }>("/documents?limit=100"),

  getDocument: (id: string) => request<DocumentSummary>(`/documents/${id}`),

  listPages: (id: string) =>
    request<{ items: PageSummary[] }>(`/documents/${id}/pages`),

  listFields: (id: string) =>
    request<{ items: ExtractedFieldData[] }>(`/documents/${id}/fields`),

  correctField: (fieldId: string, value: string, reviewedBy = "revisor") =>
    request<ExtractedFieldData>(`/fields/${fieldId}`, {
      method: "PATCH",
      body: JSON.stringify({ corrected_value: value, reviewed_by: reviewedBy }),
    }),

  pageImageUrl: (documentId: string, pageNumber: number) =>
    `${API_URL}/documents/${documentId}/pages/${pageNumber}/image`,
};

// Etiquetas en el idioma del usuario, no los nombres de columna de la base.
export const FIELD_LABELS: Record<string, string> = {
  vendor_name: "Proveedor",
  vendor_tax_id: "NIT del proveedor",
  buyer_name: "Cliente",
  buyer_tax_id: "NIT del cliente",
  invoice_number: "Número de factura",
  issue_date: "Fecha de emisión",
  due_date: "Vencimiento",
  currency: "Moneda",
  subtotal: "Subtotal",
  tax_amount: "IVA",
  total: "Total",
};

// Campos que se muestran alineados por dígito.
export const NUMERIC_FIELDS = new Set([
  "subtotal",
  "tax_amount",
  "total",
  "vendor_tax_id",
  "buyer_tax_id",
  "invoice_number",
]);

export interface ReviewQueueItem {
  document_id: string;
  filename: string;
  status: DocumentStatus;
  pending_fields: number;
  lowest_confidence: number;
}

export interface AggregateSummary {
  invoice_count: number;
  invoices_with_total: number;
  subtotal_sum: number;
  tax_sum: number;
  total_sum: number;
  earliest_issue_date: string | null;
  latest_issue_date: string | null;
  by_vendor: { vendor_name: string; invoice_count: number; total_sum: number }[];
}

export interface Citation {
  quote: string;
  document_id: string;
  filename: string;
  page_number: number | null;
  bbox: BBox | null;
}

export interface ChatAnswer {
  question: string;
  answer: string;
  sufficient_context: boolean;
  retrieved_chunks: number;
  discarded_citations: number;
  is_aggregate_question: boolean;
  covers_full_corpus: boolean;
  citations: Citation[];
}

export const extraApi = {
  reviewQueue: () =>
    request<{ items: ReviewQueueItem[] }>("/review/queue"),

  summary: () => request<AggregateSummary>("/invoices/summary"),

  ask: (question: string) =>
    request<ChatAnswer>("/chat", {
      method: "POST",
      body: JSON.stringify({ question }),
    }),
};

/** Formatea montos como dinero, con el signo pegado al numero. */
export function money(value: number | null | undefined, currency = "USD"): string {
  if (value === null || value === undefined) return "—";
  return `${currency} ${value.toLocaleString("es-SV", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}
