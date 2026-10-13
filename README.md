# Veille Environnementale ABC

Automated environmental and regulatory monitoring for ABC. The pipeline collects recent information, uses an LLM to select and summarize useful items, and publishes them in a web app.

**Open the live app: [gueneau.github.io/Application-News](https://gueneau.github.io/Application-News/)**

## How it works

GitHub Actions runs the pipeline twice a day, at 09:00 and 14:00 Paris time. It gathers information from four areas:

- **News and thematic articles** from RSS feeds and selected websites.
- **Journal officiel (JORF)** texts from DILA open data.
- **Parliamentary activity** and manually tracked legislative dossiers.
- **Water restrictions** from the official VigiEau API.

For news articles, the pipeline:

1. Excludes entries that are too old or have no usable publication date.
2. Sends up to 15 recent entries from each source to the LLM in a batch. The LLM screens titles and short feed excerpts; source keywords are hints, not mandatory matches.
3. Fetches the article page for shortlisted entries when available, then asks the LLM to make the final selection, assign a category, and write a summary. If the page cannot be read, the available excerpt is used instead.
4. Publishes general trends, useful figures, research, and sector-wide analysis as **Articles thématiques**. Company announcements and isolated local stories are normally excluded unless they demonstrate a broader trend.

The initial batch is a shortlist, not a publication decision. If its response is incomplete or invalid, candidates still go through the individual final review. Scores 2 and 3 are reserved for items with an evidenced, concrete implication for ABC; score 1 is for useful general context.

JORF texts and parliamentary stages follow separate workflows. An Assembly document labelled “Texte adopté” does not, by itself, mean that a law was finally adopted by both chambers.

## What is in this repository?

```text
abc_pipeline/       Data collection, filtering, LLM analysis, and publication
  sources/          JORF, RSS/news, Parliament, and VigiEau connectors
  config.py         Sources, keywords, LLM models, and other settings
  llm.py            LLM prompts and provider integration
index.html          Web application (plain HTML, CSS, and JavaScript)
supabase/           Database migrations for dossiers and user features
tests/              Pipeline regression tests
.github/workflows/  Scheduled data refresh and GitHub Pages publishing
```

The pipeline writes dated JSON data. GitHub Actions publishes that data, the archive, and `index.html` to the `gh-pages` branch. The live site is served by GitHub Pages.

Supabase stores shared legislative dossiers, comments, stage-change events, and article likes. Apply database migrations to the Supabase project using its SQL Editor when setting up or changing the database.

## Run locally

Use Python 3.11 and install the dependencies:

```bash
python -m pip install -r requirements.txt
```

Set the API keys and Supabase settings used by the pipeline, then run:

```bash
python -m abc_pipeline.main
```

The pipeline uses Gemini as its primary LLM provider and Groq as a fallback. Set `GEMINI_API_KEY` and `GROQ_API_KEY`; `SUPABASE_URL` and `SUPABASE_ANON_KEY` enable Supabase sync.

Run the tests with:

```bash
python -m pytest tests/
```

## Configuration

Edit `abc_pipeline/config.py` to change data sources, source-specific topic hints, freshness-related settings, LLM models, or timeouts. A source's `require_keywords` are passed as context to batch screening; they are not used as a hard filter.

GitHub Actions needs the API credentials configured as repository secrets. The scheduled workflow publishes updated data to `gh-pages`; the workflow can also be started manually from the repository's **Actions** tab.
