"""
Basic smoke tests for the ABC pipeline.
Run with: pytest tests/
"""
import json
import pytest
from datetime import datetime


# ── filters ──────────────────────────────────────────────────────────────────

def test_keyword_match_positive():
    from abc_pipeline.filters import keyword_match
    assert keyword_match("Nouveau décret sur les véhicules électriques") is True

def test_keyword_match_negative():
    from abc_pipeline.filters import keyword_match
    assert keyword_match("Résultats du championnat de football") is False

def test_keyword_match_english():
    from abc_pipeline.filters import keyword_match
    assert keyword_match("EU carbon tax reform approved by parliament") is True


# ── parliamentary stage detection ────────────────────────────────────────────

def test_scrape_dossier_stage_uses_latest_dated_stage_not_completion_badge(monkeypatch):
    from abc_pipeline.sources import parlement

    class Response:
        text = """
        <div class="swiper-slide">
          <span class="_bold _colored-primary">Première lecture au Sénat</span>
          <span class="_colored-grey _small">Lundi 10 novembre 2025 Texte adopté ✅</span>
          <span class="_small _bold">Texte adopté ✅</span>
        </div>
        <div class="swiper-slide swiper-slide-active">
          <span class="_bold _colored-primary">Première lecture à l'Assemblée nationale</span>
          <span class="_colored-grey _small">Vendredi 20 février 2026</span>
        </div>
        """

        @staticmethod
        def raise_for_status():
            pass

    monkeypatch.setattr(parlement.requests, 'get', lambda *args, **kwargs: Response())

    assert parlement._scrape_dossier_stade('https://example.com/dossier') == (
        "Première lecture à l'Assemblée nationale"
    )


@pytest.mark.parametrize(
    ('label', 'expected'),
    [
        ('Dépôt au Sénat', 'Dépôt'),
        ('Examen en commission', 'Commission'),
        ('Première lecture au Sénat', 'Première lecture au Sénat'),
        (
            "Première lecture à l'Assemblée nationale",
            "Première lecture à l'Assemblée nationale",
        ),
        ('Deuxième lecture au Sénat', 'Deuxième lecture au Sénat'),
        ('Sénat en deuxième lecture', 'Deuxième lecture au Sénat'),
        (
            'Nouvelle lecture à l’Assemblée nationale',
            'Deuxième lecture à l\'Assemblée nationale',
        ),
        ('Commission mixte paritaire', 'Commission mixte paritaire'),
        ('Texte définitivement adopté', 'Texte définitivement adopté'),
        ('Texte rejeté par le Sénat', 'Texte rejeté'),
        ('Promulgation de la loi', 'Promulgation'),
        ('Loi promulguée et publiée au Journal officiel', 'Promulgation'),
    ],
)
def test_detect_parliamentary_stage_returns_canonical_label(label, expected):
    from abc_pipeline.sources.parlement import _detect_stade_rss

    assert _detect_stade_rss(label) == expected


def test_detect_parliamentary_stage_does_not_treat_chamber_adoption_as_final():
    from abc_pipeline.sources.parlement import _detect_stade_rss

    assert _detect_stade_rss('Texte adopté par le Sénat') == ''


def test_scrape_dossier_stage_does_not_guess_from_global_status(monkeypatch):
    from abc_pipeline.sources import parlement

    class Response:
        text = '<html><body>Texte adopté ✅</body></html>'

        @staticmethod
        def raise_for_status():
            pass

    monkeypatch.setattr(parlement.requests, 'get', lambda *args, **kwargs: Response())

    assert parlement._scrape_dossier_stade('https://example.com/dossier') == ''


def test_scrape_dossier_stage_reports_rejection_on_latest_dated_step(monkeypatch):
    from abc_pipeline.sources import parlement

    class Response:
        text = """
        <div class="swiper-slide swiper-slide-active">
          <span class="_bold _colored-primary">Première lecture au Sénat</span>
          <span class="_colored-grey _small">Lundi 10 novembre 2025</span>
          <span class="_small _bold">Texte rejeté ❌</span>
        </div>
        """

        @staticmethod
        def raise_for_status():
            pass

    monkeypatch.setattr(parlement.requests, 'get', lambda *args, **kwargs: Response())

    assert parlement._scrape_dossier_stade('https://example.com/dossier') == 'Texte rejeté'


def test_scrape_adopted_text_listing_extracts_full_title_and_publication_date(monkeypatch):
    from abc_pipeline.sources import parlement

    class Response:
        text = """
        <ul class="liens-liste">
          <li data-id="OMC_PNREANR5L17BTA0351">
            <h3>Texte adopté N° 351</h3>
            <ul class="liens-liste-embed enligne clearfix">
              <li><span class="heure">Mis en ligne jeudi 8 octobre 2026 à 18h45</span></li>
              <li><a href="https://www.assemblee-nationale.fr/dyn/17/dossiers/olympe">Dossier législatif</a></li>
              <li><a href="https://www.assemblee-nationale.fr/dyn/old/17/ta/ta0351.asp">Document</a></li>
            </ul>
            <p> - Proposition de résolution invitant le Gouvernement à soutenir l'entrée au Panthéon, rejetée par l'Assemblée nationale, le 8 octobre 2026</p>
          </li>
        </ul>
        """

        @staticmethod
        def raise_for_status():
            pass

    monkeypatch.setattr(parlement.requests, 'get', lambda *args, **kwargs: Response())
    entries = parlement._scrape_an_listing(
        {'name': 'AN Textes adoptés', 'url': 'https://example.com/adoptes'},
        '2026-10-13',
    )

    assert len(entries) == 1
    assert entries[0]['titre'] == (
        "Proposition de résolution invitant le Gouvernement à soutenir "
        "l'entrée au Panthéon, rejetée par l'Assemblée nationale"
    )
    assert entries[0]['date'] == '2026-10-08'


def test_valid_category_accepts_supported_llm_category():
    from abc_pipeline.filters import valid_category
    assert valid_category("Déchets") == "Déchets"

def test_valid_category_falls_back_to_neutral_category_for_unknown_value():
    from abc_pipeline.filters import valid_category
    assert valid_category("Mobilité") == "Environnement"


# ── _date_from_text ───────────────────────────────────────────────────────────

def test_date_from_text_literal():
    from abc_pipeline.sources.rss import _date_from_text
    dt = _date_from_text("Guidelines published 2 April 2020 — read more")
    assert dt == datetime(2020, 4, 2)

def test_date_from_text_french():
    from abc_pipeline.sources.rss import _date_from_text
    dt = _date_from_text("Publiée le 17 mars 2026")
    assert dt == datetime(2026, 3, 17)

def test_date_from_text_numeric():
    from abc_pipeline.sources.rss import _date_from_text
    dt = _date_from_text("AEF dépêche 17/03/2026 16:19 — Dépêche n° 747515")
    assert dt == datetime(2026, 3, 17)

def test_date_from_text_none():
    from abc_pipeline.sources.rss import _date_from_text
    assert _date_from_text("No date here at all") is None


def test_rss_fallback_rejects_old_publication_date_even_when_title_has_future_date(monkeypatch):
    from types import SimpleNamespace

    from abc_pipeline.sources import rss

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 13, 10)

    monkeypatch.setattr(rss, 'datetime', FixedDateTime)
    monkeypatch.setattr(rss.requests, 'get', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('offline')))
    monkeypatch.setattr(rss.feedparser, 'parse', lambda *args, **kwargs: SimpleNamespace(bozo=True, entries=[], bozo_exception='offline'))
    monkeypatch.setattr(rss, 'crawl_article_links_filtered', lambda *args, **kwargs: [{
        'titre': 'EFRAG extends its survey deadline to 18 October 2026',
        'url': 'https://example.com/efrag',
    }])
    monkeypatch.setattr(rss, 'crawl_article', lambda *args, **kwargs: 'A relevant sustainability reporting survey notice.')
    monkeypatch.setattr(rss, 'get_crawled_date', lambda url: '2026-10-09')
    monkeypatch.setattr(rss, 'groq_analyse_batch', lambda *args, **kwargs: [{'pertinent': True}])
    monkeypatch.setattr(rss, 'groq_analyse_rss', lambda *args, **kwargs: {
        'pertinent': True,
        'score': 3,
        'categorie': 'Réglementation',
    })

    items = rss.fetch_rss_source({
        'name': 'EFRAG',
        'url': 'https://example.com/feed',
        'fallback_crawl': 'https://example.com/news',
        'article_url_contains': '/news/',
        'require_keywords': ['survey'],
    }, '2026-10-13')

    assert items == []


def test_rss_batch_screens_all_recent_candidates_then_validates_shortlisted_full_text(monkeypatch):
    from types import SimpleNamespace
    import time

    from abc_pipeline.sources import rss

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 13, 10)

    monkeypatch.setattr(rss, 'datetime', FixedDateTime)
    monkeypatch.setattr(rss.requests, 'get', lambda *args, **kwargs: SimpleNamespace(content=b'<rss/>'))
    monkeypatch.setattr(rss.feedparser, 'parse', lambda *_: SimpleNamespace(
        bozo=False,
        entries=[{
            'title': 'Historical heating demand shifts across Europe',
            'summary': 'A data-led comparison of long-term demand patterns.',
            'link': 'https://example.com/heating',
            'published_parsed': time.strptime('2026-10-13', '%Y-%m-%d'),
        }],
    ))
    screened = []
    monkeypatch.setattr(
        rss,
        'groq_analyse_batch',
        lambda articles, **kwargs: (
            screened.extend(articles) or [{'pertinent': True}]
        ),
    )
    crawled = []
    monkeypatch.setattr(
        rss,
        'crawl_article',
        lambda url: crawled.append(url) or 'Full article with historical figures and sector-wide energy trends.',
    )
    final_reviews = []

    def review_full_text(titre, contenu):
        final_reviews.append((titre, contenu))
        return {
            'pertinent': True,
            'score': 1,
            'resume': 'La demande de chaleur évolue sur plusieurs décennies.',
            'categorie': 'Énergie',
        }

    monkeypatch.setattr(rss, 'groq_analyse_rss', review_full_text)

    items = rss.fetch_rss_source({
        'name': 'Energy Trends',
        'url': 'https://example.com/feed',
        'require_keywords': ['term impossible to match'],
    }, '2026-10-13')

    assert len(screened) == 1
    assert screened[0]['titre'] == 'Historical heating demand shifts across Europe'
    assert screened[0]['contenu'] == 'A data-led comparison of long-term demand patterns.'
    assert crawled == ['https://example.com/heating']
    assert final_reviews[0][1].startswith('Full article')
    assert len(items) == 1
    assert items[0]['criticite'] == 1


# ── extract_json ──────────────────────────────────────────────────────────────

def test_extract_json_clean():
    from abc_pipeline.llm import extract_json
    result = extract_json('{"pertinent": true, "score": 2, "resume": "test"}')
    assert result['pertinent'] is True
    assert result['score'] == 2

def test_extract_json_with_preamble():
    from abc_pipeline.llm import extract_json
    result = extract_json('Sure! Here is the JSON: {"pertinent": false, "score": 1, "resume": ""}')
    assert result['pertinent'] is False

def test_extract_json_with_nested_braces():
    from abc_pipeline.llm import extract_json
    result = extract_json('{"resume": "Impact sur {CSRD} et {GHG}", "score": 2, "pertinent": true}')
    assert result['pertinent'] is True

def test_extract_json_invalid():
    from abc_pipeline.llm import extract_json
    result = extract_json("This is not JSON at all")
    assert result == {}


# ── _safe_score ───────────────────────────────────────────────────────────────

def test_safe_score_normal():
    from abc_pipeline.llm import _safe_score
    assert _safe_score(2) == 2

def test_safe_score_string():
    from abc_pipeline.llm import _safe_score
    assert _safe_score("2") == 2

def test_safe_score_fraction():
    from abc_pipeline.llm import _safe_score
    assert _safe_score("2/3") == 2

def test_safe_score_out_of_range():
    from abc_pipeline.llm import _safe_score
    assert _safe_score(5) == 3
    assert _safe_score(0) == 1

def test_safe_score_invalid():
    from abc_pipeline.llm import _safe_score
    assert _safe_score("élevé") == 1
    assert _safe_score(None) == 1

def test_groq_analyse_rss_uses_validated_llm_category(monkeypatch):
    from abc_pipeline import llm
    prompts = []

    def fake_call_llm(prompt, *args, **kwargs):
        prompts.append(prompt)
        return (
            '{"pertinent": true, "score": 1, "resume": "Résumé", '
            '"categorie": "Émissions"}'
        )

    monkeypatch.setattr(
        llm,
        'call_llm',
        fake_call_llm,
    )

    result = llm.groq_analyse_rss(
        "PFAS : des associations alertent sur un plan de vigilance",
        "Un article sur la vigilance des entreprises.",
    )

    assert result['categorie'] == "Émissions"
    assert '"categorie": "..."' in prompts[0]
    assert "Déchets" in prompts[0]
    assert "Environnement" in prompts[0]
    assert "risque ou l'adaptation que le cas concret met en lumière" in prompts[0]
    assert "ne rejette pas un cas concret utile" in prompts[0]
    assert "sans prétendre qu'il prouve une tendance nationale" in prompts[0]
    assert "annonce de test d'un outil par Enedis ou RTE" in prompts[0]


def test_rss_batch_screen_uses_titles_and_short_excerpts_for_recall(monkeypatch):
    from abc_pipeline import llm
    prompts = []

    def fake_call_llm(prompt, *args, **kwargs):
        prompts.append(prompt)
        return '{"texte_1": {"pertinent": true}}'

    monkeypatch.setattr(llm, 'call_llm', fake_call_llm)
    results = llm.groq_analyse_batch(
        [{
            'titre': 'Historic heat demand across Europe',
            'contenu': 'Long-term figures show a sector-wide change.',
            'themes': ['energy'],
        }],
        prompt_type='rss',
        stage='screen',
    )

    assert results == [{'pertinent': True}]
    assert 'court extrait' in prompts[0]
    assert "un cas local ou centré sur une entreprise peut mériter une lecture complète" in prompts[0].lower()
    assert "en cas d'incertitude, garde le candidat" in prompts[0]
    assert 'Historic heat demand across Europe' in prompts[0]
    assert 'Long-term figures show a sector-wide change.' in prompts[0]
    assert 'THÈMES INDICATIFS' in prompts[0]


def test_rss_editorial_prompt_keeps_heat_and_water_operational_case(monkeypatch):
    from abc_pipeline import llm
    prompts = []
    monkeypatch.setattr(
        llm,
        'call_llm',
        lambda prompt, *args, **kwargs: (
            prompts.append(prompt)
            or '{"pertinent": true, "score": 1, "resume": '
            '"Une usine a interrompu son activité faute d’eau pendant une canicule.", '
            '"categorie": "Environnement"}'
        ),
    )

    result = llm.groq_analyse_rss(
        "Canicule : quand l'usine de chips Bret's s'est arrêtée faute d'eau",
        "Lors d'une période de forte chaleur, l'usine a interrompu sa production "
        "en raison du manque d'eau. Le cas illustre les risques opérationnels liés "
        "au stress hydrique et à la continuité d'activité.",
    )

    assert result['pertinent'] is True
    assert result['score'] == 1
    assert "une usine de chips arrêtée lors d'une canicule faute d'eau" in prompts[0]
    assert "risques opérationnels liés au stress hydrique" in prompts[0]


def test_rss_batch_screen_rejects_incomplete_llm_response(monkeypatch):
    from abc_pipeline import llm
    monkeypatch.setattr(llm, 'call_llm', lambda *args, **kwargs: '{"texte_1": {"pertinent": true}}')

    with pytest.raises(ValueError, match='résultat manquant pour texte_2'):
        llm.groq_analyse_batch(
            [{'titre': 'Article 1'}, {'titre': 'Article 2'}],
            prompt_type='rss',
            stage='screen',
        )


def test_groq_analyse_rss_falls_back_when_llm_category_is_unknown(monkeypatch):
    from abc_pipeline import llm
    monkeypatch.setattr(
        llm,
        'call_llm',
        lambda *args, **kwargs: (
            '{"pertinent": true, "score": 1, "resume": "Résumé", '
            '"categorie": "Mobilité"}'
        ),
    )

    result = llm.groq_analyse_rss(
        "La voiture partagée entre voisins",
        "Un article sur une initiative locale.",
    )

    assert result['categorie'] == "Environnement"


def test_groq_analyse_rss_categorizes_priority_regulations_with_llm(monkeypatch):
    from abc_pipeline import llm
    prompts = []
    monkeypatch.setattr(llm, 'reglements_match', lambda titre: 'décret prioritaire')

    def fake_call_llm(prompt, *args, **kwargs):
        prompts.append(prompt)
        return '{"categorie": "Déchets"}'

    monkeypatch.setattr(llm, 'call_llm', fake_call_llm)

    result = llm.groq_analyse_rss(
        "Nouveau décret prioritaire sur les filières REP",
        "Texte portant sur les éco-organismes.",
    )

    assert result['score'] == 3
    assert result['categorie'] == "Déchets"
    assert '"categorie": "une catégorie de la liste"' in prompts[0]


def test_groq_analyse_jorf_receives_complete_text(monkeypatch):
    from abc_pipeline import llm
    full_text = "Disposition juridique détaillée. " * 200
    full_text += "DISPOSITION FINALE INTÉGRALE"
    prompts = []
    monkeypatch.setattr(
        llm,
        'call_llm',
        lambda prompt, *args, **kwargs: (
            prompts.append(prompt) or
            '{"pertinent": false, "score": 1, "resume": ""}'
        ),
    )

    llm.groq_analyse_jorf("Décret relatif à l'environnement", full_text)

    assert f"TEXTE INTÉGRAL:\n{full_text}" in prompts[0]
    assert "DISPOSITION FINALE INTÉGRALE" in prompts[0]


def test_crawl_article_can_return_full_text(monkeypatch):
    from abc_pipeline import crawl

    class Response:
        text = f"<html><body><p>{'texte complet ' * 500}</p></body></html>"

        @staticmethod
        def raise_for_status():
            pass

    monkeypatch.setattr(crawl.requests, 'get', lambda *args, **kwargs: Response())

    assert len(crawl.crawl_article("https://example.com")) == 2500
    full_text = crawl.crawl_article("https://example.com", max_chars=None)
    assert len(full_text) > 2500
    assert full_text.endswith("texte complet")


def test_jorf_full_analysis_only_runs_for_screened_items_with_full_text(monkeypatch):
    import io
    import tarfile

    from abc_pipeline.sources import jorf

    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w:gz') as tar:
        content = b"<root/>"
        member = tarfile.TarInfo("text.xml")
        member.size = len(content)
        tar.addfile(member, io.BytesIO(content))

    class Response:
        content = archive.getvalue()

        @staticmethod
        def raise_for_status():
            pass

    article = {
        'titre': 'Décret relatif aux déchets',
        'contenu': 'Décret — Ministère',
        'url': 'https://www.legifrance.gouv.fr/jorf/id/123',
        'date': '2026-10-12',
        'keyword_match': True,
    }
    full_text = "Texte intégral du décret. " * 300
    analyzed_texts = []
    monkeypatch.setattr(
        jorf,
        'get_today_jorf_url',
        lambda _: 'https://example.com/JORF_20261012-002503.tar.gz',
    )
    monkeypatch.setattr(jorf.requests, 'get', lambda *args, **kwargs: Response())
    monkeypatch.setattr(jorf, 'parse_jorf_xml', lambda *_: [article])
    briefing_inputs = []

    def make_briefing(items, date):
        briefing_inputs.extend(items)
        return 'Briefing fondé sur un texte confirmé.'

    monkeypatch.setattr(jorf, 'groq_briefing_jorf', make_briefing)
    monkeypatch.setattr(
        jorf,
        'crawl_article',
        lambda url, max_chars=2500: full_text if max_chars is None else '',
    )
    monkeypatch.setattr(
        jorf,
        'groq_analyse_batch',
        lambda *_args, **_kwargs: [{'pertinent': True}],
    )

    def analyze_full_text(titre, contenu):
        analyzed_texts.append(contenu)
        return {'pertinent': True, 'score': 2, 'resume': 'Résumé fondé sur le texte intégral.'}

    monkeypatch.setattr(jorf, 'groq_analyse_jorf', analyze_full_text)

    items, autres, total, briefing, issue_date = jorf.fetch_jorf('2026-10-12')

    assert total == 1
    assert issue_date == '2026-10-12'
    assert analyzed_texts == [full_text]
    assert items[0]['resume'] == 'Résumé fondé sur le texte intégral.'
    assert briefing_inputs[0]['resume'] == 'Résumé fondé sur le texte intégral.'
    assert briefing_inputs[0]['nature'] == 'Décret'
    assert 'confirmé' in briefing
    assert autres == []


def test_get_today_jorf_url_prefers_today_issue(monkeypatch):
    from abc_pipeline.sources import jorf

    monkeypatch.setattr(
        jorf,
        'list_jorf_files',
        lambda: [
            'JORF_20261011-235900.tar.gz',
            'JORF_20261012-002503.tar.gz',
            'JORF_20261012-140000.tar.gz',
        ],
    )

    assert jorf.get_today_jorf_url('20261012').endswith('JORF_20261012-140000.tar.gz')


def test_get_today_jorf_url_falls_back_to_latest_available_issue(monkeypatch):
    from abc_pipeline.sources import jorf

    monkeypatch.setattr(
        jorf,
        'list_jorf_files',
        lambda: [
            'JORF_20261011-002503.tar.gz',
            'JORF_20261011-205013.tar.gz',
        ],
    )

    assert jorf.get_today_jorf_url('20261012').endswith('JORF_20261011-205013.tar.gz')


def test_jorf_issue_date_comes_from_selected_archive_filename():
    from abc_pipeline.sources.jorf import _jorf_issue_date

    assert _jorf_issue_date(
        'https://echanges.dila.gouv.fr/OPENDATA/JORF/JORF_20261013-002011.tar.gz'
    ) == '2026-10-13'
    assert _jorf_issue_date('https://example.com/jorf.tar.gz') is None
    assert _jorf_issue_date('https://example.com/JORF_20261340-002011.tar.gz') is None


def test_fetch_jorf_reports_no_issue_date_when_archive_is_unavailable(monkeypatch):
    from abc_pipeline.sources import jorf

    monkeypatch.setattr(jorf, 'get_today_jorf_url', lambda _: None)

    assert jorf.fetch_jorf('2026-10-13') == ([], [], 0, '', None)


def test_jorf_briefing_does_not_use_screened_but_rejected_texts(monkeypatch):
    import io
    import tarfile

    from abc_pipeline.sources import jorf

    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w:gz') as tar:
        content = b"<root/>"
        member = tarfile.TarInfo("text.xml")
        member.size = len(content)
        tar.addfile(member, io.BytesIO(content))

    class Response:
        content = archive.getvalue()

        @staticmethod
        def raise_for_status():
            pass

    article = {
        'titre': 'Décret relatif aux certificats d’économies d’énergie',
        'contenu': 'Décret — Ministère',
        'url': 'https://www.legifrance.gouv.fr/jorf/id/123',
        'date': '2026-10-12',
        'keyword_match': True,
    }
    briefing_calls = []
    monkeypatch.setattr(
        jorf,
        'get_today_jorf_url',
        lambda _: 'https://example.com/JORF_20261012-002503.tar.gz',
    )
    monkeypatch.setattr(jorf.requests, 'get', lambda *args, **kwargs: Response())
    monkeypatch.setattr(jorf, 'parse_jorf_xml', lambda *_: [article])
    monkeypatch.setattr(jorf, 'groq_briefing_jorf', lambda items, *_: briefing_calls.extend(items))
    monkeypatch.setattr(jorf, 'crawl_article', lambda url, max_chars=None: 'Texte intégral du décret.')
    monkeypatch.setattr(
        jorf,
        'groq_analyse_batch',
        lambda *_args, **_kwargs: [{'pertinent': True}],
    )
    monkeypatch.setattr(
        jorf,
        'groq_analyse_jorf',
        lambda *_args: {'pertinent': False, 'score': 1, 'resume': 'Aucune obligation pour ABC.'},
    )

    items, autres, total, briefing, issue_date = jorf.fetch_jorf('2026-10-12')

    assert total == 1
    assert issue_date == '2026-10-12'
    assert items == []
    assert briefing_calls == []
    assert 'Aucun texte présélectionné' in briefing
    assert autres[0]['titre'] == article['titre']


def test_jorf_briefing_uses_verified_summaries_only(monkeypatch):
    from abc_pipeline import llm

    prompts = []
    monkeypatch.setattr(
        llm,
        'call_llm',
        lambda prompt, *args, **kwargs: (
            prompts.append(prompt) or '{"briefing": "Résumé des textes vérifiés."}'
        ),
    )
    items = [{
        'titre': 'Arrêté relatif aux économies d’énergie',
        'nature': 'ARRETE',
        'resume': 'Le texte encadre les contrôles des demandes CEE.',
        'pourquoi': 'ABC doit vérifier ses délégations CEE.',
    }]

    result = llm.groq_briefing_jorf(items, '2026-10-12')

    assert result == 'Résumé des textes vérifiés.'
    assert 'Le texte encadre les contrôles des demandes CEE.' in prompts[0]
    assert 'ABC doit vérifier ses délégations CEE.' in prompts[0]


# ── parse_jorf_xml ────────────────────────────────────────────────────────────

def test_parse_jorf_xml_basic():
    from abc_pipeline.sources.jorf import parse_jorf_xml
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <root>
      <TEXTE nor="ENVT2600001A" date_publi="2026-03-21" nature="Arr\xc3\xaat\xc3\xa9" cid="JORFTEXT000001">
        <TITRE_TXT>Arr\xc3\xaat\xc3\xa9 du 21 mars 2026 relatif aux v\xc3\xa9hicules \xc3\xa9lectriques</TITRE_TXT>
      </TEXTE>
    </root>"""
    articles = parse_jorf_xml(xml, "2026-03-21")
    assert len(articles) == 1
    assert "électriques" in articles[0]['titre']
    assert articles[0]['date'] == "2026-03-21"

def test_parse_jorf_xml_dedup():
    from abc_pipeline.sources.jorf import parse_jorf_xml
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <root>
      <TEXTE nor="ENVT2600001A" date_publi="2026-03-21">
        <TITRE_TXT>Premier texte</TITRE_TXT>
      </TEXTE>
      <TEXTE nor="ENVT2600001A" date_publi="2026-03-21">
        <TITRE_TXT>Doublon m\xc3\xaame NOR</TITRE_TXT>
      </TEXTE>
    </root>"""
    articles = parse_jorf_xml(xml, "2026-03-21")
    assert len(articles) == 1

def test_parse_jorf_xml_empty():
    from abc_pipeline.sources.jorf import parse_jorf_xml
    articles = parse_jorf_xml(b"<root></root>", "2026-03-21")
    assert articles == []

def test_parse_jorf_xml_malformed():
    from abc_pipeline.sources.jorf import parse_jorf_xml
    articles = parse_jorf_xml(b"not xml at all <<<", "2026-03-21")
    assert articles == []


# ── make_id ───────────────────────────────────────────────────────────────────

def test_make_id_deterministic():
    from abc_pipeline.filters import make_id
    assert make_id("JORF", "Titre A") == make_id("JORF", "Titre A")

def test_make_id_different():
    from abc_pipeline.filters import make_id
    assert make_id("JORF", "Titre A") != make_id("JORF", "Titre B")
