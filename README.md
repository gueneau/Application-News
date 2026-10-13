# Veille Environnementale — Pipeline & Application

An automated regulatory and environmental intelligence platform. Runs twice daily via GitHub Actions and deploys a live web app to GitHub Pages.

---

## What it does

The pipeline aggregates, filters, and analyzes regulatory and environmental news from multiple sources, then publishes a structured daily briefing accessible via a single-page web application.

**Sources covered:**
- **Journal Officiel (JORF)** — Daily official gazette via DILA OpenData (tar.gz XML). Every text published that day is collected; keyword-matched texts are pre-screened by the LLM, then shortlisted texts are checked against their full Légifrance text before being confirmed and summarized.
- **RSS and publication feeds** — Press, think tanks, regulators, public agencies, and environmental organizations, including Le Monde, Les Echos, The Guardian, GreenUnivers, ESG Today, Reporterre, Actu-Environnement, ADEME, EFRAG, AEF Développement Durable, and Acteurs Publics.
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
├── llm.py               # LLM calls (Gemini primary, Groq fallback)
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

### RSS and thematic articles

1. **Freshness checks** — Reject stale or undated feed entries before spending LLM calls. Crawl-only sources also suppress URLs already reviewed in recent runs.
2. **Broad batch screening** — Send up to 15 recent entries from each source together, using their titles and short feed excerpts. This is a recall-oriented shortlist, not the final relevance decision. Source-specific `require_keywords` are context hints only; they do not reject an entry.
3. **Full-text review** — Fetch the article only for shortlisted candidates. The editorial LLM review decides whether to publish it and assigns its category and score. If a page is inaccessible, only the available excerpt is used, and the model is told not to infer facts that excerpt does not support.
4. **Fallback sources** — Playwright and crawl-only sources use the same batch screening and final review path. If a batch response is malformed or unavailable, candidates proceed to individual final review rather than being silently discarded.

Thematic articles should provide reusable context: a sector trend, aggregate or historical figures, a study, a comparison, or a generalizable analysis. A company announcement, local project, or isolated incident is excluded unless the article substantiates a broader sector shift. Score 1 is for this kind of useful context; scores 2–3 are reserved for a concrete, evidenced implication for ABC.

### Official and legislative texts

The JORF pipeline collects the day's official texts, pre-screens keyword candidates in batches, then checks shortlisted items against the available full Légifrance text before confirming and summarizing them. Parliamentary tracking reads the Assembly's actual dated legislative stages; a document labelled “Texte adopté” is not by itself treated as final adoption by both chambers.

The application uses **Gemini as the primary LLM provider** and falls back to **Groq**. Provider models and rate limits are configured in `abc_pipeline/config.py`.

**Scoring:**
| Score | Meaning | Displayed as |
|---|---|---|
| 1 | General trend, data, or context worth knowing; no direct action established | Articles thématiques |
| 2 | Evidenced regulatory or market change calling for an identifiable action within 12 months | À suivre |
| 3 | Imminent obligation, direct risk, or clearly evidenced strategic opportunity | ⚠ Impact direct |

The LLM must base its decision on the content actually supplied and must not infer significance from a title or keyword alone. When a complete article is unavailable, an excerpt may be reviewed, but unsupported implications are not presented as facts.

---

## Web Application

Single-page app (`index.html`) deployed to GitHub Pages. No build step, no framework — vanilla JS + CSS.

**Views:**
- **Feed** — Daily RSS signals, filterable by category and score. "Articles thématiques" contains selected score-1 trend and context articles; "À suivre" contains score-2/3 items.
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
    'require_keywords': ['keyword1', ...],      # optional topic hints for LLM screening, not a hard filter
    'article_url_contains': '/articles/',       # for filtered fallback crawl
}
```

**Sources without RSS** (scrape-only): set `url: 'https://invalid-no-rss'` — the pipeline will skip to `fallback_crawl` automatically.

---

## Setup

```bash
pip install -r requirements.txt

export GEMINI_API_KEY=...
export GROQ_API_KEY=...       # fallback provider
export SUPABASE_URL=...
export SUPABASE_ANON_KEY=...

python -m abc_pipeline.main
```

**GitHub Actions secrets:** `GEMINI_API_KEY` and `GROQ_API_KEY` are used by the LLM pipeline. `SUPABASE_URL` and `SUPABASE_ANON_KEY` enable legislative dossier and user-data sync.

---

## Key Design Decisions

- **Freshness filter** — RSS articles older than 24h (72h on Mondays) are excluded. JORF texts with `date_publi` older than 7 days are excluded (consolidations/rectifications of older laws).
- **No hallucination** — PJL analysis always uses crawled bill text (Senate exposé des motifs → AN HTML version → AN dossier page fallback), never title alone.
- **Graceful degradation** — Gemini failure → Groq fallback. RSS failure → batch-screened fallback crawl. JORF unavailable → clean skip.
- **Stateless runs** — No local state between GitHub Actions runs. Supabase is the source of truth for legislative dossiers; the pipeline seeds from it on each run.
- **Editorial precision** — Score 1 is reserved for generalizable thematic value, not a catch-all for borderline company news. A batch screening stage favors recall; final full-text review controls publication. Scores 2/3 require an evidenced implication for ABC.
