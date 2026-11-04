"""Catalog queries and offers. Every price shown is computed here, never by the model."""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal

from emporio import pricing
from emporio.pricing import Promotion, ResolvedPrice
from emporio.text import normalize, tokenize

DEFAULT_LIMIT = 5
MAX_LIMIT = 10

AVAILABILITY_LABELS = {
    "available": "disponível para venda",
    "out_of_stock": "temporariamente sem estoque",
    "coming_soon": "em breve, ainda não disponível para venda",
    "discontinued": "descontinuado, não faz mais parte do catálogo",
}

# Instrument words per normalized category name, including singular forms the names omit.
_CATEGORY_TERMS = {
    "guitarras": {"guitarra", "guitarras"},
    "baixos": {"baixo", "baixos", "contrabaixo", "contrabaixos"},
    "baterias e percussao": {"bateria", "baterias", "percussao"},
    "teclados e pianos": {"teclado", "teclados", "piano", "pianos", "sintetizador", "sintetizadores"},
    "violoes": {"violao", "violoes"},
    "instrumentos de sopro (madeiras)": {
        "sopro", "sopros", "flauta", "flautas", "clarinete", "clarinetes", "saxofone", "saxofones",
    },
    "instrumentos de sopro (metais)": {
        "sopro", "sopros", "trompete", "trompetes", "trombone", "trombones",
    },
    "cordas orquestrais": {
        "violino", "violinos", "viola", "violas", "violoncelo", "violoncelos", "orquestrais",
    },
    "ukuleles": {"ukulele", "ukuleles"},
}
_CATEGORY_WORDS = frozenset().union(*_CATEGORY_TERMS.values())

# Grammar and preference words: "violão barato" must not require the word "barato" in a product.
# Cheapness is expressed through sort and max_price instead.
_QUERY_STOPWORDS = frozenset(
    "a o as os um uma uns umas de da do das dos para pra com sem e em no na nos nas "
    "por que tem ter quero preciso algum alguma ate reais preco valor "
    "barato barata baratos baratas mais menos bom boa bons boas melhor melhores legal "
    "otimo otima qualidade custo beneficio opcao opcoes outro outra outros outras "
    "modelo modelos instrumento instrumentos".split()
)


class ToolInputError(ValueError):
    """Invalid request; the message is returned to the model so it can correct itself."""


def availability_of(status: str, stock: int) -> str:
    """A product is on sale only when active with stock, whatever else the stock column says."""
    if status == "discontinued":
        return "discontinued"
    if status == "coming_soon":
        return "coming_soon"
    return "available" if stock > 0 else "out_of_stock"


def _active_promotions(conn: sqlite3.Connection) -> dict[int, list[Promotion]]:
    promotions: dict[int, list[Promotion]] = {}
    rows = conn.execute(
        "SELECT product_id, discount_percent, description FROM promotions WHERE is_active = 1"
    )
    for row in rows:
        promotions.setdefault(row["product_id"], []).append(
            Promotion(Decimal(row["discount_percent"]), row["description"])
        )
    return promotions


def _products(conn: sqlite3.Connection, product_id: int | None = None) -> list[sqlite3.Row]:
    query = """
        SELECT p.*, c.name AS category_name
        FROM products AS p JOIN categories AS c ON c.category_id = p.category_id
    """
    if product_id is None:
        return conn.execute(query).fetchall()
    return conn.execute(f"{query} WHERE p.product_id = ?", (product_id,)).fetchall()


def _category_terms(category_name: str) -> set[str]:
    normalized = normalize(category_name)
    return _CATEGORY_TERMS.get(normalized, set()) | set(tokenize(category_name))


def _price_fields(price: ResolvedPrice) -> dict:
    fields: dict = {"list_price": pricing.format_brl(price.list_cents)}
    if price.source == "ambiguous":
        fields["price_status"] = "ambiguous_promotions"
        fields["price_note"] = (
            "Há mais de uma promoção ativa para este produto. Não informe preço promocional: "
            "oriente a confirmação com a equipe."
        )
        return fields
    fields["price"] = pricing.format_brl(price.final_cents)
    fields["price_cents"] = price.final_cents
    fields["price_condition"] = {
        "list": "preço de tabela",
        "promotion": "preço promocional",
        "pix": "preço de tabela com 5% de desconto no PIX",
    }[price.source]
    if price.promotion:
        fields["promotion"] = {
            "discount": pricing.format_percent(price.promotion.percent),
            "campaign": price.promotion.description,
        }
    return fields


def _summary(row: sqlite3.Row, price: ResolvedPrice) -> dict:
    availability = availability_of(row["status"], row["stock_quantity"])
    return {
        "product_id": row["product_id"],
        "name": row["name"],
        "category": row["category_name"],
        "availability": availability,
        "availability_label": AVAILABILITY_LABELS[availability],
        "stock_quantity": row["stock_quantity"],
        **_price_fields(price),
    }


def _searchable_words(row: sqlite3.Row) -> set[str]:
    specs = json.loads(row["specs_json"])
    spec_values = " ".join(
        str(value) for value in specs.values() if str(value).casefold() not in {"yes", "no"}
    )
    words = set(tokenize(f"{row['name']} {row['description'] or ''} {spec_values}"))
    return words | _category_terms(row["category_name"])


def _name_compact(row: sqlite3.Row) -> str:
    return "".join(tokenize(row["name"]))


def _token_matches(token: str, words: set[str], compact_name: str) -> bool:
    # The compact name lets "KAL700T" find "KAL-700T".
    return token in compact_name or any(word.startswith(token) for word in words)


def _row_matches(row: sqlite3.Row, token: str) -> bool:
    # An instrument word selects its category: a ukulele whose description mentions a violão
    # is not a violão, and "viola" must not match "violao" by prefix.
    if token in _CATEGORY_WORDS:
        return token in _category_terms(row["category_name"])
    return _token_matches(token, _searchable_words(row), _name_compact(row))


def _relevance(row: sqlite3.Row, tokens: list[str]) -> int:
    name_words = set(tokenize(row["name"]))
    compact = _name_compact(row)
    return sum(_token_matches(token, name_words, compact) for token in tokens)


def search_products(
    conn: sqlite3.Connection,
    query: str | None = None,
    category: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    only_available: bool | None = None,
    sort: str | None = None,
    payment: str | None = None,
    limit: int | None = None,
) -> dict:
    """Search the catalog, computing effective prices before filtering, sorting and limiting."""
    tokens = [token for token in tokenize(query or "") if token not in _QUERY_STOPWORDS]
    if not tokens and category is None and min_price is None and max_price is None:
        raise ToolInputError(
            "Informe ao menos um critério: texto de busca, categoria ou faixa de preço."
        )
    if min_price is not None and max_price is not None and min_price > max_price:
        raise ToolInputError("min_price não pode ser maior que max_price.")
    limit = DEFAULT_LIMIT if limit is None else limit
    only_available = True if only_available is None else only_available
    sort = sort or ("relevance" if tokens else "price_asc")

    rows = _products(conn)
    if category is not None:
        rows = _filter_by_category(rows, category)

    promotions = _active_promotions(conn)
    priced = [
        (row, pricing.resolve_price(row["price_cents"], promotions.get(row["product_id"], []), payment))
        for row in rows
    ]
    low = None if min_price is None else pricing.to_cents(min_price)
    high = None if max_price is None else pricing.to_cents(max_price)

    def effective(price: ResolvedPrice) -> int:
        return price.list_cents if price.final_cents is None else price.final_cents

    priced = [
        (row, price)
        for row, price in priced
        if (low is None or effective(price) >= low) and (high is None or effective(price) <= high)
    ]

    match_quality = "exact"
    if tokens:
        exact = [
            (row, price) for row, price in priced if all(_row_matches(row, token) for token in tokens)
        ]
        if exact:
            priced = exact
        else:
            # Near matches help when a model is not found, but must not be presented as it.
            match_quality = "partial"
            priced = [
                (row, price) for row, price in priced if any(_row_matches(row, token) for token in tokens)
            ]

    on_sale = [
        (row, price)
        for row, price in priced
        if availability_of(row["status"], row["stock_quantity"]) == "available"
    ]
    candidates = on_sale if only_available else priced

    if sort == "relevance":
        candidates.sort(key=lambda item: (-_relevance(item[0], tokens), effective(item[1])))
    else:
        candidates.sort(key=lambda item: effective(item[1]), reverse=sort == "price_desc")

    result: dict = {
        "total_matches": len(candidates),
        "returned": min(limit, len(candidates)),
        "price_filter_basis": (
            "Preço efetivo: promoção ativa, se houver; 5% no PIX somente quando payment=pix."
        ),
        "products": [_summary(row, price) for row, price in candidates[:limit]],
    }
    if match_quality == "partial":
        result["match_quality"] = "partial"
        result["match_note"] = (
            "Nenhum produto corresponde a todos os termos. Estes são apenas parecidos: "
            "não os apresente como o item pedido."
        )
    if only_available and not candidates and priced:
        result["unavailable_matches"] = [_summary(row, price) for row, price in priced[:3]]
    if not candidates and not priced:
        result["note"] = "Nada encontrado na base disponível. Não invente produtos."
    return result


def _filter_by_category(rows: list[sqlite3.Row], category: str) -> list[sqlite3.Row]:
    wanted = set(tokenize(category)) - _QUERY_STOPWORDS
    filtered = [row for row in rows if wanted & _category_terms(row["category_name"])]
    if not filtered:
        known = sorted({row["category_name"] for row in rows})
        raise ToolInputError(
            f"Categoria sem produtos na base: '{category}'. Categorias com produtos: {', '.join(known)}."
        )
    return filtered


def _get_row(conn: sqlite3.Connection, product_id: int) -> sqlite3.Row | None:
    try:
        rows = _products(conn, product_id)
    except OverflowError:
        # SQLite integers stop at 64 bits, so a longer number cannot be an existing product.
        return None
    return rows[0] if rows else None


def get_product_details(conn: sqlite3.Connection, product_id: int) -> dict:
    row = _get_row(conn, product_id)
    if row is None:
        return {"error": "product_not_found", "product_id": product_id}
    promotions = _active_promotions(conn).get(product_id, [])
    price = pricing.resolve_price(row["price_cents"], promotions)
    details = _summary(row, price)
    details["description"] = row["description"]
    details["specs"] = json.loads(row["specs_json"])
    if not promotions:
        details["active_promotion"] = None
    return details


def calculate_offer(
    conn: sqlite3.Connection, product_id: int, payment: str, installments: int | None = None
) -> dict:
    """Compute the final price and conditions of a product for one payment method."""
    if payment != "credit" and installments not in (None, 1):
        raise ToolInputError("Parcelas só se aplicam ao pagamento com crédito (payment=credit).")
    row = _get_row(conn, product_id)
    if row is None:
        return {"error": "product_not_found", "product_id": product_id}

    promotions = _active_promotions(conn).get(product_id, [])
    price = pricing.resolve_price(row["price_cents"], promotions, payment)
    offer = _summary(row, price)
    offer["payment"] = payment
    available = offer["availability"] == "available"
    offer["sale_available"] = available

    conditions = []
    if not available:
        conditions.append(
            "Produto fora de venda no momento: valores apenas informativos, não ofereça a compra."
        )
    if payment == "pix" and price.source == "promotion":
        conditions.append(
            "Desconto de PIX não aplicado: não é cumulativo com preço promocional (manual, seção 6.2)."
        )
    if price.source == "promotion":
        conditions.append("Apresente preço original, percentual de desconto e preço final.")
    if payment == "boleto":
        conditions.append("Boleto à vista, com compensação em até 3 dias úteis.")

    if payment == "credit" and price.final_cents is not None:
        offer["credit"] = _credit_terms(price.final_cents, installments)
    offer["conditions"] = conditions
    return offer


def _credit_terms(total_cents: int, installments: int | None) -> dict:
    if installments is not None and not 1 <= installments <= pricing.MAX_INSTALLMENTS:
        raise ToolInputError(f"Parcelas devem estar entre 1 e {pricing.MAX_INSTALLMENTS}.")
    best = pricing.max_eligible_installments(total_cents)
    terms: dict = {
        "interest": "sem juros",
        "max_eligible_installments": best,
        "max_eligible_plan": pricing.describe_plan(total_cents, best),
    }
    if installments is not None:
        eligible = pricing.is_installment_eligible(total_cents, installments)
        terms["requested_installments"] = installments
        terms["eligible"] = eligible
        terms["minimum_installment"] = pricing.format_brl(
            pricing.minimum_installment_cents(installments)
        )
        if eligible:
            terms["plan"] = pricing.describe_plan(total_cents, installments)
        else:
            terms["smallest_installment_if_split"] = pricing.format_brl(
                min(pricing.split_installments(total_cents, installments))
            )
    return terms
