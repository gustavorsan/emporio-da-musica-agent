"""Policy retrieval: known questions, paraphrases, complementary rules and misses."""

from __future__ import annotations

import pytest

from emporio import config
from emporio.knowledge import build_match_query, load_sections, search


def _sections(conn, question, tags=None):
    return [item["section"] for item in search(conn, question, tags)["results"]]


@pytest.mark.parametrize(
    "question, expected",
    [
        ("Qual o endereço da loja?", "1.2"),
        ("Onde fica a loja?", "1.2"),
        ("Qual o horário de sábado?", "2"),
        ("Vocês abrem domingo?", "2"),
        ("Quais as formas de pagamento?", "3"),
        ("Posso parcelar em quantas vezes?", "3.1"),
        ("Me arrependi da minha compra, posso devolver meu pedido?", "4.1"),
        ("Quero devolver", "4.1"),
        ("Não gostei da compra", "4.1"),
        ("Comprei online, recebi há 5 dias e quero devolver.", "4.1"),
        ("Meu violão veio com defeito", "4.2"),
        ("Quero trocar por outra cor", "4.3"),
        ("Qual o valor do frete para Campo Grande?", "5.1"),
        ("Vocês entregam em outras cidades pelos Correios?", "5.2"),
        ("Como rastrear meu pedido?", "5.3"),
        ("Tem Black Friday?", "6.1"),
        ("Dá para acumular o desconto do PIX com a promoção?", "6.2"),
        ("Qual o WhatsApp de vocês?", "7"),
        ("Quero fazer uma reclamação", "7.3"),
        ("A garantia cobre por quanto tempo?", "8.1"),
        ("Quanto tempo de garantia do fabricante?", "8.2"),
        ("Quero excluir meus dados pessoais", "9"),
        ("Vocês vendem amplificador?", "1"),
    ],
)
def test_expected_section_is_retrieved(conn, question, expected):
    assert expected in _sections(conn, question)


def test_pix_question_brings_the_non_cumulative_rule(conn):
    assert "6.2" in _sections(conn, "Tem desconto no PIX?")


def test_regret_brings_the_ineligible_items(conn):
    assert "4.4" in _sections(conn, "Me arrependi da compra")


def test_related_sections_are_marked(conn):
    results = search(conn, "Quais as formas de pagamento?")["results"]
    related = [item for item in results if "included_as_related_to" in item]
    assert related


def test_results_carry_source_page_and_assumptions(conn):
    item = next(
        result
        for result in search(conn, "Qual o frete local?")["results"]
        if result["section"] == "5.1"
    )
    assert item["pages"] == [4]
    assert item["manual_version"] == "2.1"
    assert "políticas_da_loja.pdf" in item["source"]
    assert any("R$ 500,00" in text for text in item["assumptions"])


def test_results_do_not_expose_ranking_scores(conn):
    item = search(conn, "garantia")["results"][0]
    assert not {"score", "rank", "bm25"} & set(item)


def test_wrong_tag_does_not_block_retrieval(conn):
    assert "4.1" in _sections(conn, "Me arrependi da compra", tags=["garantia"])


def test_tag_narrows_when_it_has_matches(conn):
    sections = _sections(conn, "prazo", tags=["entrega"])
    assert sections[0].startswith("5.")


@pytest.mark.parametrize(
    "question", ["Qual a capital da França?", "Quem ganhou a Copa de 2002?", "oi", "???"]
)
def test_questions_without_evidence_return_guidance(conn, question):
    outcome = search(conn, question)
    assert outcome["results"] == []
    assert outcome["guidance"]


@pytest.mark.parametrize("hostile", ['" OR 1=1 --', "NEAR(a b) AND NOT *", "title:garantia^"])
def test_match_syntax_in_questions_is_harmless(conn, hostile):
    search(conn, hostile)


def test_match_query_has_no_implicit_and():
    assert " OR " in build_match_query("frete grátis Campo Grande")


def test_policy_text_preserves_exact_conditions():
    bodies = {section.section: section.body for section in load_sections(config.KNOWLEDGE_DIR)}
    assert "até 7 (sete) dias corridos após o recebimento" in bodies["4.1"]
    assert "em até 10 dias úteis" in bodies["4.1"]
    assert "exceto abaixo de R$ 50,00" in bodies["3.1"]
    assert "não se aplica sobre preços já promocionais" in bodies["6.2"]
    assert "Rua 14 de Maio, 3200" in bodies["1.2"]
    assert "Sábado: 09:00 às 13:00" in bodies["2"]
    assert "(67) 3321-4500" in bodies["7"]
    assert "contato@empóriodamusica.com.br" in bodies["1.2"]
