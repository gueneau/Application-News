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
    assert '"categorie": "une catégorie de la liste"' in prompts[0]
    assert "Déchets, Émissions ou Environnement" in prompts[0]


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
