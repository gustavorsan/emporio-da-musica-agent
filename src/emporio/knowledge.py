"""Policy documents: Markdown parsing, FTS5 index construction and lexical retrieval."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import yaml

from emporio.text import tokenize

_HEADING_RE = re.compile(r"^## (\S+) (.+)$", re.MULTILINE)

# Column weights for bm25(): title, aliases, tags, body. Aliases are curated for how
# customers phrase things, which matters because FTS5 has no Portuguese stemmer.
_BM25_WEIGHTS = "5.0, 8.0, 2.0, 1.0"

MAX_PRIMARY_SECTIONS = 3
MAX_TOTAL_SECTIONS = 6

_STOPWORDS = frozenset(
    """
    a ao aos as ate com como da das de do dos e ela ele em entao era essa esse esta este
    eu foi for ha isso ja la lhe mais mas me meu meus minha minhas muito na nas no nos
    o oi ola os ou para pela pelas pelo pelos por pra pro qual quais quando que quem se
    sem ser sera seu sua sao so sobre tem tenho tudo um uma uns umas vcs voce voces
    gostaria queria quero posso pode podem preciso favor obrigado obrigada bom boa dia
    tarde noite vou fazer saber informar dizer loja emporio musica
    """.split()
)

SCHEMA = f"""
CREATE TABLE knowledge_sections (
    id             INTEGER PRIMARY KEY,
    doc_id         TEXT NOT NULL,
    section        TEXT NOT NULL UNIQUE,
    title          TEXT NOT NULL,
    body           TEXT NOT NULL,
    pages_json     TEXT NOT NULL,
    tags_json      TEXT NOT NULL,
    related_json   TEXT NOT NULL,
    assumptions_json TEXT NOT NULL,
    source         TEXT NOT NULL,
    manual_version TEXT NOT NULL
);
CREATE VIRTUAL TABLE knowledge_fts USING fts5(
    title, aliases, tags, body,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""


class KnowledgeError(ValueError):
    """Raised when a policy document does not follow the expected structure."""


@dataclass(frozen=True)
class Section:
    doc_id: str
    section: str
    title: str
    body: str
    pages: list[int]
    tags: list[str]
    aliases: list[str]
    related: list[str]
    assumptions: list[str]
    source: str
    manual_version: str


def _split_frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text("utf-8")
    parts = text.split("---\n", 2)
    if len(parts) != 3 or parts[0].strip():
        raise KnowledgeError(f"{path.name}: missing YAML frontmatter")
    return yaml.safe_load(parts[1]), parts[2]


def parse_document(path: Path) -> list[Section]:
    meta, content = _split_frontmatter(path)
    declared = {str(key): value for key, value in meta["sections"].items()}
    headings = list(_HEADING_RE.finditer(content))
    found = {match.group(1) for match in headings}
    if found != set(declared):
        raise KnowledgeError(
            f"{path.name}: headings and metadata differ: {sorted(found ^ set(declared))}"
        )
    sections = []
    for index, match in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(content)
        number = match.group(1)
        info = declared[number]
        sections.append(
            Section(
                doc_id=meta["id"],
                section=number,
                title=match.group(2).strip(),
                body=content[match.end() : end].strip(),
                pages=list(info["pages"]),
                tags=list(meta["tags"]),
                aliases=[str(alias) for alias in info.get("aliases", [])],
                related=[str(item) for item in info.get("related", [])],
                assumptions=list(info.get("assumptions", [])),
                source=meta["source"],
                manual_version=str(meta["manual_version"]),
            )
        )
    return sections


def load_sections(knowledge_dir: Path) -> list[Section]:
    paths = sorted(knowledge_dir.glob("*.md"))
    if not paths:
        raise KnowledgeError(f"No policy documents found in {knowledge_dir}")
    sections = [section for path in paths for section in parse_document(path)]
    known = {section.section for section in sections}
    for section in sections:
        unknown = set(section.related) - known
        if unknown:
            raise KnowledgeError(f"Section {section.section} relates to unknown {sorted(unknown)}")
    return sections


def build_index(conn: sqlite3.Connection, knowledge_dir: Path) -> None:
    """Create the policy tables; the index is always rebuilt from the Markdown files."""
    sections = load_sections(knowledge_dir)
    conn.executescript(SCHEMA)
    with conn:
        for section in sections:
            cursor = conn.execute(
                """
                INSERT INTO knowledge_sections (
                    doc_id, section, title, body, pages_json, tags_json,
                    related_json, assumptions_json, source, manual_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    section.doc_id,
                    section.section,
                    section.title,
                    section.body,
                    json.dumps(section.pages),
                    json.dumps(section.tags),
                    json.dumps(section.related),
                    json.dumps(section.assumptions, ensure_ascii=False),
                    section.source,
                    section.manual_version,
                ),
            )
            conn.execute(
                "INSERT INTO knowledge_fts (rowid, title, aliases, tags, body) VALUES (?, ?, ?, ?, ?)",
                (
                    cursor.lastrowid,
                    section.title,
                    " ; ".join(section.aliases),
                    " ".join(section.tags),
                    section.body,
                ),
            )


def _stem(token: str) -> str:
    """Drop the last characters as a crude stem, so "entregas" also finds "entregue"."""
    return token[: max(4, len(token) - 2)]


def build_match_query(question: str) -> str | None:
    """Turn free text into a safe MATCH expression.

    Passing the sentence straight to MATCH would AND all its terms and could break on
    reserved syntax, so each term is quoted, prefix-matched and OR-ed instead.
    """
    terms = dict.fromkeys(
        _stem(token)
        for token in tokenize(question)
        if len(token) >= 2 and token not in _STOPWORDS
    )
    if not terms:
        return None
    return " OR ".join(f'"{term}"*' for term in terms)


def _section_payload(row: sqlite3.Row, related_to: str | None) -> dict:
    payload = {
        "section": row["section"],
        "title": row["title"],
        "text": row["body"],
        "source": row["source"],
        "manual_version": row["manual_version"],
        "pages": json.loads(row["pages_json"]),
    }
    assumptions = json.loads(row["assumptions_json"])
    if assumptions:
        payload["assumptions"] = assumptions
    if related_to:
        payload["included_as_related_to"] = related_to
    return payload


def _ranked_ids(
    conn: sqlite3.Connection, match: str, tags: list[str] | None, limit: int
) -> list[sqlite3.Row]:
    rows = conn.execute(
        f"""
        SELECT s.*
        FROM knowledge_fts AS f
        JOIN knowledge_sections AS s ON s.id = f.rowid
        WHERE knowledge_fts MATCH ?
        ORDER BY bm25(knowledge_fts, {_BM25_WEIGHTS})
        """,
        (match,),
    ).fetchall()
    if tags:
        wanted = set(tags)
        rows = [row for row in rows if wanted & set(json.loads(row["tags_json"]))]
    return rows[:limit]


def search(conn: sqlite3.Connection, question: str, tags: list[str] | None = None) -> dict:
    """Retrieve the best sections plus the ones needed to complete their rules."""
    match = build_match_query(question)
    primary = _ranked_ids(conn, match, tags, MAX_PRIMARY_SECTIONS) if match else []
    # Tags come from the model and may be wrong, so they never block retrieval on their own.
    if match and tags and not primary:
        primary = _ranked_ids(conn, match, None, MAX_PRIMARY_SECTIONS)
    if not primary:
        return {
            "results": [],
            "guidance": (
                "Nenhuma seção do manual foi encontrada. Não responda com base em suposições: "
                "peça mais detalhes ou informe que essa informação não está disponível."
            ),
        }

    results = [_section_payload(row, None) for row in primary]
    included = {row["section"] for row in primary}
    for row in primary:
        for number in json.loads(row["related_json"]):
            if number in included or len(results) >= MAX_TOTAL_SECTIONS:
                continue
            related = conn.execute(
                "SELECT * FROM knowledge_sections WHERE section = ?", (number,)
            ).fetchone()
            results.append(_section_payload(related, row["section"]))
            included.add(number)
    return {"results": results}
