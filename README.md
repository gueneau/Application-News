# Veille Environnementale ABC

Automated environmental and regulatory monitoring for ABC. The pipeline collects recent information, uses an LLM to select and summarize useful items, and publishes them in a web app. A separate, lighter path (Flux Live) publishes raw RSS headlines with no LLM involved, refreshed every 30 minutes.

**Open the live app: [gueneau.github.io/Application-News](https://gueneau.github.io/Application-News/)**

The app icon pairs a V with an open golden orbit and a small star; the same static design is used in the browser tab and on the home screen.

## Tech stack

Everything here runs without a paid server: GitHub Actions does the scheduled work, GitHub Pages serves the result, and Supabase is the only piece of infrastructure that needs an account.

| Layer | What's used | Why |
|---|---|---|
| Pipeline | Python 3.11, `requests` / `feedparser` for RSS, `BeautifulSoup4` + `lxml` for scraping, `Playwright` for the few JS-rendered sources | Collects and parses JORF, RSS, Assemblée nationale, and VigiEau data |
| LLM analysis | Google **Gemini** (primary) → **Groq** (fallback) → **OpenRouter** (final fallback) | Screens and scores articles; the pipeline degrades to the next provider automatically on rate limits or outages |
| Frontend | Plain HTML, CSS, and JavaScript — no framework, no build step | `index.html` is the entire web app; it's edited directly and deployed as-is |
| Data | Static JSON files (`data/*.json`, `archive.json`) | No application database read path; the frontend just fetches JSON over HTTPS |
| Shared state | **Supabase** (Postgres), accessed with the public anon key and row-level security policies | Stores legislative dossiers, comments, stage-change events, and favorited ("liked") articles — shared across every visitor, not per-account |
| Serverless | **Supabase Edge Function** (Deno) | The only place a GitHub token is allowed to exist; see [Flux Live](#flux-live-lightweight-rss-no-llm) below |
| Hosting & CI | **GitHub Actions** (scheduled + on-demand workflows) + **GitHub Pages** (`gh-pages` branch) | No server to maintain; Actions regenerates the data, Pages serves it |


## How it works

```text
GitHub Actions (cron)
  ├─ veille.yml      08:00 & 13:30 Paris → abc_pipeline (JORF, RSS+LLM, Parlement, VigiEau)
  └─ flux-live.yml   every 30 min        → scripts/flux_live.py (RSS only, no LLM)
         │                                        │
         ▼                                        ▼
   data/<date>.json, archive.json          data/flux_live.json
         └──────────────┬─────────────────────────┘
                         ▼
                  gh-pages branch  →  GitHub Pages  →  index.html (vanilla JS SPA)
                         │
                         ▼
                     Supabase (Postgres)
            dossiers · comments · stage events · favorites

   "Actualiser" button on the Flux Live tab (browser)
         │  no GitHub token ships in the page
         ▼
   Supabase Edge Function (trigger-flux-live)
         │  holds a repo-scoped GitHub PAT as a server-side secret
         ▼
   GitHub Actions workflow_dispatch → flux-live.yml runs immediately
```

GitHub Actions runs the main pipeline twice a day, at 09:00 and 14:00 Paris time. It gathers information from four areas:

- **News and thematic articles** from RSS feeds and selected websites.
- **Journal officiel (JORF)** texts from DILA open data.
- **Parliamentary activity** and manually tracked legislative dossiers.
- **Water restrictions** from the official VigiEau API.

For news articles, the pipeline:

1. Excludes entries that are too old or have no usable publication date.
2. Sends up to 15 recent entries from each source to the LLM in a batch. The LLM screens titles and short feed excerpts; source keywords are hints, not mandatory matches.
3. Fetches the article page for shortlisted entries when available, then asks the LLM to make the final selection, assign a category, and write a summary. If the page cannot be read, the available excerpt is used instead.
4. Publishes useful context as **Articles thématiques**: trends, figures, research, and sector analysis, as well as concrete cases that illuminate transferable operational risks (for example, a factory forced to stop during a heatwave because of water shortages). Such a case can be useful without proving a national trend. Purely promotional company announcements without a wider lesson are normally excluded.

The initial batch is a shortlist, not a publication decision. If its response is incomplete or invalid, candidates still go through the individual final review. Scores 2 and 3 are reserved for items with an evidenced, concrete implication for ABC; score 1 is for useful general context.

<details>
<summary>Show the RSS prompts sent to the LLM</summary>

The prompts below show the editorial instructions used by the pipeline. The pipeline inserts ABC's context, the article title and content, and the available topic categories where indicated.

### 1. Batch screening

System role: `Tu es éditeur d’une veille environnementale. JSON valide uniquement.`

> {ABC_CONTEXT_SHORT}
>
> Présélectionne largement ces candidats à partir du titre et du court extrait. Garde les tendances, chiffres, études et évolutions sectorielles, mais aussi les cas concrets qui peuvent éclairer un risque opérationnel pour ABC ou ses clients : chaleur, manque d'eau, énergie, continuité d'activité, adaptation ou contraintes environnementales. Un cas local ou centré sur une entreprise peut mériter une lecture complète même s'il ne prouve pas une tendance générale.
>
> Écarte en présélection les annonces purement promotionnelles ou commerciales sans enseignement apparent, et les sujets manifestement hors périmètre. Ne déduis pas une pertinence du seul mot-clé. Les titres et extraits courts sont ambigus : en cas d'incertitude, garde le candidat pour vérification du contenu complet.
>
> Cette étape est un filtre de rappel : pertinent=true signifie seulement « à vérifier sur l'article complet », pas « à publier ». Aucun résumé ni score définitif ici.
>
> Pour chaque candidat, le pipeline ajoute son titre, un extrait de 500 caractères maximum et, si configurés, des thèmes indicatifs de la source (qui ne suffisent pas seuls).
>
> Répondre en JSON : `{"texte_1": {"pertinent": boolean}, "texte_2": {...}}`

### 2. Final editorial review

System role: `Tu es analyste climat et RSE pour ABC. JSON valide uniquement.`

> {ABC_CONTEXT_SHORT}
>
> Tu sélectionnes des articles pour une veille environnementale utile à un responsable Environnement/Climat d'une entreprise de services. La veille doit à la fois donner du recul sur les tendances et faire remonter des cas concrets qui éclairent les risques et décisions opérationnels d'ABC ou de ses clients. Ne la limite pas aux études nationales ni aux articles qui prouvent une tendance statistique.
>
> RETENIR (score 1, « Articles thématiques ») si l'article apporte au moins un éclairage environnemental utile et crédible, notamment :
> - Une tendance, donnée agrégée ou historique, étude, comparaison, évolution sectorielle ou analyse de politique publique.
> - Un cas réel d'entreprise ou de site révélant une vulnérabilité, un risque opérationnel, une mesure d'adaptation ou une conséquence environnementale transposable à ABC ou à ses clients : par exemple une activité interrompue par la chaleur ou le manque d'eau, des contraintes sur l'énergie ou les matières, ou une adaptation face à un aléa climatique. Un cas documenté peut être utile même s'il est local et ne démontre pas à lui seul une tendance générale.
> - Une innovation ou pratique particulière si l'article donne un enseignement concret au-delà de la promotion d'un acteur.
>
> ÉCARTER les annonces d'entreprise purement promotionnelles ou commerciales (levée de fonds, nouveau contrat, lancement d'offre, partenariat ou test) quand elles n'apportent ni donnée sectorielle ni enseignement opérationnel. Écarter aussi le fait divers ou l'incident sans lien environnemental établi, les opinions sans éléments concrets, et les rapprochements avec ABC qui reposent seulement sur une association d'idées. La présence d'une entreprise, d'une ville ou d'un site dans le titre n'est jamais, à elle seule, un motif d'inclusion ou d'exclusion.
>
> Exemple de discernement : un article sur une usine de chips arrêtée lors d'une canicule faute d'eau est intéressant pour les risques de chaleur, de stress hydrique et de continuité d'activité, même si le cas est local ; il peut être retenu en score 1 sans prétendre qu'il prouve une tendance nationale. Une simple annonce de test d'un outil par Enedis ou RTE n'est pas suffisante, sauf si l'article explique ce qu'elle révèle d'une évolution plus large des réseaux ou de la flexibilité énergétique.
>
> Score 2 ou 3, « À suivre », uniquement si l'article établit une implication concrète pour ABC, ses opérations ou ses clients. Une implication peut être un risque illustré par un cas concret ; il n'est pas nécessaire qu'elle constitue une obligation réglementaire :
> - 2 = évolution ou risque à anticiper, avec action plausible à identifier dans les 12 mois
> - 3 = obligation imminente, risque direct et significatif, ou opportunité stratégique claire
>
> Ne donne pas 2/3 sur la seule base d'un exemple anecdotique ou d'une conséquence hypothétique. Un cas de vulnérabilité peut rester score 1 s'il est instructif mais ne permet pas d'établir un risque direct pour ABC.
>
> Domaines à considérer : climat et adaptation, chaleur, eau et sécheresse, énergie, bâtiment et efficacité, mobilité, émissions, déchets et biodiversité ; enjeux clients et continuité des prestations compris. Ces thèmes orientent l'analyse mais ne suffisent pas à rendre un article pertinent.
>
> Rejeter les contenus clairement hors périmètre :
> - ICPE/SEVESO/REACH (ABC non exploitant), élections, géopolitique pure
> - Agriculture/pêche/sylviculture, immobilier résidentiel, finance de marché
> - Catastrophes naturelles à l'étranger sans lien réglementaire FR/UE
> - Faits divers, sport, défense, santé humaine sans angle entreprise
>
> Reste équilibré : ne transforme pas tout cas local en tendance générale, mais ne rejette pas un cas concret utile simplement parce qu'il est local ou centré sur une entreprise. Si le titre et l'extrait sont ambigus, utilise le contenu fourni avant de trancher ; ne conclus pas à partir du seul mot-clé ou du seul titre.
>
> Attribue aussi une seule catégorie selon le sujet principal de l'article, pas les mentions secondaires ou les mots présents dans des liens/navigation : [available categories]. En cas de doute, choisis Environnement.
>
> Résumé : pour le score 1, donne en une phrase soit l'enseignement général, soit le risque ou l'adaptation que le cas concret met en lumière, sans extrapoler. Pour le score 2/3, explique en 1-2 phrases l'implication concrète pour ABC ou ses clients et distingue les faits de l'interprétation.
>
> Le contenu transmis peut être un extrait si la page complète est inaccessible ou trop longue. N'invente ni chiffres, ni causalité, ni implication non étayée. Si l'extrait ne suffit pas à confirmer un élément précis, reste prudent dans le résumé et le score plutôt que de le présenter comme un fait établi.
>
> Article examiné :
> TITRE: {titre}
> CONTENU: {contenu, limité à 600 caractères}
>
> Répondre en JSON : `{"pertinent": boolean, "resume": "...", "pourquoi": "", "score": 1, "categorie": "..."}`

</details>

JORF texts and parliamentary stages follow separate workflows. An Assembly document labelled "Texte adopté" does not, by itself, mean that a law was finally adopted by both chambers.

## Flux Live (lightweight RSS, no LLM)

The **Flux Live** tab shows the latest items from every configured RSS source, sorted by publication date, with no filtering and no LLM call — it is a plain headline feed, not an editorial selection.

- `scripts/flux_live.py` reads each feed and writes `data/flux_live.json`. It shares the source list (`RSS_SOURCES` in `abc_pipeline/config.py`) with the main pipeline but does none of its keyword matching, scoring, or summarizing.
- `.github/workflows/flux-live.yml` runs this script every 30 minutes and publishes the result to `gh-pages`, independently of the twice-daily `veille.yml` pipeline.
- The tab's **Actualiser** button triggers an immediate refresh instead of waiting for the next scheduled run. Because `workflow_dispatch` needs a GitHub token with `actions:write`, and that token can never be shipped in a public static page's source code, the button calls a Supabase Edge Function (`supabase/functions/trigger-flux-live`) instead. That function holds the token as a server-side secret, checks that no run is already in progress (a 2-minute cooldown), and is the only thing allowed to call GitHub's API on the button's behalf.
- Articles can be saved to **Favoris** from Flux Live the same way as from the main Feed — same star button, same shared Supabase-backed list.

## What is in this repository?

```text
abc_pipeline/       Data collection, filtering, LLM analysis, and publication
  sources/          JORF, RSS/news, Parliament, and VigiEau connectors
  config.py         Sources, keywords, LLM models, and other settings
  llm.py            LLM prompts and provider integration (Gemini / Groq / OpenRouter)
scripts/
  flux_live.py      Raw RSS fetch for the Flux Live tab (no LLM)
  send_eau_csv.py   Optional: emails daily water-restriction data as CSV
index.html          Web application (plain HTML, CSS, and JavaScript)
supabase/
  functions/        Edge Functions (trigger-flux-live)
  migrations/        Database schema for dossiers, comments, and favorites
tests/              Pipeline regression tests
.github/workflows/  veille.yml (main pipeline) and flux-live.yml (RSS-only refresh)
```

The pipeline writes dated JSON data. GitHub Actions publishes that data, the archive, and `index.html` to the `gh-pages` branch. The live site is served by GitHub Pages.

Supabase stores shared legislative dossiers, comments, stage-change events, and article favorites. Apply database migrations to the Supabase project using its SQL Editor when setting up or changing the database. Edge Functions are deployed separately with the Supabase CLI (see below).

## Run locally

Use Python 3.11 and install the dependencies:

```bash
python -m pip install -r requirements.txt
```

Set the API keys and Supabase settings used by the pipeline, then run:

```bash
python -m abc_pipeline.main
```

The pipeline tries Gemini first, then Groq, then OpenRouter. Set `GEMINI_API_KEY`, `GROQ_API_KEY`, and `OPENROUTER_API_KEY`; `SUPABASE_URL` and `SUPABASE_ANON_KEY` enable Supabase sync.

Flux Live is independent and needs no LLM key at all:

```bash
python -m scripts.flux_live
```

Run the tests with:

```bash
python -m pytest tests/
```

### Deploying the Edge Function

The `trigger-flux-live` function needs the [Supabase CLI](https://supabase.com/docs/guides/cli) and a GitHub personal access token scoped **only** to this repository, with "Actions: Read and write" permission and nothing else:

```bash
npx supabase login
npx supabase functions deploy trigger-flux-live --project-ref fsucjfaogepwvczdyikz
npx supabase secrets set GITHUB_TOKEN=<your fine-grained PAT> --project-ref fsucjfaogepwvczdyikz
```

## Configuration

Edit `abc_pipeline/config.py` to change data sources, source-specific topic hints, freshness-related settings, LLM models, or timeouts. A source's `require_keywords` are passed as context to batch screening; they are not used as a hard filter. Flux Live reuses the same `RSS_SOURCES` list.

GitHub Actions needs the API credentials configured as repository secrets. The scheduled workflow publishes updated data to `gh-pages`; both workflows can also be started manually from the repository's **Actions** tab.
