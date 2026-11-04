"""Tool contracts exposed to the model and their validated execution.

Schemas use only optional-null fields and no numeric keywords, which keeps them valid for
OpenAI strict mode; ranges are enforced by the validators below. The session customer is
not a field of any model, so the model has no way to choose whose orders it reads.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

import openai
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from emporio import catalog, knowledge, orders, returns
from emporio.catalog import ToolInputError

# Far above any catalog price; larger values only reach the Decimal conversion as errors.
MAX_PRICE_BRL = 1_000_000_000
# A century is far beyond any purchase, and keeps date subtraction from overflowing.
MAX_DAYS_AGO = 36_500
MAX_CONTACT_CHARS = 254
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

_LOOKUP_FAILED = {
    "error": "lookup_failed",
    "guidance": "A consulta falhou. Informe a limitação ao cliente e não apresente dados de memória.",
}


class _ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchProducts(_ToolArgs):
    """Busca produtos no catálogo por texto, categoria e faixa de preço.

    Use para descobrir produtos e seus IDs. O preço efetivo considera promoção ativa e,
    com payment=pix, o desconto de PIX. Exige ao menos texto, categoria ou faixa de preço.
    Em query, use só tipo, marca, modelo ou característica do instrumento. Para "o mais
    barato" ou "mais barato que", use sort=price_asc e max_price, sem colocar isso em query.
    Em category, use só um nome de categoria que você já tenha visto em um resultado; na
    dúvida, deixe null e busque por query.
    """

    query: str | None = None
    category: str | None = None
    min_price: float | None = None
    max_price: float | None = None
    only_available: bool | None = None
    sort: Literal["relevance", "price_asc", "price_desc"] | None = None
    payment: Literal["pix", "debit", "boleto", "credit"] | None = None
    limit: int | None = None

    @field_validator("min_price", "max_price")
    @classmethod
    def _price_in_range(cls, value: float | None) -> float | None:
        if value is not None and not (math.isfinite(value) and 0 <= value <= MAX_PRICE_BRL):
            raise ValueError(f"deve estar entre 0 e {MAX_PRICE_BRL}")
        return value

    @field_validator("limit")
    @classmethod
    def _limit_in_range(cls, value: int | None) -> int | None:
        if value is not None and not 1 <= value <= catalog.MAX_LIMIT:
            raise ValueError(f"deve estar entre 1 e {catalog.MAX_LIMIT}")
        return value


class GetProductDetails(_ToolArgs):
    """Retorna descrição, especificações, status, estoque e preço atual de um produto."""

    product_id: int


class CalculateOffer(_ToolArgs):
    """Calcula o valor final de um produto em uma forma de pagamento, com suas condições.

    Use sempre que o cliente perguntar sobre PIX, débito, boleto ou parcelamento. Em
    payment=credit, informe installments ou omita para obter o máximo de parcelas elegível.
    """

    product_id: int
    payment: Literal["pix", "debit", "boleto", "credit"]
    installments: int | None = None


class GetOrderStatus(_ToolArgs):
    """Consulta pedidos do cliente identificado nesta sessão.

    Com order_id, retorna o pedido; sem order_id, lista os pedidos do cliente. O cliente é
    definido pela aplicação e não pode ser escolhido nem alterado.
    """

    order_id: int | None = None


class VerifyIdentity(_ToolArgs):
    """Confirma quem é o cliente, para liberar a consulta dos pedidos dele nesta conversa.

    Use somente com o e-mail e o telefone que o próprio cliente informou nesta conversa, depois
    de pedir os dois. Nunca invente, complete nem reutilize valores vistos em outro lugar. O
    resultado nunca traz dados de contato.
    """

    email: str
    phone: str

    @field_validator("email", "phone")
    @classmethod
    def _bounded(cls, value: str) -> str:
        # Enforced here because strict schemas do not allow length keywords.
        if len(value) > MAX_CONTACT_CHARS:
            raise ValueError(f"deve ter até {MAX_CONTACT_CHARS} caracteres")
        return value


class CheckReturnWindow(_ToolArgs):
    """Calcula, com a data de hoje, se o prazo de troca ou devolução está aberto.

    Use sempre que o cliente perguntar se ainda está no prazo. reason: regret (arrependimento),
    defect (defeito de fabricação) ou preference (troca por preferência). purchase_channel: online
    ou store (loja física), só se o cliente tiver dito; caso contrário, null. Com número de pedido
    do cliente identificado, passe order_id para usar
    a data de compra registrada. Datas exatas vão em purchase_date e received_date, no formato
    AAAA-MM-DD. Para "ontem" ou "há 5 dias", use purchase_days_ago ou received_days_ago (hoje = 0,
    ontem = 1). Previsão de entrega não é data de recebimento.
    """

    reason: Literal["regret", "defect", "preference"]
    purchase_channel: Literal["online", "store"] | None = None
    order_id: int | None = None
    purchase_date: str | None = None
    purchase_days_ago: int | None = None
    received_date: str | None = None
    received_days_ago: int | None = None

    @field_validator("purchase_date", "received_date")
    @classmethod
    def _iso_date(cls, value: str | None) -> str | None:
        # fromisoformat alone also accepts week dates and compact forms the schema does not promise.
        if value is not None:
            if not _ISO_DATE.fullmatch(value):
                raise ValueError("use o formato AAAA-MM-DD")
            date.fromisoformat(value)
        return value

    @field_validator("purchase_days_ago", "received_days_ago")
    @classmethod
    def _days_in_range(cls, value: int | None) -> int | None:
        if value is not None and not 0 <= value <= MAX_DAYS_AGO:
            raise ValueError(f"deve estar entre 0 e {MAX_DAYS_AGO}")
        return value

    @model_validator(mode="after")
    def _one_form_per_date(self) -> "CheckReturnWindow":
        if self.purchase_date is not None and self.purchase_days_ago is not None:
            raise ValueError("informe purchase_date ou purchase_days_ago, não ambos")
        if self.received_date is not None and self.received_days_ago is not None:
            raise ValueError("informe received_date ou received_days_ago, não ambos")
        return self


class SearchKnowledge(_ToolArgs):
    """Busca no manual da loja: endereço, contatos, horários, pagamento, parcelamento, regras de promoção, trocas, devolução, frete, rastreio, garantia, atendimento e privacidade."""

    question: str
    tags: (
        list[
            Literal[
                "loja",
                "pagamento",
                "promocao",
                "entrega",
                "troca",
                "devolucao",
                "garantia",
                "atendimento",
                "privacidade",
            ]
        ]
        | None
    ) = None


_MODELS: dict[str, type[_ToolArgs]] = {
    "search_products": SearchProducts,
    "get_product_details": GetProductDetails,
    "calculate_offer": CalculateOffer,
    "verify_identity": VerifyIdentity,
    "get_order_status": GetOrderStatus,
    "check_return_window": CheckReturnWindow,
    "search_knowledge": SearchKnowledge,
}

TOOL_SCHEMAS = [
    openai.pydantic_function_tool(model, name=name) for name, model in _MODELS.items()
]


@dataclass(frozen=True)
class ToolContext:
    conn: sqlite3.Connection
    customer_id: int | None
    today: date
    identity_attempts_left: int = 0
    # Everything the customer typed in this conversation, to tell given details from invented ones.
    customer_text: str = ""


@dataclass
class ToolOutcome:
    payload: dict
    ok: bool
    error: str | None = None
    sources: list[str] = field(default_factory=list)
    products: dict[int, str] = field(default_factory=dict)
    # Set only when verify_identity succeeded; the agent adopts it as the session customer.
    identified: orders.Customer | None = None


def _resolve_date(text: str | None, days_ago: int | None, today: date) -> date | None:
    if text is not None:
        return date.fromisoformat(text)
    return None if days_ago is None else today - timedelta(days=days_ago)


def _run(name: str, args: _ToolArgs, ctx: ToolContext) -> dict:
    if isinstance(args, SearchProducts):
        return catalog.search_products(ctx.conn, **args.model_dump())
    if isinstance(args, GetProductDetails):
        return catalog.get_product_details(ctx.conn, args.product_id)
    if isinstance(args, CalculateOffer):
        return catalog.calculate_offer(ctx.conn, args.product_id, args.payment, args.installments)
    if isinstance(args, VerifyIdentity):
        return orders.verify_identity(
            ctx.conn, ctx.customer_id, ctx.identity_attempts_left, args.email, args.phone, ctx.customer_text
        )
    if isinstance(args, GetOrderStatus):
        return orders.get_order_status(ctx.conn, ctx.customer_id, args.order_id, ctx.today)
    if isinstance(args, CheckReturnWindow):
        return returns.check_return_window(
            ctx.conn,
            ctx.customer_id,
            ctx.today,
            args.reason,
            args.purchase_channel,
            args.order_id,
            _resolve_date(args.purchase_date, args.purchase_days_ago, ctx.today),
            _resolve_date(args.received_date, args.received_days_ago, ctx.today),
        )
    if isinstance(args, SearchKnowledge):
        return knowledge.search(ctx.conn, args.question, args.tags)
    raise AssertionError(f"Unhandled tool: {name}")


def _discussed_products(payload: dict) -> dict[int, str]:
    entries = [payload, *payload.get("products", []), *payload.get("unavailable_matches", [])]
    return {
        entry["product_id"]: entry["name"]
        for entry in entries
        if "product_id" in entry and "name" in entry
    }


def execute(name: str, raw_arguments: str, ctx: ToolContext) -> ToolOutcome:
    """Validate and run one tool call; failures become results the model must report honestly."""
    model = _MODELS.get(name)
    if model is None:
        return ToolOutcome({"error": "unknown_tool", "tool": name}, ok=False, error="unknown_tool")
    try:
        args = model.model_validate(json.loads(raw_arguments or "{}"))
    except (json.JSONDecodeError, ValidationError) as exc:
        details = exc.errors(include_url=False, include_input=False) if isinstance(exc, ValidationError) else str(exc)
        payload = {"error": "invalid_arguments", "details": json.loads(json.dumps(details, default=str))}
        return ToolOutcome(payload, ok=False, error="invalid_arguments")
    try:
        payload = _run(name, args, ctx)
    except ToolInputError as exc:
        return ToolOutcome({"error": "invalid_request", "message": str(exc)}, ok=False, error="invalid_request")
    except sqlite3.Error:
        # The exception text stays out of the conversation; the log records the failure.
        return ToolOutcome(dict(_LOOKUP_FAILED), ok=False, error="lookup_failed")
    except Exception as exc:  # noqa: BLE001
        # A faulty tool must never end the customer's session; the type goes to the log only.
        return ToolOutcome(dict(_LOOKUP_FAILED), ok=False, error=f"unexpected:{type(exc).__name__}")
    identified = payload.pop("session_customer", None)
    sources = [item["section"] for item in payload.get("results", [])]
    if "policy_section" in payload:
        sources.append(payload["policy_section"])
    error = payload.get("error")
    return ToolOutcome(
        payload,
        ok=error is None,
        error=error,
        sources=sources,
        products=_discussed_products(payload),
        identified=identified,
    )
