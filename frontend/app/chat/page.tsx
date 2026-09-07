"use client";

import { useState } from "react";
import Link from "next/link";
import { ChatAnswer, extraApi } from "@/lib/api";

const EXAMPLES = [
  "¿Qué le compré a Distribuidora Cuscatlán?",
  "¿Cuál es la factura con el total más alto?",
  "¿Cuánto gasté en servicios de mantenimiento?",
];

interface Turn {
  question: string;
  answer: ChatAnswer | null;
  error: string | null;
}

export default function ChatPage() {
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [asking, setAsking] = useState(false);

  async function ask(text: string) {
    const trimmed = text.trim();
    if (!trimmed || asking) return;

    setQuestion("");
    setAsking(true);
    setTurns((prev) => [...prev, { question: trimmed, answer: null, error: null }]);

    try {
      const answer = await extraApi.ask(trimmed);
      setTurns((prev) =>
        prev.map((t, i) => (i === prev.length - 1 ? { ...t, answer } : t)),
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setTurns((prev) =>
        prev.map((t, i) => (i === prev.length - 1 ? { ...t, error: message } : t)),
      );
    } finally {
      setAsking(false);
    }
  }

  return (
    <main className="mx-auto max-w-3xl px-6 py-10">
      <h1 className="text-3xl font-semibold tracking-tight">Preguntar</h1>
      <p className="mt-2 text-ink-soft">
        Cada afirmación viene con la cita del documento de donde salió. Hacé clic en
        una cita para ver el renglón resaltado sobre la página original.
      </p>

      {turns.length === 0 && (
        <ul className="mt-6 flex flex-wrap gap-2">
          {EXAMPLES.map((example) => (
            <li key={example}>
              <button
                type="button"
                onClick={() => ask(example)}
                className="border border-rule px-3 py-1.5 text-sm text-ink-soft transition-colors hover:bg-paper-sunk hover:text-ink"
              >
                {example}
              </button>
            </li>
          ))}
        </ul>
      )}

      <ol className="mt-8 space-y-8">
        {turns.map((turn, index) => (
          <li key={index}>
            <p className="border-l-2 border-ink pl-4 text-lg">{turn.question}</p>

            {turn.error ? (
              <p className="mt-3 bg-flag-wash px-3 py-2 text-sm text-flag">
                No se pudo responder. {turn.error}
              </p>
            ) : turn.answer ? (
              <Answer answer={turn.answer} />
            ) : (
              <p className="mt-3 text-ink-muted">Buscando en los documentos…</p>
            )}
          </li>
        ))}
      </ol>

      <div className="mt-8 flex gap-2">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") ask(question);
          }}
          placeholder="Preguntá algo sobre tus facturas"
          className="w-full border border-rule bg-paper px-3 py-2.5 placeholder:text-ink-muted"
        />
        <button
          type="button"
          onClick={() => ask(question)}
          disabled={asking || !question.trim()}
          className="shrink-0 bg-ink px-5 py-2.5 text-paper transition-opacity disabled:opacity-40"
        >
          {asking ? "Buscando" : "Preguntar"}
        </button>
      </div>
    </main>
  );
}

function Answer({ answer }: { answer: ChatAnswer }) {
  const partialAggregate = answer.is_aggregate_question && !answer.covers_full_corpus;

  return (
    <div className="mt-4">
      <p className="whitespace-pre-wrap leading-relaxed">{answer.answer}</p>

      {partialAggregate && (
        // El RAG solo ve los fragmentos que recupero la busqueda. Para sumas y
        // conteos eso significa responder sobre una muestra, y el usuario tiene
        // que saberlo antes de usar el numero.
        <p className="mt-3 border-l-2 border-flag bg-flag-wash px-3 py-2 text-sm text-flag">
          Esta respuesta cubre solo los {answer.retrieved_chunks} fragmentos
          recuperados, no todas las facturas. Para sumas exactas, mirá el total en la
          página de facturas.
        </p>
      )}

      {answer.citations.length > 0 && (
        <div className="mt-5">
          <p className="text-sm text-ink-muted">
            {answer.citations.length}{" "}
            {answer.citations.length === 1 ? "cita verificada" : "citas verificadas"}
            {answer.discarded_citations > 0 &&
              ` · ${answer.discarded_citations} descartadas por no encontrarse en el original`}
          </p>
          <ul className="mt-2 divide-y divide-rule border-y border-rule">
            {answer.citations.map((citation, index) => (
              <li key={index}>
                <Link
                  href={citationHref(citation)}
                  className="block py-2.5 transition-colors hover:bg-paper-sunk"
                >
                  <span className="block text-ink">“{citation.quote}”</span>
                  <span className="mt-1 block text-sm text-link">
                    {citation.filename}
                    {citation.page_number ? `, página ${citation.page_number}` : ""}
                    {citation.bbox ? " · ver en el documento" : ""}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}

      {!answer.sufficient_context && answer.citations.length === 0 && (
        <p className="mt-3 text-sm text-ink-muted">
          Los documentos cargados no contienen esa información.
        </p>
      )}
    </div>
  );
}

/** Enlaza al documento pasando la zona a resaltar en la URL. */
function citationHref(citation: { document_id: string; page_number: number | null; bbox: { x: number; y: number; w: number; h: number } | null }): string {
  const params = new URLSearchParams();
  if (citation.page_number) params.set("page", String(citation.page_number));
  if (citation.bbox) {
    const { x, y, w, h } = citation.bbox;
    params.set("bbox", [x, y, w, h].join(","));
  }
  const query = params.toString();
  return `/documents/${citation.document_id}${query ? `?${query}` : ""}`;
}
