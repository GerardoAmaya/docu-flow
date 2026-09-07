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
