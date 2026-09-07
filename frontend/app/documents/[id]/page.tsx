"use client";

import { Suspense, use, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  BBox,
  DocumentSummary,
  ExtractedFieldData,
  FIELD_LABELS,
  NUMERIC_FIELDS,
  PageSummary,
  api,
} from "@/lib/api";

const FIELD_ORDER = [
  "vendor_name", "vendor_tax_id", "buyer_name", "buyer_tax_id",
  "invoice_number", "issue_date", "due_date", "currency",
  "subtotal", "tax_amount", "total",
];

export default function ReviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <Suspense fallback={<p className="p-8 text-ink-muted">Cargando documento…</p>}>
      <Review id={id} />
    </Suspense>
  );
}

function Review({ id }: { id: string }) {
  const search = useSearchParams();

  const [document, setDocument] = useState<DocumentSummary | null>(null);
  const [pages, setPages] = useState<PageSummary[]>([]);
  const [fields, setFields] = useState<ExtractedFieldData[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Una cita del chat llega con la zona a resaltar en la URL. Mientras esa
  // marca esta activa manda sobre el campo seleccionado.
  const citedBox = parseBBox(search.get("bbox"));
  const citedPage = Number(search.get("page")) || null;
  const [showingCitation, setShowingCitation] = useState(Boolean(citedBox));

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [doc, pageList, fieldList] = await Promise.all([
          api.getDocument(id), api.listPages(id), api.listFields(id),
        ]);
        if (cancelled) return;
        setDocument(doc);
        setPages(pageList.items);
        setFields(fieldList.items);
        setSelected(fieldList.items.find((f) => f.needs_review)?.id ?? null);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [id]);

  const ordered = useMemo(() => {
    const index = new Map(FIELD_ORDER.map((n, i) => [n, i]));
    return [...fields].sort(
      (a, b) => (index.get(a.field_name) ?? 99) - (index.get(b.field_name) ?? 99),
    );
  }, [fields]);

  const activeField = ordered.find((f) => f.id === selected) ?? null;
  const highlight = showingCitation && citedBox ? citedBox : activeField?.bbox ?? null;
  const activePage =
    (showingCitation ? citedPage : activeField?.page_number) ?? pages[0]?.page_number ?? 1;
  const pending = fields.filter((f) => f.needs_review).length;

  async function save(field: ExtractedFieldData) {
    const value = draft[field.id] ?? field.corrected_value ?? field.value_text ?? "";
    setSaving(field.id);
    setError(null);
    try {
      const updated = await api.correctField(field.id, value);
      setFields((prev) => prev.map((f) => (f.id === updated.id ? updated : f)));
      setDraft((prev) => { const n = { ...prev }; delete n[field.id]; return n; });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(null);
    }
  }

  if (loading) return <p className="p-8 text-ink-muted">Cargando documento…</p>;

  if (error && !document) {
    return (
      <div className="p-8">
        <p className="text-flag">No se pudo cargar el documento.</p>
        <p className="mt-2 text-sm text-ink-muted">{error}</p>
        <Link href="/" className="mt-4 inline-block text-link underline">Volver</Link>
      </div>
    );
  }

  return (
    <div>
      <header className="border-b border-rule bg-paper-sunk px-6 py-4">
        <div className="mx-auto max-w-6xl">
          <Link href="/" className="text-sm text-link hover:underline">
            ← Todas las facturas
          </Link>
          <div className="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-2">
            <h1 className="text-xl font-semibold">{document?.filename}</h1>
            {pending > 0 ? (
              <span className="bg-flag-wash px-2 py-0.5 text-sm text-flag">
                {pending} {pending === 1 ? "campo necesita" : "campos necesitan"} revisión
              </span>
            ) : (
              <span className="bg-verified-wash px-2 py-0.5 text-sm text-verified">
                Todo verificado
              </span>
            )}
          </div>
        </div>
      </header>

      <div className="mx-auto grid max-w-6xl lg:grid-cols-[1.1fr_1fr]">
        <section className="border-rule p-6 lg:border-r">
          <figure className="sticky top-6">
            <div className="relative overflow-hidden border border-rule bg-paper-sunk">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={api.pageImageUrl(id, activePage)}
                alt={`Página ${activePage}`}
                className="block w-full"
              />
              {highlight && (
                // Los bbox vienen normalizados 0-1, así que como porcentajes de
                // CSS el recuadro escala solo con la imagen a cualquier zoom.
                <div
                  className={`pointer-events-none absolute border-2 ${showingCitation ? "border-link bg-link/15" : "border-flag bg-flag/15"}`}
                  style={{
                    left: `${highlight.x * 100}%`,
                    top: `${highlight.y * 100}%`,
                    width: `${highlight.w * 100}%`,
                    height: `${highlight.h * 100}%`,
                  }}
                />
              )}
            </div>
            <figcaption className="mt-2 flex items-baseline justify-between gap-3 text-sm text-ink-muted">
              <span>
                {showingCitation
                  ? "Zona citada en la respuesta"
                  : highlight
                    ? `Zona del campo ${activeField ? FIELD_LABELS[activeField.field_name] : ""}`
                    : "Este campo no tiene una zona localizada"}
              </span>
              {showingCitation && (
                <button
                  type="button"
                  onClick={() => setShowingCitation(false)}
                  className="shrink-0 text-link hover:underline"
                >
                  Quitar
                </button>
              )}
            </figcaption>
          </figure>
        </section>

        <section className="p-6">
          {error && (
            <p className="mb-4 bg-flag-wash px-3 py-2 text-sm text-flag">
              No se guardó el cambio. {error}
            </p>
          )}
          <ul className="divide-y divide-rule">
            {ordered.map((field) => (
              <FieldRow
                key={field.id}
                field={field}
                isSelected={field.id === selected && !showingCitation}
                draftValue={draft[field.id]}
                isSaving={saving === field.id}
                onSelect={() => { setSelected(field.id); setShowingCitation(false); }}
                onChange={(v) => setDraft((p) => ({ ...p, [field.id]: v }))}
                onSave={() => save(field)}
              />
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}

function FieldRow({
  field, isSelected, draftValue, isSaving, onSelect, onChange, onSave,
}: {
  field: ExtractedFieldData;
  isSelected: boolean;
  draftValue: string | undefined;
  isSaving: boolean;
  onSelect: () => void;
  onChange: (v: string) => void;
  onSave: () => void;
}) {
  const stored = field.corrected_value ?? field.value_text ?? "";
  const value = draftValue ?? stored;
  const dirty = draftValue !== undefined && draftValue !== stored;
  const isNumeric = NUMERIC_FIELDS.has(field.field_name);

  return (
    <li className={`border-l-2 py-3 pl-3 transition-colors ${isSelected ? "border-l-link bg-paper-sunk" : "border-l-transparent"}`}>
      <button
        type="button"
        onClick={onSelect}
        className="flex w-full items-baseline justify-between gap-3 text-left"
      >
        <span className="text-sm text-ink-soft">
          {FIELD_LABELS[field.field_name] ?? field.field_name}
        </span>
        <Confidence field={field} />
      </button>

      <div className="mt-2 flex items-center gap-2 pr-3">
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onFocus={onSelect}
          placeholder="Sin dato"
          className={`w-full border border-rule bg-paper px-2 py-1.5 placeholder:text-ink-muted ${isNumeric ? "figure text-right" : ""}`}
        />
        {dirty && (
          <button
            type="button"
            onClick={onSave}
            disabled={isSaving}
            className="shrink-0 bg-ink px-3 py-1.5 text-sm text-paper disabled:opacity-50"
          >
            {isSaving ? "Guardando" : "Guardar"}
          </button>
        )}
      </div>

      {field.corrected_value !== null && field.value_text !== field.corrected_value && (
        <p className="mt-1.5 pr-3 text-sm text-ink-muted">
          El modelo había leído{" "}
          <span className={isNumeric ? "figure" : ""}>{field.value_text || "nada"}</span>
        </p>
      )}
    </li>
  );
}

function Confidence({ field }: { field: ExtractedFieldData }) {
  if (field.reviewed_at) {
    return <span className="shrink-0 text-sm text-verified">Corregido</span>;
  }
  if (field.value_text === null) {
    return <span className="shrink-0 text-sm text-ink-muted">No encontrado</span>;
  }

  const percent = Math.round(field.confidence * 100);
  const flagged = field.needs_review;

  // Una barra hace comparable la confianza de un vistazo; el numero solo
  // obliga a leer fila por fila.
  return (
    <span className="flex shrink-0 items-center gap-2">
      <span
        aria-hidden
        className="block h-1 w-16 bg-rule"
      >
        <span
          className={`block h-full ${flagged ? "bg-flag" : "bg-verified"}`}
          style={{ width: `${Math.max(percent, 2)}%` }}
        />
      </span>
      <span className={`figure w-9 text-right text-sm ${flagged ? "text-flag" : "text-ink-muted"}`}>
        {percent}%
      </span>
    </span>
  );
}

function parseBBox(raw: string | null): BBox | null {
  if (!raw) return null;
  const parts = raw.split(",").map(Number);
  if (parts.length !== 4 || parts.some(Number.isNaN)) return null;
  const [x, y, w, h] = parts;
  return { x, y, w, h };
}
