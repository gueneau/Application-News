# Veille Environnementale — Pipeline & Application

An automated regulatory and environmental intelligence platform. Runs twice daily via GitHub Actions and deploys a live web app to GitHub Pages.

---

## What it does

The pipeline aggregates, filters, and analyzes regulatory and environmental news from multiple sources, then publishes a structured daily briefing accessible via a single-page web application.

**Sources covered:**
- **Journal Officiel (JORF)** — Daily official gazette via DILA OpenData (tar.gz XML). Every text published that day is collected; keyword-matched texts are pre-screened by the LLM, then shortlisted texts are checked against their full Légifrance text before being confirmed and summarized.
- **RSS feeds** — ~10 sources: press (Le Monde, Les Echos, The Guardian, GreenUnivers, ESG Today, Reporterre, Actu-Environnement), think tanks (Shift Project, I4CE, Haut Conseil pour le Climat, France Stratégie), regulatory bodies (EFRAG, AEF Développement Durable, Acteurs Publics).
- **Parlement** — Scrapes the Assemblée Nationale for new government bills (projets de loi), tracks their progression through the legislative pipeline (Dépôt → Commission → Première lecture → Sénat → Adoption → Promulgation), and crawls the actual bill text from AN/Senate for LLM analysis.
- **VigiEau** — Water restriction alerts by department via the official API, with historical drought data.

---

## Architecture

```
GitHub Actions (cron 09:00 + 14:00 Paris)
        │
        ▼
abc_pipeline/
├── main.py              # Orchestrator
├── sources/
│   ├── jorf.py          # JORF tar.gz download + XML parsing
│   ├── rss.py           # RSS fetching + fallback scraping
│   ├── parlement.py     # AN bill tracking + stage scraping
│   └── vigieau.py       # Water restriction API
├── llm.py               # LLM calls (Mistral primary, Groq fallback)
├── crawl.py             # Article content fetching (BeautifulSoup)
├── filters.py           # Keyword matching + categorisation
├── config.py            # All sources, keywords, models, timeouts
├── supabase_sync.py     # Bidirectional sync with Supabase
└── output.py            # data.json + archive.json generation
        │
        ▼
data/YYYY-MM-DD.json  ──► GitHub Pages ──► index.html (SPA)
        │
        ▼
Supabase (legislative dossiers, comments, events, user likes)
```

---

## LLM Pipeline

Each item goes through a two-step LLM filter:

1. **Filter** (`llama-3.1-8b-instant` via Groq, `mistral-small-latest` as primary): determines `pertinent` (true/false) and assigns a **score 1–3**.
2. **Enrich** (`llama-3.3-70b-versatile`, only for score ≥ 2): generates a precise `pourquoi` explaining the concrete operational implication.

**Scoring:**
| Score | Meaning | Displayed as |
|---|---|---|
| 1 | Worth knowing, no immediate action | À consulter |
| 2 | Regulatory change requiring anticipation within 12 months | Signaux prioritaires |
| 3 | Immediate obligation or direct risk | ⚠ Impact direct |

The LLM is instructed to base its analysis **only on the actual text provided** — for bills, the pipeline crawls the full bill text from the Senate or AN HTML version before calling the LLM. If no content is retrievable, the LLM is explicitly told not to infer.

---

## Web Application

Single-page app (`index.html`) deployed to GitHub Pages. No build step, no framework — vanilla JS + CSS.

**Views:**
- **Feed** — Daily RSS + JORF signals, filterable by category and score. "À consulter" collapsible section for score-1 items.
- **JO** — Full Journal Officiel of the day, executive briefing + all texts grouped by theme. Searchable.
- **Dossiers** (Pipeline auto + Mes dossiers) — Legislative tracking with progress bar, stage history, and a drawer for comments and status management. Manual dossier addition supported.

**Persistence:** Supabase (PostgreSQL) stores tracked legislative dossiers, user comments, stage change events, and article likes across sessions.

---

## Configuration

All sources, keywords, LLM models, and timeouts are in `abc_pipeline/config.py`.

**Adding an RSS source:**
```python
{
    'name': 'Source Name',
    'url': 'https://example.com/rss.xml',
    'categorie': 'Presse',
    'fallback_crawl': 'https://example.com',   # used if RSS fails or returns 403
    'require_keywords': ['keyword1', ...],      # extra filter for broad sources
    'article_url_contains': '/articles/',       # for filtered fallback crawl
}
```

**Sources without RSS** (scrape-only): set `url: 'https://invalid-no-rss'` — the pipeline will skip to `fallback_crawl` automatically.

---

## Setup

```bash
pip install -r requirements.txt

export GROQ_API_KEY=...
export MISTRAL_API_KEY=...
export SUPABASE_URL=...
export SUPABASE_ANON_KEY=...

python -m abc_pipeline.main
```

**GitHub Actions secrets required:** `GROQ_API_KEY`, `MISTRAL_API_KEY`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`.

---

## Key Design Decisions

- **Freshness filter** — RSS articles older than 24h (72h on Mondays) are excluded. JORF texts with `date_publi` older than 7 days are excluded (consolidations/rectifications of older laws).
- **No hallucination** — PJL analysis always uses crawled bill text (Senate exposé des motifs → AN HTML version → AN dossier page fallback), never title alone.
- **Graceful degradation** — Mistral rate limit → Groq fallback. RSS failure → fallback crawl. JORF unavailable → clean skip.
- **Stateless runs** — No local state between GitHub Actions runs. Supabase is the source of truth for legislative dossiers; the pipeline seeds from it on each run.
- **Score 1 as safety net** — Borderline articles are included at score 1 ("À consulter") rather than silently dropped, so nothing relevant is missed. Score 2/3 requires explicit regulatory implication within 12 months.
