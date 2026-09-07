"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { API_URL, api, DocumentSummary } from "@/lib/api";

const STATUS_LABEL: Record<string, string> = {
  pending: "En cola",
  ocr_running: "Leyendo",
  extracting: "Extrayendo",
  embedding: "Indexando",
  needs_review: "Necesita revisión",
  completed: "Listo",
  failed: "Falló",
};

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);

  async function refresh() {
    try {
      const data = await api.listDocuments();
      setDocuments(data.items);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    // Mientras haya documentos en proceso, refrescamos. El procesamiento es
    // asincrono y sin esto el usuario tendria que recargar a mano.
    const timer = setInterval(refresh, 4000);
    return () => clearInterval(timer);
  }, []);

  async function upload(file: File) {
    setUploading(true);
    setError(null);
    try {
      const body = new FormData();
      body.append("file", file);
      const response = await fetch(`${API_URL}/documents`, {
        method: "POST",
        body,
      });
      if (!response.ok) throw new Error(await response.text());
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
    }
  }

  const pending = documents.filter((d) => d.status === "needs_review");

  return (
    <main className="mx-auto max-w-4xl px-6 py-10">
      <h1 className="text-2xl font-semibold">Facturas</h1>
      <p className="mt-1 max-w-prose text-ink-soft">
        Subí un PDF o la foto de un recibo. El sistema lo lee, extrae los campos y
        marca los que necesitan que alguien los confirme.
      </p>

      <label className="mt-6 flex cursor-pointer items-center gap-3 border border-dashed border-rule-strong px-4 py-6 hover:bg-paper-sunk">
        <input
          type="file"
          accept=".pdf,.jpg,.jpeg,.png,.tiff"
          className="sr-only"
          disabled={uploading}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) upload(file);
            e.target.value = "";
          }}
        />
        <span className="text-ink">
          {uploading ? "Subiendo…" : "Elegí un archivo para procesar"}
        </span>
        <span className="text-sm text-ink-muted">PDF, JPG, PNG o TIFF</span>
      </label>

      {error && (
        <p className="mt-4 bg-flag-wash px-3 py-2 text-sm text-flag">
          No se pudo completar la operación. {error}
        </p>
      )}

      {pending.length > 0 && (
        <p className="mt-8 text-ink">
          {pending.length} de {documents.length} documentos esperan revisión.
        </p>
      )}

      {loading ? (
        <p className="mt-8 text-ink-muted">Cargando…</p>
      ) : documents.length === 0 ? (
        <p className="mt-8 text-ink-muted">
          Todavía no hay documentos. Subí el primero para empezar.
        </p>
      ) : (
        <ul className="mt-4 divide-y divide-rule border-t border-rule">
          {documents.map((doc) => (
            <li key={doc.id}>
              <Link
                href={`/documents/${doc.id}`}
                className="flex items-baseline justify-between gap-4 py-3 hover:bg-paper-sunk"
              >
                <span className="truncate">{doc.filename}</span>
                <span
                  className={`shrink-0 text-sm ${
                    doc.status === "needs_review"
                      ? "text-flag"
                      : doc.status === "failed"
                        ? "text-flag"
                        : doc.status === "completed"
                          ? "text-verified"
                          : "text-ink-muted"
                  }`}
                >
                  {STATUS_LABEL[doc.status] ?? doc.status}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
