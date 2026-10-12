"""
LLM integration for ABC pipeline.

Supports Google Gemini as primary provider, with Groq as fallback.
"""

import json
import logging
import os
import re
import time

try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False

try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

from .config import (
    ABC_CONTEXT,
    ABC_CONTEXT_SHORT,
    GEMINI_MAX_RETRY,
    GEMINI_MODEL_ENRICH,
    GEMINI_MODEL_FILTER,
    GEMINI_MIN_INTERVAL,
    GEMINI_RETRY_WAIT,
    GROQ_MAX_RETRY,
    GROQ_MODEL_ENRICH,
    GROQ_MODEL_FILTER,
    GROQ_MIN_INTERVAL,
    GROQ_RETRY_WAIT,
)

from .filters import reglements_match

log = logging.getLogger(__name__)

# Global state for rate limiting
_groq_client = None
_gemini_client = None
_groq_last_call: float = 0.0
_gemini_last_call: float = 0.0
_llm_call_count: int = 0
_llm_total_seconds: float = 0.0

# Use Gemini as primary provider
USE_GEMINI = True


def get_llm_stats() -> dict:
    return {'calls': _llm_call_count, 'seconds': round(_llm_total_seconds, 1)}


# ============================================================================
# GEMINI IMPLEMENTATION (Primary)
# ============================================================================


def _get_gemini_client():
    """Initialize and return the Gemini client."""
    global _gemini_client
    if not GEMINI_AVAILABLE:
        raise RuntimeError("google.generativeai not installed. Run: pip install google-generativeai")
    
    api_key = os.environ.get('GEMINI_API_KEY', '')
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY manquant")
    
    if _gemini_client is None:
        genai.configure(api_key=api_key)
        _gemini_client = genai
    
    return _gemini_client


def _call_gemini(prompt: str, system: str, max_tokens: int, model: str) -> str:
    """Call Gemini API with rate limiting and retry."""
    global _gemini_last_call, _llm_call_count, _llm_total_seconds
    
    # Force minimum tokens to avoid truncation
    max_tokens = max(max_tokens, 1000)
    
    # Rate limiting
    elapsed = time.time() - _gemini_last_call
    if elapsed < GEMINI_MIN_INTERVAL:
        time.sleep(GEMINI_MIN_INTERVAL - elapsed)
    
    # Initialize client
    client = _get_gemini_client()
    
    # Prepare system message - Gemini uses a different format
    system_content = system if 'json' in system.lower() else (system + ' Reponds en JSON.').strip()
    
    for attempt in range(1, GEMINI_MAX_RETRY + 1):
        _gemini_last_call = time.time()
        _t = time.time()
        
        try:
            # Use the GenerativeModel API
            gemini_model = client.GenerativeModel(model)
            response = gemini_model.generate_content(
                contents=[system_content, prompt],
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=max_tokens,
                    temperature=0.2,
                )
            )
            
            _llm_call_count += 1
            _llm_total_seconds += time.time() - _t
            
            # Handle the response
            if hasattr(response, 'text'):
                return response.text.strip()
            elif hasattr(response, 'candidate') and hasattr(response.candidate, 'content'):
                content = response.candidate.content
                if hasattr(content, 'parts'):
                    return ''.join(part.text for part in content.parts if hasattr(part, 'text')).strip()
                return str(content).strip()
            else:
                return str(response).strip()
                
        except Exception as e:
            err_str = str(e)
            if '429' in err_str or 'rate_limit' in err_str.lower() or 'quota' in err_str.lower():
                log.warning(
                    f"Gemini rate limit [{model}] "
                    f"(tentative {attempt}/{GEMINI_MAX_RETRY}) — attente {GEMINI_RETRY_WAIT}s"
                )
                time.sleep(GEMINI_RETRY_WAIT)
                _gemini_last_call = time.time()
            else:
                log.warning(f"Gemini erreur [{model}] : {e}")
                return ''
    
    log.error(f"Gemini [{model}] : toutes les tentatives épuisées")
    return ''


# ============================================================================
# GROQ IMPLEMENTATION (Fallback)
# ============================================================================


def _get_groq_client() -> 'Groq':
    global _groq_client
    if not GROQ_AVAILABLE:
        raise RuntimeError("groq not installed. Run: pip install groq")
    if _groq_client is None:
        api_key = os.environ.get('GROQ_API_KEY', '')
        if not api_key:
            raise RuntimeError("GROQ_API_KEY manquant")
        _groq_client = Groq(api_key=api_key)
    return _groq_client


def _call_groq_fallback(prompt: str, system: str, max_tokens: int, model: str) -> str:
    """Groq fallback with rate limiter and retry."""
    global _groq_last_call, _llm_call_count, _llm_total_seconds
    max_tokens = max(max_tokens, 1000)  # Force minimum 1000 tokens to avoid truncation
    elapsed = time.time() - _groq_last_call
    if elapsed < GROQ_MIN_INTERVAL:
        time.sleep(GROQ_MIN_INTERVAL - elapsed)

    client = _get_groq_client()
    sys_content = system if 'json' in system.lower() else (system + ' Reponds en JSON.').strip()
    messages = [
        {'role': 'system', 'content': sys_content},
        {'role': 'user', 'content': prompt},
    ]

    for attempt in range(1, GROQ_MAX_RETRY + 1):
        _groq_last_call = time.time()
        _t = time.time()
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            _llm_call_count += 1
            _llm_total_seconds += time.time() - _t
            return resp.choices[0].message.content.strip()
        except Exception as e:
            err_str = str(e)
            if '429' in err_str or 'rate_limit' in err_str.lower():
                log.warning(
                    f"Groq fallback rate limit [{model}] "
                    f"(tentative {attempt}/{GROQ_MAX_RETRY}) — attente {GROQ_RETRY_WAIT}s"
                )
                time.sleep(GROQ_RETRY_WAIT)
                _groq_last_call = time.time()
            else:
                log.warning(f"Groq fallback erreur [{model}] : {e}")
                return ''

    log.error(f"Groq fallback [{model}] : toutes les tentatives épuisées")
    return ''


# ============================================================================
# UNIFIED LLM CALLER
# ============================================================================


def call_llm(prompt: str, system: str = '', max_tokens: int = 300, enrich: bool = False) -> str:
    """Unified LLM entry-point - uses Gemini by default, falls back to Groq if needed."""
    global USE_GEMINI
    model = GEMINI_MODEL_ENRICH if enrich else GEMINI_MODEL_FILTER
    
    # Try Gemini first
    if USE_GEMINI and GEMINI_AVAILABLE:
        try:
            return _call_gemini(prompt, system, max_tokens, model)
        except Exception as e:
            log.warning(f"Gemini échoué, passage à Groq : {e}")
            USE_GEMINI = False  # Disable for this session
    
    # Fallback to Groq
    groq_model = GROQ_MODEL_ENRICH if enrich else GROQ_MODEL_FILTER
    return _call_groq_fallback(prompt, system, max_tokens, groq_model)


def call_groq(prompt: str, system: str = '', max_tokens: int = 300, model: str = None) -> str:
    """Compatibility alias (pipeline called `call_groq` originally)."""
    global USE_GEMINI
    if model is None:
        # Determine if this should use enrich model
        enrich = (model == GROQ_MODEL_ENRICH)
        return call_llm(prompt, system, max_tokens, enrich=enrich)
    else:
        # If model is specified, use it directly
        if USE_GEMINI and GEMINI_AVAILABLE:
            try:
                return _call_gemini(prompt, system, max_tokens, GEMINI_MODEL_FILTER)
            except Exception as e:
                log.warning(f"Gemini échoué, passage à Groq : {e}")
                USE_GEMINI = False
        return _call_groq_fallback(prompt, system, max_tokens, model)


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================


def extract_json(text: str) -> dict:
    """Extract the first valid JSON object from a string."""
    try:
        return json.loads(text)
    except Exception:
        pass

    # Fallback: try to locate the first {...} block (greedy to handle nested braces)
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except Exception:
            pass
    return {}


def _safe_score(val) -> int:
    """Safely cast a score value to an int in [1, 3]."""
    try:
        return max(1, min(3, int(float(str(val or 1).split('/')[0]))))
    except (ValueError, TypeError):
        return 1


# ============================================================================
# LLM — Batch analysis (for rate limit optimization)
# ============================================================================


def groq_analyse_batch(articles: list, prompt_type: str = "rss") -> list:
    """Analyse plusieurs articles en un seul appel LLM.
    Optimise le quota (15 RPM, 1M tokens/jour pour Gemini).
    
    Args:
        articles: Liste de dicts avec {"titre": str, "contenu": str}
        prompt_type: "jorf" ou "rss"
        
    Returns:
        Liste de dicts avec les analyses
    """
    if not articles:
        return []
    
    # Construis le prompt batch
    if prompt_type == "jorf":
        system = 'Tu es expert climat et RSE conseillant le Responsable Environnement de ABC. JSON uniquement.'
        intro = (
            f"{ABC_CONTEXT_SHORT}\n\n"
            "Ces textes du Journal Officiel ont passé un premier filtre.\n"
            "Analyse CHACUN et réponds avec un JSON contenant les résultats pour chaque texte.\n\n"
        )
    else:  # rss
        system = 'Tu es analyste climat et RSE pour ABC. JSON valide uniquement.'
        intro = (
            f"{ABC_CONTEXT_SHORT}\n\n"
            "Analyse CHACUN de ces articles et réponds avec un JSON contenant les résultats.\n\n"
        )
    
    # Ajoute chaque article au prompt
    article_sections = []
    for idx, art in enumerate(articles, 1):
        titre = art.get("titre", "")[:120]
        contenu = art.get("contenu", "")[:350]
        article_sections.append(f"Texte {idx}: [TITRE] {titre} [CONTENU] {contenu}")
    
    prompt = intro + "\n\n".join(article_sections) + "\n\n"
    prompt += 'Format: {"texte_1": {"pertinent": boolean, "resume": "...", "score": 1}, "texte_2": {...}, ...}'
    
    # Use the unified call_llm which will use Gemini by default
    raw = call_llm(prompt, system, max_tokens=1000)
    result = extract_json(raw)
    
    if not result:
        return [{'pertinent': False, 'resume': '', 'score': 1} for _ in articles]
    
    # Parse les résultats
    outputs = []
    for idx in range(1, len(articles) + 1):
        key = f"texte_{idx}"
        if key in result:
            outputs.append(result[key])
        else:
            outputs.append({'pertinent': False, 'resume': '', 'score': 1})
    
    return outputs


# ============================================================================
# LLM — JORF analysis
# ============================================================================


def groq_analyse_jorf(titre: str, contenu: str) -> dict:
    system = 'Tu es expert climat et RSE conseillant le Responsable Environnement de ABC. JSON uniquement.'
    prompt = (
        f"{ABC_CONTEXT_SHORT}\n\n"
        "Ce texte du Journal Officiel a passé un premier filtre par mots-clés.\n"
        "Détermine s'il est VRAIMENT pertinent pour le Responsable Environnement / Climat de ABC.\n\n"
        "PERTINENT si le texte crée ou modifie une obligation s'appliquant DIRECTEMENT à ABC :\n"
        "  - Obligations BGES, bilan GES, plan climat pour grandes entreprises de services\n"
        "  - CSRD / reporting extra-financier / taxonomie verte\n"
        "  - Réglementation déchets : nouvelles filières REP, tri, traçabilité\n"
        "  - Réglementation énergie : efficacité énergétique, DPE tertiaire, renouvelables\n"
        "  - Mobilité et flotte : ZFE, véhicules électriques, réglementation transport\n"
        "  - Arrêtés CatNat, PPR, risques naturels (risques sur sites clients ABC)\n"
        "  - Loi ou décret structurant la politique climatique (SNBC, PNACC, loi énergie-climat)\n\n"
        "REJETER IMPÉRATIVEMENT :\n"
        "  - Réglementation ICPE, SEVESO, biocides, REACH, amiante (ABC n'est pas exploitant)\n"
        "  - Nominations, délégations de signature, textes purement administratifs\n"
        "  - Conventions collectives hors branche propreté\n"
        "  - Réglementation sectorielle sans rapport : agriculture, pêche, défense, santé humaine\n\n"
        "RÈGLE : si le lien avec ABC est indirect ou nécessite plusieurs déductions → NON PERTINENT.\n\n"
        "Score (si pertinent) :\n"
        "  1 = information de veille\n"
        "  2 = évolution réglementaire à anticiper\n"
        "  3 = obligation immédiate ou risque direct\n\n"
        f"TITRE: {titre}\n"
        f"CONTENU: {contenu[:500]}\n\n"
        'JSON: {"pertinent": boolean, "resume": "ce que ça change pour ABC en 1 phrase (vide si non pertinent)", '
        '"pourquoi": "", "score": 1}'
    )

    raw = call_llm(prompt, system, max_tokens=300, enrich=False)
    result = extract_json(raw)
    if not result:
        return {'pertinent': False, 'resume': '', 'pourquoi': '', 'score': 1}

    score = _safe_score(result.get('score'))
    if not result.get('pertinent') or score < 2:
        result['pourquoi'] = ''
        return result

    enrich_prompt = (
        f"{ABC_CONTEXT}\n\n"
        f"Texte JO retenu (score {score}/3) : {titre}\n"
        f"Résumé : {result.get('resume','')}\n\n"
        "En 1-2 phrases précises, explique POURQUOI c'est un signal stratégique important pour ABC : "
        "quelle obligation concrète, quel délai, quel risque ou quelle opportunité.\n"
        'JSON: {"pourquoi": "..."}'
    )
    raw2 = call_llm(enrich_prompt, 'Expert RSE ABC. JSON uniquement.', max_tokens=150, enrich=True)
    enrich = extract_json(raw2)
    if enrich.get('pourquoi'):
        result['pourquoi'] = enrich['pourquoi']

    log.debug(f"JORF enrichi (score={score}) : {titre[:50]}")
    return result


# ============================================================================
# LLM — RSS / Presse analysis
# ============================================================================


def groq_analyse_rss(titre: str, contenu: str) -> dict:
    if reglements_match(titre):
        return {'pertinent': True, 'score': 3, 'resume': titre[:200], 'pourquoi': 'Mention de réglementation prioritaire.'}
    
    system = 'Tu es analyste climat et RSE pour ABC. JSON valide uniquement.'
    prompt = (
        f"{ABC_CONTEXT_SHORT}\n\n"
        "Évalue cet article : est-il pertinent pour le Responsable Environnement / Climat de ABC?\n\n"
        "PERTINENT si le sujet concerne directement ABC :\n"
        "- Politique climatique FR/UE : SNBC, PNACC, loi énergie-climat, trajectoires nationales\n"
        "- Décarbonation entreprises : objectifs net zéro, scope 1/2/3, bilan GES, BGES\n"
        "- Reporting ESG : CSRD, ESRS, taxonomie verte, reporting extra-financier\n"
        "- Réglementation déchets : REP, tri, traçabilité, éco-organismes\n"
        "- Énergie & bâtiment : efficacité énergétique, DPE tertiaire, renouvelables\n"
        "- Mobilité durable : ZFE, électrification flottes, véhicules propres\n"
        "- Risques climatiques opérationnels en France ou UE affectant les entreprises de services\n\n"
        "NON PERTINENT (exclure complètement) :\n"
        "- ICPE/SEVESO/REACH (ABC non exploitant), élections, géopolitique pure\n"
        "- Agriculture/pêche/sylviculture, immobilier résidentiel, finance de marché\n"
        "- Catastrophes naturelles à l'étranger sans lien réglementaire FR/UE\n"
        "- Faits divers, sport, défense, santé humaine sans angle entreprise\n"
        "EN CAS DE DOUTE sur la pertinence : pertinent=true avec score=1. "
        "Score 1 = article intéressant à lire pour rester informé, même sans implication directe immédiate.\n\n"
        "Score (exigeant pour 2/3 — score 1 est le filet de sécurité) :\n"
        "  1 = tendance, contexte ou signal à connaître — va dans 'À consulter'\n"
        "  2 = évolution réglementaire ou marché qui impose une action identifiable à ABC dans 12 mois\n"
        "  3 = obligation légale imminente, risque direct ou opportunité stratégique claire\n"
        "Score 2/3 uniquement si l'article mentionne explicitement un sujet opérationnel pour ABC.\n\n"
        f"TITRE: {titre}\n"
        f"CONTENU: {contenu[:600]}\n\n"
        'JSON: {"pertinent": boolean, "resume": "1-2 phrases sur ce que ça change pour ABC (vide si non pertinent)", "pourquoi": "", "score": 1}'
    )

    raw = call_llm(prompt, system, max_tokens=300, enrich=False)
    result = extract_json(raw)
    if not result:
        return {'pertinent': False, 'resume': '', 'pourquoi': '', 'score': 1}

    score = _safe_score(result.get('score'))
    if not result.get('pertinent') or score < 2:
        result['pourquoi'] = ''
        return result

    enrich_prompt = (
        f"{ABC_CONTEXT}\n\n"
        f"Article retenu (score {score}/3) : {titre}\n"
        f"Résumé : {result.get('resume','')}\n\n"
        "En 1-2 phrases précises, explique POURQUOI c'est un signal stratégique important pour ABC : "
        "quelle implication concrète, quel délai d'action, quel risque ou quelle opportunité.\n"
        'JSON: {"pourquoi": "..."}'
    )
    raw2 = call_llm(enrich_prompt, 'Expert RSE ABC. JSON uniquement.', max_tokens=150, enrich=True)
    enrich = extract_json(raw2)
    if enrich.get('pourquoi'):
        result['pourquoi'] = enrich['pourquoi']

    log.debug(f"RSS enrichi (score={score}) : {titre[:50]}")
    return result


# ============================================================================
# LLM — JORF executive briefing
# ============================================================================


_BRIEFING_SKIP = [
    'nomination', 'délégation de signature', 'désignation',
    'portant nomination', 'portant délégation', 'délégation de pouvoir',
    'cessation de fonctions',
]


def groq_briefing_jorf(articles: list, today_str: str) -> str:
    """Generate a daily executive briefing from JORF significant texts."""
    if not articles:
        return ''

    candidates = [a for a in articles if not any(p in a['titre'].lower() for p in _BRIEFING_SKIP)]
    if not candidates:
        return ''

    lines = []
    for i, a in enumerate(candidates[:80]):
        nature = a.get('contenu', '').split(' — ')[0] or 'Texte'
        lines.append(f"{i + 1}. [{nature}] {a['titre']}")

    system = 'Tu es expert RSE. Réponds UNIQUEMENT avec ce JSON exact : {"briefing": "ton texte ici"}'
    prompt = (
        f"Textes du Journal Officiel du {today_str} :\n\n"
        + '\n'.join(lines)
        + "\n\nEn 2-3 phrases maximum, résume ce qui est significatif pour un "
        "Responsable Environnement/Climat d'une grande entreprise de services. "
        "Si rien n'est pertinent, écris une seule phrase le disant. "
        "RÉPONDS UNIQUEMENT avec ce JSON, sans autre texte : "
        '{"briefing": "ton résumé ici"}'
    )

    raw = call_llm(prompt, system, max_tokens=300)
    result = extract_json(raw)
    if not isinstance(result, dict):
        return ''

    briefing = ''
    for key in ('briefing', 'pertinence', 'summary', 'résumé', 'texte', 'content'):
        val = result.get(key, '')
        if isinstance(val, str) and len(val.strip()) > 10:
            briefing = val.strip()
            break

    if not briefing:
        for val in result.values():
            if isinstance(val, str) and len(val.strip()) > 10:
                briefing = val.strip()
                break

    _NEG = ('aucun lien', 'aucune obligation', 'aucun texte', 'rien de pertinent',
            'non pertinent', 'pas de lien', 'sans lien', 'n\'est identifiable')
    if briefing and any(n in briefing.lower() for n in _NEG):
        briefing = ''
    return briefing or "Aucun texte réglementaire significatif pour ABC dans ce JO."


# ============================================================================
# LLM — PJL analysis (Parlement)
# ============================================================================


def groq_analyse_pjl(titre: str, description: str) -> dict:
    system = 'Tu es conseiller RSE senior pour ABC. JSON valide uniquement.'
    has_content = len(description) > len(titre) + 50
    content_note = (
        "Le texte du PJL est fourni ci-dessous — base ton analyse UNIQUEMENT sur ce contenu.\n"
        if has_content else
        "ATTENTION : seul le titre est disponible, pas le texte complet. "
        "Si le titre ne mentionne pas explicitement un sujet ABC, répondre pertinent=false.\n"
    )
    prompt = (
        f"{ABC_CONTEXT_SHORT}\n\n"
        "Évalue ce projet de loi gouvernemental pour le Responsable Environnement / Climat de ABC.\n\n"
        f"{content_note}\n"
        "PERTINENT si le texte crée ou modifie des obligations s'appliquant à ABC :\n"
        "- Obligations BGES, bilan GES, plan climat pour grandes entreprises\n"
        "- CSRD, taxonomie verte, reporting extra-financier\n"
        "- Réglementation déchets : REP, tri, traçabilité, éco-organismes\n"
        "- Énergie & bâtiment : efficacité énergétique, DPE tertiaire, renouvelables\n"
        "- Mobilité : ZFE, électrification flottes, véhicules propres\n"
        "- Politique climatique structurante : SNBC, PNACC, loi énergie-climat\n\n"
        "À EXCLURE SYSTÉMATIQUEMENT (pertinent=FALSE indépendamment du contenu) :\n"
        "- Fiscalité, impôts, conventions fiscales, double imposition\n"
        "- Justice pénale, extradition, réadmission, police\n"
        "- Diplomatie, accords internationaux, protocoles (sauf si explicitement clima/énergie/déchets)\n"
        "- Sécurité sociale, retraite, protection sociale\n"
        "- Affaires culturelles, restitution de biens\n"
        "- Commerce, douanes (sauf CBAM/traçabilité carbone)\n"
        "- Agriculture (sauf pratiques de durabilité strictement liées à ABC)\n"
        "- ICPE, SEVESO, biocides, REACH (ABC non exploitant)\n"
        "- Santé humaine, défense, transport collectif public\n"
        "EN CAS DE DOUTE : pertinent=FALSE. Seuls les textes EXPLICITEMENT applicables à ABC (climat/énergie/déchets/RSE) sont pertinent=TRUE.\n"
        "Ne pas inférer d'obligations ABC non mentionnées dans le texte.\n\n"
        "Score d'urgence pour ABC :\n"
        "  1 = à connaître, horizon > 18 mois\n"
        "  2 = à anticiper, impact probable dans 6-18 mois\n"
        "  3 = impact direct imminent, action requise\n\n"
        f"TITRE: {titre}\n"
        f"TEXTE: {description[:2500]}\n\n"
        'JSON: {"pertinent": boolean, "resume": "ce que ce PJL change pour ABC en 1-2 phrases, basé uniquement sur le texte (vide si non pertinent)", '
        '"pourquoi": "", "score": 1, "horizon": "estimation entrée en vigueur ex: fin 2026"}'
    )

    raw = call_llm(prompt, system, max_tokens=300, enrich=False)
    result = extract_json(raw)
    if not result:
        return {'pertinent': False, 'resume': '', 'pourquoi': '', 'score': 1, 'horizon': ''}

    score = _safe_score(result.get('score'))
    if not result.get('pertinent') or score < 2:
        result['pourquoi'] = ''
        return result

    enrich_prompt = (
        f"{ABC_CONTEXT}\n\n"
        f"PJL retenu (score {score}/3) : {titre}\n"
        f"Résumé : {result.get('resume','')}\n"
        f"Horizon estimé : {result.get('horizon','')}\n"
        f"Extrait du texte : {description[:1000]}\n\n"
        "En 1-2 phrases BASÉES SUR LE TEXTE FOURNI, explique pourquoi c'est un signal "
        "stratégique pour ABC : quelle obligation concrète, quel délai, quel risque opérationnel. "
        "Ne pas inventer d'obligations non mentionnées dans le texte.\n"
        'JSON: {"pourquoi": "..."}'
    )
    raw2 = call_llm(enrich_prompt, 'Expert RSE ABC. JSON uniquement.', max_tokens=150, enrich=True)
    enrich = extract_json(raw2)
    if enrich.get('pourquoi'):
        result['pourquoi'] = enrich['pourquoi']

    log.debug(f"PJL enrichi (score={score}) : {titre[:50]}")
    return result


# ============================================================================
# LLM — Parlement executive briefing
# ============================================================================


def groq_briefing_parlement(entries: list, today_str: str) -> str:
    if not entries:
        return ''

    lines = [f"{i + 1}. [{e['source']}] {e['titre']}" for i, e in enumerate(entries[:40])]
    system = 'Tu es expert RSE. Réponds UNIQUEMENT avec ce JSON exact : {"briefing": "ton texte ici"}'
    prompt = (
        f"Activité parlementaire du {today_str} :\n\n" + '\n'.join(lines) +
        "\n\nEn 2-3 phrases, résume ce qui est significatif pour un Responsable "
        "Environnement/Climat d'une grande entreprise de services (climat, énergie, "
        "déchets, RSE, reporting). Si rien n'est pertinent, dis-le en 1 phrase. "
        'Réponds UNIQUEMENT : {"briefing": "ton résumé"}'
    )

    raw = call_llm(prompt, system, max_tokens=250)
    result = extract_json(raw)
    if not isinstance(result, dict):
        return ''

    for key in ('briefing', 'pertinence', 'summary', 'résumé', 'texte', 'content'):
        val = result.get(key, '')
        if isinstance(val, str) and len(val.strip()) > 10:
            return val.strip()

    for val in result.values():
        if isinstance(val, str) and len(val.strip()) > 10:
            return val.strip()
    return ''
