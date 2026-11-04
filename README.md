# Customer service agent — Empório da Música

**English** | [Português](README.pt-BR.md)

Python prototype of a text-based customer service agent for Empório da Música, a fictional musical instrument store in Campo Grande, Brazil. The agent answers questions about products, prices, availability, orders and store policies, in a conversation on the command line or on a local web page.

**The central idea:** the model interprets the message and writes the reply; prices, discounts, installments, availability, exchange and return deadlines, and order access are resolved in Python.

The agent, its policies and the example conversations are in Brazilian Portuguese, the language of the store's customers.

## How to run

Requires Python 3.11 or later (validated on 3.14.6, on Windows 11) and an OpenAI API key.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env      # fill in OPENAI_API_KEY and OPENAI_MODEL
python -m emporio prepare        # builds the database and the index in var/
python -m emporio web            # opens at http://127.0.0.1:8000; Ctrl+C stops it
python -m emporio chat           # or chat in the terminal; "sair" exits
```

On Linux or macOS, activate the environment with `source .venv/bin/activate` and copy the file with `cp`.

| Command | What it does |
| --- | --- |
| `python -m emporio web --port 8080` | Uses another port for the local page. |
| `python -m emporio chat --customer-id 6` | Testing shortcut: skips verification and starts as that customer. Not available on the page. |
| `python -m emporio chat --show-tools` | Shows the tools used in each reply. |
| `python -m emporio chat --script file.txt --transcript output.md` | Sends the messages in a file to the real model and saves the conversation. If the model fails, it exits with an error and saves no transcript. |
| `pytest` | Runs the deterministic tests, with no network and no key. |
| `python evals/run.py` | Runs the acceptance scenarios against the real model (uses API credits). |
| `python conversations/capture.py` | Regenerates the example conversations (uses API credits). |

To test orders, ask about an order and give the customer's e-mail and phone in the conversation; they are in the customers CSV in `data/`. Customer 6 owns order 9, customer 2 owns order 8, customer 12 owns order 3, and customer 7 owns orders 2 and 20.

## Architecture

```text
CSVs ──► validated load ──► SQLite ◄── parameterized queries ───── catalog.py, orders.py
Markdown ──► FTS5 index ───────┘  ◄── lexical search ───────────── knowledge.py
                                                                        ▲
Message ──► agent.py ◄──► OpenAI (tool calling)                         │
                 └──────► tools.py: validates arguments and executes ───┘
```

| Module in `src/emporio/` | Responsibility |
| --- | --- |
| `loader.py`, `schema.sql` | Validated, atomic load of the CSVs. |
| `db.py` | Read-only connection used for customer service. |
| `pricing.py` | Promotions, PIX, installments and arithmetic in cents. |
| `catalog.py` | Search, details and offers. |
| `orders.py` | Identity verification and the session customer's orders. |
| `knowledge.py` | Policy parsing, indexing and retrieval. |
| `returns.py` | Deadlines for regret, defects, preference and the legal warranty. |
| `tools.py` | Contracts of the seven tools and error handling. |
| `llm.py` | Single point of contact with the OpenAI SDK. |
| `agent.py`, `prompts.py` | Tool loop, session and persona. |
| `cli.py` | The `prepare`, `chat` and `web` commands. |
| `web.py`, `static/` | Local server and chat page, on top of the same agent as the CLI. |

## Technical decisions

**Function calling, with no agent framework.** The loop is a short repetition: call the model, run tools, return the results. LangChain or LangGraph would add abstractions without solving a real problem here, and the explicit flow makes it easier to test loop limits, failures and history trimming.

**Hybrid RAG with structured and text retrieval.** Operational data is relational and needs exact filters, so it goes to SQL; policies are text, so they go to text search. Mixed questions use both sources.

**Tools instead of free-form SQL.** The model only chooses among seven operations with validated arguments. This limits the query surface and keeps filters, exposed fields and authorization in the code. No tool returns an e-mail or phone: `verify_identity` only compares them, so a prompt injection has no way to obtain this data.

| Tool | Purpose |
| --- | --- |
| `search_products` | Search by text, category and price. Computes the effective price before filtering, sorting and limiting. |
| `get_product_details` | Description, specifications, status and stock. |
| `calculate_offer` | Final amount and conditions by payment method. |
| `verify_identity` | Confirms the customer from the e-mail and phone given in the conversation. |
| `get_order_status` | One order or the list of orders of the session customer. |
| `check_return_window` | Computes, using today's date, whether the exchange or return deadline is open. |
| `search_knowledge` | Excerpts from the manual, with section, page and assumptions. |

**Deadlines in Python.** The original plan had five tools, with the model comparing dates. Counting days is arithmetic, and a mistake denies or grants a customer's right, so this calculation follows the same rule as prices. Today's date comes from the application, in the store's time zone; the model only interprets what the customer said and reports the result.

**Identification in the conversation.** The agent asks for e-mail and phone only when it needs to look up an order. The code only compares values the customer actually typed in the conversation, and only then sets the session customer, so the model neither chooses the customer nor can invent the data. Asking for both is not excessive: one e-mail is shared by two customers in the data, and alone it could expose another person's orders. A failure looks the same whichever field was wrong, there are three attempts per conversation, and the identity does not change once confirmed. E-mail and phone stay out of the log and of the displayed tool record.

**OpenAI with Chat Completions.** The API offers tool calling with strict schemas, which reduces malformed arguments. The explicit history fits the in-memory session management. The model is configurable through `OPENAI_MODEL`, and there is no multi-provider layer, which a prototype does not justify.

**The `gpt-5.4-mini` model.** The agent needs reliable tool calling rather than long reasoning, since calculations and rules live in the code. This model accepts tools in Chat Completions with no additional configuration, and its cost (US$ 0.75 input and US$ 4.50 output per million tokens, as of 2026-11-04) allowed the scenarios to be repeated many times. According to the OpenAI documentation, `gpt-6.1-terra` requires the Responses API for tools, and `gpt-6-luna` requires `reasoning_effort=none`. The failures observed were intermittent omissions, described under Verification, not calculation errors, as those live in the code.

**SQLite with `sqlite3`.** It needs no server, allows joins and has FTS5. The database is generated locally: the load validates everything in a temporary file and only then replaces the previous one, so an error never leaves a partial database. Money is stored in integer cents, with `Decimal` for discounts.

**FTS5 instead of embeddings.** The manual is small and its terms are predictable, and lexical search is simple to reproduce and debug. Since FTS5 has no Portuguese stemmer, the query combines terms with OR and prefixes, and each section has curated aliases (“me arrependi”, “quero devolver”) with a higher weight. Related sections are retrieved together, so PIX comes with the non-cumulative discount rule. Embeddings remain a possible evolution, if evaluations show failures with paraphrases.

**Policies in Markdown with YAML.** The manual's text is kept apart from the interpretations, recorded in `assumptions`, with source page and version. The PDF, marked for restricted use, is challenge material and is not in this repository: the sections in `knowledge/` preserve the text, with source page and version, and the `source` field cites that file. The index is rebuilt from these files.

**Prompt.** The system prompt defines the persona from §7.1 of the manual, when to consult each source, the rules the model cannot bypass and what it must not claim to have done. Date, time and store status are computed in Python and injected on every call.

**Local page with no framework.** The page makes it easier to test the agent by hand without changing its rules: it uses the same `Agent` as the CLI, and the session customer is still set by the application, out of the model's reach. The server uses only the standard library, as a one-page prototype does not justify a new dependency. Because it fronts a paid API key on the machine, it listens only on `127.0.0.1`, refuses other `Host` and `Origin` values, so a website open in the browser cannot use the key, and never interprets the model's reply as HTML.

**CLI and in-memory history.** The challenge focuses on the agent, and the CLI is enough to demonstrate it and capture the example conversations. History is kept during the session, with the discussed products in the prompt to resolve “e no PIX?” (“what about with PIX?”). It is trimmed by whole turns, never separating tool calls from their results. Persisting conversations would bring no gain in the evaluated scenarios and would create a new concern with personal data.

## Assumptions

The challenge asks for ambiguities to be resolved and documented.

| Situation | Decision |
| --- | --- |
| Availability | For sale only with `status = active` and positive stock: some discontinued and “coming soon” products have stock. |
| Promotion | `is_active` is the only source of validity, as there are no dates. The Black Friday mentioned in the manual grants no discount by itself in November. |
| Several active promotions on one product | Does not occur in the current data. If it did, no promotional price would be chosen, and the agent would advise confirming. |
| Promotions and payment methods | A promotion applies to any payment method, and installments are computed on the promotional price. |
| Budget search | Filters by effective price: active promotion and, only if the customer asks, PIX. The Ohana CK-20 at R$ 549.00 fits “up to R$ 500” at R$ 439.20. |
| Instrument type in search | “Violão” (guitar) or “viola” select the category, not a mention in the description: a ukulele that mentions “violão” is not listed among guitars. |
| Counting deadlines | Calendar days: the day of the event is day 0, and the last day still counts. A deadline ending on a closed day is not extended, as the manual does not provide for this. |
| Start of each deadline | Regret counts from receipt and defect exchange from purchase, as the manual says. With no recorded delivery date, receipt is what the customer states; the forecast never replaces it. |
| Start of the preference exchange | The manual does not define it. The agent only concludes what holds for both possible starts; if the conclusion depends on the start, it advises confirming with the team. |
| Regret on in-store purchases | Section 4.1 covers online purchases. The agent does not apply this right to the physical store and presents the other options. |
| Minimum installment | §3.1 prevails over the general R$ 100.00 in §3. “Except below R$ 50.00” was read as a R$ 50.00 minimum for up to 3 installments. |
| Rounding | Half a cent rounds up. Remaining cents go to the first installments, and the smallest installment is the one compared to the minimum. |
| WhatsApp | The manual is inconsistent: (67) 3321-4500 from the channel's own section takes priority. (67) 3341-4444 is treated as the general phone. |
| E-mail | Uses the address on the cover, with no accent in the domain. |
| Shipping at exactly R$ 500.00 | The manual does not define it. The agent advises confirming, without choosing a rule. |
| Time zone | `America/Campo_Grande`, the store's time zone. Holidays are not in the data and are treated as unavailable information. |
| Identification | E-mail and phone on record, verified in the code. Data that appears in a conversation does not prove ownership, so this is a prototype check, not authentication. |

Inconsistencies found in the data, preserved and not corrected:

- The totals of orders 3 and 20 differ from the sum at current prices. The recorded total is treated as historical.
- Orders 9, 16 and 18 have installments below the current minimum. The rule applies to new offers, not to history.
- Orders 3 and 14 contain a “coming soon” product, and order 3 is recorded as delivered. Order items never indicate availability.
- Order 8 is recorded as shipped, with a past forecast. The agent reports the record and advises confirming, without stating a delay or a delivery.
- The manual mentions over 300 instruments, and the data has 65. The agent answers about what is listed, without concluding that the store does not carry the rest.

## Verification

- **Deterministic tests (`pytest`):** loading, prices, installments, return deadlines, availability, ownership, policy retrieval, agent loop mechanics and the local page's API. The loop tests use a simulated client and do not evaluate model replies.
- **Acceptance scenarios (`evals/`):** 44 scenarios with the real model, checking facts and tools called, without requiring identical text. Tone and persona are reviewed by reading the conversations. The model varies between runs: in the last three rounds with `gpt-5.4-mini`, on 2026-11-04, 43, 43 and 40 of 44 passed. The failures were intermittent: the model sometimes skips a lookup, omits installment values, or opens with “dá sim” (“yes, you can”) before noting that an already tested product depends on the team's assessment. The checks were kept strict rather than loosened to clear these failures.
- **Example conversations (`conversations/`):** five transcripts of real CLI runs, with the model and tools used. Reviewing them found errors the checks had missed, such as misread installments, which were fixed before this capture. The e-mail and phone in conversation 03 belong to the fictional customer in the CSV.

These tests cover the evaluated scenarios and do not guarantee the behavior for every possible input. Prompts and tests do not eliminate hallucination or prompt injection; that is why calculation, authorization and the write block live in the code.

## Known limitations

- The agent takes no actions: it does not record complaints, open returns, issue refunds or send messages. It advises and points to the store's channels, even though the manual provides for recording complaints.
- The deadline calculation is deterministic but depends on its inputs: the data records neither the actual delivery nor the purchase channel, and the model translates what the customer says into dates and a case type. Using the tool is instructed by the prompt, not enforced by the code, so the model may occasionally reply without calling it.
- The data is a snapshot of the imported CSVs, with no synchronization with stock or carriers.
- Text search depends on the curated aliases and may fail on unforeseen paraphrases.
- Attribute search is textual over name, description and specifications; there are no structured attribute filters.
- There is no shipping quote, as it depends on postal code, weight and dimensions, which were not provided.
- There is no calendar of holidays or special hours.
- History is lost when the session ends, when the page is reloaded or when a new conversation starts.
- Anyone who knows a customer's e-mail and phone can look up their orders, as there is no code sent to the customer and no password. This data, typed in the conversation, is also sent to the model provider and appears in the CLI transcript if one is saved.
- The page is for local use only: it has no login or HTTPS and must not be exposed on a network. It does not save transcripts, which is the CLI's role.

## What I would do with more time

- Real authentication, with a code sent to the customer, instead of comparing e-mail and phone.
- Code verification of each reply before it reaches the customer: checking that amounts and dates come from the tools and that the installment plan appears in full, with one retry on failure.
- Retrieval evaluation with more paraphrases and, if the results justify it, hybrid search with embeddings.
- Integrations to record complaints and hand over to human agents.
- An API and WhatsApp integration, the store's main remote channel.
- Structured attribute filters, such as strings, electronics and body type.
- Cost and latency observability per conversation, and evaluations in CI.

## Use of coding assistants

- **Codex:** analysis of the challenge and the data, requirements gathering, a design review and the execution plan, recorded in Portuguese in [LEVANTAMENTO_REQUISITOS.md](LEVANTAMENTO_REQUISITOS.md), [PARECER_ADERENCIA_SYSTEM_DESIGN_V2.md](PARECER_ADERENCIA_SYSTEM_DESIGN_V2.md) and [PLANO_EXECUCAO.md](PLANO_EXECUCAO.md).
- **Claude Code:** review of the plan against the data, which revealed gaps such as historical installments below the minimum and the store's time zone; implementation of the code, policies, tests and scenarios; writing this README and its Portuguese translation.

**My role:** I decided on the provider, scope and publication, and reviewed the results. I asked for an error review and chose what to fix; requested the local interface, the deadline calculation in Python and the identification in the conversation; and corrected the agent when I noticed it was asking which store the purchase was made in, although there is only one. Critical calculations were checked against the CSVs by automated tests, and the model's replies by reading the conversations, which found errors the tests had missed.
