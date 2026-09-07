"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { use } from "react";
import {
  api,
  DocumentSummary,
  ExtractedFieldData,
  FIELD_LABELS,
  NUMERIC_FIELDS,
  PageSummary,
} from "@/lib/api";

const FIELD_ORDER = [
  "vendor_name",
  "vendor_tax_id",
  "buyer_name",
  "buyer_tax_id",
  "invoice_number",
  "issue_date",
  "due_date",
  "currency",
  "subtotal",
  "tax_amount",
  "total",
];

export default function ReviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);

  const [document, setDocument] = useState<DocumentSummary | null>(null);
  const [pages, setPages] = useState<PageSummary[]>([]);
  const [fields, setFields] = useState<ExtractedFieldData[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [doc, pageList, fieldList] = await Promise.all([
          api.getDocument(id),
          api.listPages(id),
          api.listFields(id),
        ]);
        if (cancelled) return;
        setDocument(doc);
        setPages(pageList.items);
        setFields(fieldList.items);
        // Abrimos en el primer campo marcado: es donde el revisor tiene que
        // mirar, y le ahorra buscarlo en la lista.
        setSelected(fieldList.items.find((f) => f.needs_review)?.id ?? null);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [id]);

  const ordered = useMemo(() => {
    const index = new Map(FIELD_ORDER.map((name, i) => [name, i]));
    return [...fields].sort(
      (a, b) => (index.get(a.field_name) ?? 99) - (index.get(b.field_name) ?? 99),
    );
  }, [fields]);

  const activeField = ordered.find((f) => f.id === selected) ?? null;
  const activePage = activeField?.page_number ?? pages[0]?.page_number ?? 1;
  const pendingCount = fields.filter((f) => f.needs_review).length;

  async function save(field: ExtractedFieldData) {
    const value = draft[field.id] ?? field.corrected_value ?? field.value_text ?? "";
    setSaving(field.id);
    setError(null);
    try {
      const updated = await api.correctField(field.id, value);
      setFields((prev) => prev.map((f) => (f.id === updated.id ? updated : f)));
      setDraft((prev) => {
        const next = { ...prev };
        delete next[field.id];
        return next;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(null);
    }
  }

  if (loading) {
    return <p className="p-8 text-ink-muted">Cargando documento…</p>;
  }

  if (error && !document) {
    return (
      <div className="p-8">
        <p className="text-flag">No se pudo cargar el documento.</p>
        <p className="mt-2 text-sm text-ink-muted">{error}</p>
        <Link href="/" className="mt-4 inline-block text-link underline">
          Volver a la lista
        </Link>
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      <header className="border-b border-rule px-6 py-4">
        <Link href="/" className="text-sm text-link hover:underline">
          Documentos
        </Link>
        <div className="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <h1 className="text-xl font-semibold">{document?.filename}</h1>
          {pendingCount > 0 ? (
            <span className="rounded-sm bg-flag-wash px-2 py-0.5 text-sm text-flag">
              {pendingCount} {pendingCount === 1 ? "campo necesita" : "campos necesitan"} revisión
            </span>
          ) : (
            <span className="rounded-sm bg-verified-wash px-2 py-0.5 text-sm text-verified">
              Todo verificado
            </span>
          )}
        </div>
      </header>

      <div className="grid gap-0 lg:grid-cols-[1.1fr_1fr]">
        <section className="border-rule p-6 lg:border-r">
          <PageWithHighlight
            documentId={id}
            pageNumber={activePage}
            bbox={activeField?.bbox ?? null}
            label={activeField ? FIELD_LABELS[activeField.field_name] : null}
          />
        </section>

        <section className="p-6">
          {error && (
            <p className="mb-4 rounded-sm bg-flag-wash px-3 py-2 text-sm text-flag">
              No se guardó el cambio. {error}
            </p>
          )}

          <ul className="divide-y divide-rule">
            {ordered.map((field) => (
              <FieldRow
                key={field.id}
                field={field}
                isSelected={field.id === selected}
                draftValue={draft[field.id]}
                isSaving={saving === field.id}
                onSelect={() => setSelected(field.id)}
                onChange={(value) =>
                  setDraft((prev) => ({ ...prev, [field.id]: value }))
                }
                onSave={() => save(field)}
              />
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}

function PageWithHighlight({
  documentId,
  pageNumber,
  bbox,
  label,
}: {
  documentId: string;
  pageNumber: number;
  bbox: { x: number; y: number; w: number; h: number } | null;
  label: string | null;
}) {
  return (
    <figure className="sticky top-6">
      <div className="relative overflow-hidden border border-rule bg-paper-sunk">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={api.pageImageUrl(documentId, pageNumber)}
          alt={`Página ${pageNumber} del documento`}
          className="block w-full"
        />
        {bbox && (
          // Las coordenadas vienen normalizadas 0-1, asi que el overlay escala
          // solo con la imagen sin importar el zoom ni el DPI del render.
          <div
            className="pointer-events-none absolute border-2 border-flag bg-flag/15"
            style={{
              left: `${bbox.x * 100}%`,
              top: `${bbox.y * 100}%`,
              width: `${bbox.w * 100}%`,
              height: `${bbox.h * 100}%`,
            }}
          />
        )}
      </div>
      <figcaption className="mt-2 text-sm text-ink-muted">
        {bbox
          ? `Página ${pageNumber} · zona resaltada: ${label}`
          : `Página ${pageNumber} · este campo no tiene una zona localizada en la imagen`}
      </figcaption>
    </figure>
  );
}

function FieldRow({
  field,
  isSelected,
  draftValue,
  isSaving,
  onSelect,
  onChange,
  onSave,
}: {
  field: ExtractedFieldData;
  isSelected: boolean;
  draftValue: string | undefined;
  isSaving: boolean;
  onSelect: () => void;
  onChange: (value: string) => void;
  onSave: () => void;
}) {
  const stored = field.corrected_value ?? field.value_text ?? "";
  const value = draftValue ?? stored;
  const dirty = draftValue !== undefined && draftValue !== stored;
  const isNumeric = NUMERIC_FIELDS.has(field.field_name);

  return (
    <li
      className={`py-3 pl-3 ${isSelected ? "border-l-2 border-l-link bg-paper-sunk" : "border-l-2 border-l-transparent"}`}
    >
      <button
        type="button"
        onClick={onSelect}
        className="flex w-full items-baseline justify-between gap-3 text-left"
      >
        <span className="text-sm text-ink-soft">
          {FIELD_LABELS[field.field_name] ?? field.field_name}
        </span>
        <ConfidenceTag field={field} />
      </button>

      <div className="mt-2 flex items-center gap-2 pr-3">
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onFocus={onSelect}
          placeholder="Sin dato"
          className={`w-full border border-rule bg-paper px-2 py-1.5 text-ink placeholder:text-ink-muted ${isNumeric ? "figure text-right" : ""}`}
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

function ConfidenceTag({ field }: { field: ExtractedFieldData }) {
  if (field.reviewed_at) {
    return <span className="text-sm text-verified">Corregido</span>;
  }
  if (field.value_text === null) {
    return <span className="text-sm text-ink-muted">No encontrado</span>;
  }
  const percent = Math.round(field.confidence * 100);
  return (
    <span className={`figure text-sm ${field.needs_review ? "text-flag" : "text-ink-muted"}`}>
      {percent}%
    </span>
  );
}
