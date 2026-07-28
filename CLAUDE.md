# CLAUDE.md - Ausgemerzt

> Deutsche politische Faktencheck-SaaS. Soft-Fork / Downstream des
> `grregis/MuckScraper`-Kerns (Python/Flask + PostgreSQL/pgvector + Meilisearch
> + Ollama). Kanonische Spec-Heimat: `docs/spec/` (folgt). Vollplan:
> `~/.claude/Plans/squishy-floating-moler.md`.

## Sprache der Codebase: Python (Ausnahme zur globalen TS-Regel)

Dieses Repo ist **Python** (Flask/SQLAlchemy). Das ist eine explizite,
principal-freigegebene Ausnahme zur globalen "TypeScript immer, nie Python"-
>Invariante aus `~/.claude/LIFEOS/USER/CONFIG/OPERATIONAL_RULES.md`.

- Freigabe: Michael, 2026-07-27 (Memory-Proposal "Python exception for
  Ausgemerzt", applied 2026-07-27).
- Begründung: MuckScraper liefert ~70% der Maschinerie (Ingestion, Bias-Scoring,
  Editions-Workflow, Ollama-LLM). Ein TS-Port wäre Neubau statt Hill-Climb.
- Die Ausnahme gilt **nur für dieses Repo** und verallgemeinert nicht.

## Local-first-LLM-Regel

- Ollama ist der Default-LLM (`LLM_PROVIDER=ollama`). Cloud-Provider (Gemini,
  Groq, Ollama-Cloud) sind opt-in via `LLM_PROVIDER`-Env.
- LLM-Aufrufe gehen über `news_fetcher/llm_client.py:generate_text()` - kein
  direkter `requests`-Call an einen LLM-Endpunkt in Caller-Code.
- Timeouts sind via `LLM_TIMEOUT` env konfigurierbar (Default 60s für kurze
  Calls); summarizer nutzt bewusst längere explizite Timeouts.

## Faktencheck-Integritätsregel (P0)

Jedes Ampel-Urteil (`Verdict`) muss traceable sein:

- `claim_text` - die geprüfte Aussage
- `source_url` - die Artikel-URL, aus der der Claim extrahiert wurde
- `reference_url(s)` - die Referenz, gegen die geprüft wurde (HTTP-200)
- roher LLM-Output (`llm_raw`) - nachvollziehbar, keine Black-Box-Urteile
- `status` - green | yellow | red | unverifiable

Eine halluzinierte `reference_url` (kein echtes Dokument dahinter) ist ein
**kritischer Bug (P0)**. Kein Urteil ohne Quelle.

## Stil-Regeln

- **Kein Em-Dash.** `-` als Trenner, `|` in Tabellen, oder Satz umstellen.
  Zero Exceptions (gilt auch hier im CLAUDE.md).
- **Markdown, kein HTML** für Content. HTML nur für `<details>`/`<aside>`.
- **Keine fest codierten Pfade** - Env-Vars oder relative Pfade.
- `bun`/`bunx` nur für TS-Tooling (hier N/A außer optionalen Build-Scripts).

## Spec-Heimat

`docs/spec/` (folgt in Phase 0.3) wird die kanonische Spec-Heimat: `vision.md`,
`roadmap.md`, `operational-rules.md` - gemerged aus dem alten TS-Scaffold
(`schmetti-dev/ausgemerzt-ts-legacy`). Bis dahin ist dieser Plan-Pointer die
Spezifikation.

## Upstream-Verhältnis

- `origin` -> `schmetti-dev/ausgemerzt` (dieser Downstream, public fork).
- `upstream` -> `grregis/MuckScraper` (Kern; periodisch relevante Updates
  ziehen, keine Contributor-Pflicht).
- Bug-Fixes die den Kern betreffen: lokal machen, Upstream-PR optional.
- Unsere Nischenschicht (DE-Topics, DE-RSS, Faktencheck, SaaS) kommt upstream
  nie - sie bleibt hier.