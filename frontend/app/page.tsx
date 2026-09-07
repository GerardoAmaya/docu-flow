"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  API_URL,
  AggregateSummary,
  DocumentSummary,
  ReviewQueueItem,
  api,
  extraApi,
  money,
} from "@/lib/api";

const STATUS_LABEL: Record<string, string> = {
  pending: "En cola",
  ocr_running: "Leyendo",
  extracting: "Extrayendo",
  embedding: "Indexando",
  needs_review: "Necesita revisión",
  completed: "Listo",
  failed: "Falló",
};

const IN_PROGRESS = new Set(["pending", "ocr_running", "extracting", "embedding"]);

export default function HomePage() {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [queue, setQueue] = useState<ReviewQueueItem[]>([]);
  const [summary, setSummary] = useState<AggregateSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [docs, q] = await Promise.all([api.listDocuments(), extraApi.reviewQueue()]);
      setDocuments(docs.items);
      setQueue(q.items);
      setError(null);
      // El resumen puede no existir si todavía no se extrajo ninguna factura.
      try {
        setSummary(await extraApi.summary());
      } catch {
        setSummary(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Mientras haya documentos en proceso refrescamos solos: el pipeline es
  // asíncrono y sin esto el usuario tendría que recargar a mano.
  const working = documents.some((d) => IN_PROGRESS.has(d.status));
  useEffect(() => {
    if (!working) return;
    const timer = setInterval(refresh, 4000);
    return () => clearInterval(timer);
  }, [working, refresh]);

  async function upload(file: File) {
    setUploading(true);
    setError(null);
    try {
      const body = new FormData();
      body.append("file", file);
      const response = await fetch(`${API_URL}/documents`, { method: "POST", body });
      if (!response.ok) throw new Error(await response.text());
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
    }
  }

  const pendingFields = queue.reduce((n, item) => n + item.pending_fields, 0);

  return (
    <main className="mx-auto max-w-6xl px-6 py-10">
      <h1 className="text-3xl font-semibold tracking-tight">Facturas</h1>
      <p className="mt-2 max-w-xl text-ink-soft">
        El sistema lee cada documento, extrae los campos y marca los que necesitan
        que alguien los confirme antes de darlos por buenos.
      </p>

      {summary && <LedgerStrip summary={summary} pendingFields={pendingFields} />}

      {queue.length > 0 && (
        <section className="mt-10">
          <h2 className="text-lg font-semibold">Esperan tu revisión</h2>
          <ul className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {queue.map((item) => (
              <li key={item.document_id}>
                <Link
                  href={`/documents/${item.document_id}`}
                  className="block border-l-2 border-flag bg-flag-wash px-4 py-3 transition-colors hover:bg-flag-wash/60"
                >
                  <span className="block truncate font-medium">{item.filename}</span>
                  <span className="mt-1 block text-sm text-flag">
                    {item.pending_fields}{" "}
                    {item.pending_fields === 1 ? "campo" : "campos"} · confianza mínima{" "}
                    <span className="figure">
                      {Math.round(item.lowest_confidence * 100)}%
                    </span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="mt-10">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h2 className="text-lg font-semibold">Todos los documentos</h2>
          <label className="cursor-pointer border border-rule-strong px-3 py-1.5 text-sm transition-colors hover:bg-paper-sunk">
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
            {uploading ? "Subiendo…" : "Subir un documento"}
          </label>
        </div>

        {error && (
          <p className="mt-4 bg-flag-wash px-3 py-2 text-sm text-flag">
            No se pudo completar la operación. {error}
          </p>
        )}

        {loading ? (
          <p className="mt-6 text-ink-muted">Cargando…</p>
        ) : documents.length === 0 ? (
          <p className="mt-6 text-ink-muted">
            Todavía no hay documentos. Subí una factura para empezar.
          </p>
        ) : (
          <ul className="mt-4 divide-y divide-rule border-y border-rule">
            {documents.map((doc) => (
              <li key={doc.id}>
                <Link
                  href={`/documents/${doc.id}`}
                  className="flex items-baseline gap-4 py-2.5 transition-colors hover:bg-paper-sunk"
                >
                  <span className="min-w-0 flex-1 truncate">{doc.filename}</span>
                  <span className="figure hidden text-sm text-ink-muted sm:block">
                    {doc.page_count ?? "—"} pág
                  </span>
                  <StatusTag status={doc.status} />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}

function LedgerStrip({
  summary,
  pendingFields,
}: {
  summary: AggregateSummary;
  pendingFields: number;
}) {
  // Una banda de cifras separadas por reglas, como el pie de un libro
  // contable. Los montos van en la cara monoespaciada para que se alineen.
  const incomplete = summary.invoice_count - summary.invoices_with_total;

  return (
    <section className="mt-8 border-y border-rule-strong">
      <dl className="grid divide-y divide-rule sm:grid-cols-2 sm:divide-x sm:divide-y-0 lg:grid-cols-4">
        <Figure label="Facturas leídas" value={String(summary.invoice_count)} />
        <Figure
          label="Total facturado"
          value={money(summary.total_sum)}
          note={
            incomplete > 0
              ? `sobre ${summary.invoices_with_total} de ${summary.invoice_count}`
              : undefined
          }
        />
        <Figure label="IVA acumulado" value={money(summary.tax_sum)} />
        <Figure
          label="Campos por revisar"
          value={String(pendingFields)}
          tone={pendingFields > 0 ? "flag" : "verified"}
        />
      </dl>
    </section>
  );
}

function Figure({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: string;
  note?: string;
  tone?: "flag" | "verified";
}) {
  const color =
    tone === "flag" ? "text-flag" : tone === "verified" ? "text-verified" : "text-ink";
  return (
    <div className="px-4 py-4 first:pl-0">
      <dt className="text-sm text-ink-muted">{label}</dt>
      <dd className={`figure mt-1 text-xl ${color}`}>{value}</dd>
      {note && <dd className="mt-0.5 text-sm text-ink-muted">{note}</dd>}
    </div>
  );
}

function StatusTag({ status }: { status: string }) {
  const color =
    status === "needs_review" || status === "failed"
      ? "text-flag"
      : status === "completed"
        ? "text-verified"
        : "text-ink-muted";
  return (
    <span className={`shrink-0 text-sm ${color}`}>
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}
