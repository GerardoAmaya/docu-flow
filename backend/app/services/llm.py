"""Anthropic client wrapper with cost tracking and a deterministic mock mode."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.models import LLMCall

logger = logging.getLogger(__name__)


class LLMError(Exception):
    pass


class TransientLLMError(LLMError):
    """Error que vale la pena reintentar: rate limit, sobrecarga, red."""


@dataclass
class LLMResponse:
    data: dict
    input_tokens: int
    output_tokens: int
    latency_ms: int
    was_mocked: bool


def _strip_code_fence(text: str) -> str:
    """Quita los ``` que el modelo a veces agrega pese a pedirle JSON puro."""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    return (fenced.group(1) if fenced else text).strip()


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Costo en USD segun los precios configurados en el .env.

    Si el modelo no esta en la tabla devolvemos 0: preferimos un cero visible
    a un numero inventado que despues alguien cite en una reunion.
    """
    price_in = settings.price_input_per_mtok.get(model, 0.0)
    price_out = settings.price_output_per_mtok.get(model, 0.0)
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


class LLMClient:
    """Envuelve la API de Anthropic y deja una fila en llm_calls por llamada."""

    def __init__(self, db: Session, document_id: uuid.UUID | None = None) -> None:
        self.db = db
        self.document_id = document_id
        self._client = None

    def _anthropic(self):
        if self._client is None:
            import anthropic

            if not settings.anthropic_api_key:
                raise LLMError(
                    "ANTHROPIC_API_KEY is empty. Set it in .env or keep MOCK_LLM=true."
                )
            self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        return self._client

    def complete_json(
        self,
        *,
        system: str,
        prompt: str,
        purpose: str,
        model: str | None = None,
        mock_result: dict | None = None,
    ) -> LLMResponse:
        """Pide una respuesta JSON al modelo y registra la llamada.

        En modo mock devuelve `mock_result` sin tocar la red, pero igual
        escribe la fila en llm_calls con was_mocked=True. Asi el dashboard
        muestra el conteo real de invocaciones aunque no se haya gastado nada.
        """
        model = model or settings.extraction_model
        started = time.perf_counter()

        if settings.mock_llm:
            latency_ms = int((time.perf_counter() - started) * 1000)
            self._record(purpose, model, 0, 0, latency_ms, was_mocked=True)
            return LLMResponse(
                data=mock_result or {},
                input_tokens=0,
                output_tokens=0,
                latency_ms=latency_ms,
                was_mocked=True,
            )

        try:
            message = self._call_api(system=system, prompt=prompt, model=model)
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            self._record(purpose, model, 0, 0, latency_ms, error=str(exc)[:2000])
            raise

        latency_ms = int((time.perf_counter() - started) * 1000)
        raw_text = "".join(
            block.text for block in message.content if block.type == "text"
        )

        try:
            data = json.loads(_strip_code_fence(raw_text))
        except json.JSONDecodeError as exc:
            self._record(
                purpose,
                model,
                message.usage.input_tokens,
                message.usage.output_tokens,
                latency_ms,
                error=f"Invalid JSON: {exc}",
            )
            raise LLMError(f"Model returned malformed JSON: {raw_text[:400]}") from exc

        self._record(
            purpose,
            model,
            message.usage.input_tokens,
            message.usage.output_tokens,
            latency_ms,
        )
        return LLMResponse(
            data=data,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            latency_ms=latency_ms,
            was_mocked=False,
        )

    @retry(
        retry=retry_if_exception_type(TransientLLMError),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        reraise=True,
    )
    def _call_api(self, *, system: str, prompt: str, model: str):
        import anthropic

        try:
            return self._anthropic().messages.create(
                model=model,
                max_tokens=settings.llm_max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
        except (anthropic.RateLimitError, anthropic.APIStatusError) as exc:
            # 429 y 5xx son transitorios; el backoff exponencial los absorbe.
            status = getattr(exc, "status_code", None)
            if status == 429 or (status is not None and status >= 500):
                raise TransientLLMError(str(exc)) from exc
            raise LLMError(str(exc)) from exc
        except anthropic.APIConnectionError as exc:
            raise TransientLLMError(str(exc)) from exc

    def _record(
        self,
        purpose: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        *,
        was_mocked: bool = False,
        error: str | None = None,
    ) -> None:
        self.db.add(
            LLMCall(
                document_id=self.document_id,
                purpose=purpose,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=estimate_cost(model, input_tokens, output_tokens),
                latency_ms=latency_ms,
                was_mocked=was_mocked,
                error=error,
            )
        )
        self.db.commit()
