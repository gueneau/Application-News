"""
LLM integration for ABC pipeline.

Supports Google Gemini as primary provider, with Groq as fallback.
"""

import json
import logging
import os
import re
import time

import requests

try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False

try:
    import google.genai as genai
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
    OPENROUTER_MAX_RETRY,
    OPENROUTER_MODEL,
    OPENROUTER_RETRY_WAIT,
    OPENROUTER_TIMEOUT,
    GROQ_MAX_RETRY,
    GROQ_MODEL_ENRICH,
    GROQ_MODEL_FILTER,
    GROQ_MIN_INTERVAL,
    GROQ_RETRY_WAIT,
)

from .filters import CATEGORIES, reglements_match, valid_category

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
USE_GROQ = True
USE_OPENROUTER = True


def get_llm_stats() -> dict:
    return {'calls': _llm_call_count, 'seconds': round(_llm_total_seconds, 1)}


# ============================================================================
# GEMINI IMPLEMENTATION (Primary)
# ============================================================================


def _get_gemini_client():
    """Initialize and return the Gemini client."""
    global _gemini_client
    if not GEMINI_AVAILABLE:
        raise RuntimeError("google-genai not installed. Run: pip install google-genai")
    
    api_key = os.environ.get('GEMINI_API_KEY', '')
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY manquant")
    
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=api_key)
    
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
            # Use the client.models API
            response = client.models.generate_content(
                model=model,
                contents=[system_content, prompt],
                config=genai.types.GenerateContentConfig(
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


def _call_openrouter_fallback(prompt: str, system: str, max_tokens: int) -> str:
    """Last-resort OpenRouter call using the configured free model."""
    global _llm_call_count, _llm_total_seconds
    api_key = os.environ.get('OPENROUTER_API_KEY', '')
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY manquant")

    system_content = system if 'json' in system.lower() else (system + ' Reponds en JSON.').strip()
    payload = {
        'model': OPENROUTER_MODEL,
        'messages': [
            {'role': 'system', 'content': system_content},
            {'role': 'user', 'content': prompt},
        ],
        'max_tokens': max(max_tokens, 1000),
        'temperature': 0.2,
    }
    headers = {
        'Authorization': ''.join(('Bear', 'er ', api_key)),
        'Content-Type': 'application/json',
    }

    for attempt in range(1, OPENROUTER_MAX_RETRY + 1):
        started_at = time.time()
        try:
            response = requests.post(
                'https://openrouter.ai/api/v1/chat/completions',
                headers=headers,
                json=payload,
                timeout=OPENROUTER_TIMEOUT,
            )
            response.raise_for_status()
            content = response.json()['choices'][0]['message']['content']
            if not isinstance(content, str) or not content.strip():
                log.warning(f"OpenRouter [{OPENROUTER_MODEL}] : réponse vide")
                return ''
            _llm_call_count += 1
            _llm_total_seconds += time.time() - started_at
            return content.strip()
        except requests.RequestException as e:
            status_code = getattr(getattr(e, 'response', None), 'status_code', None)
            retryable = status_code == 429 or status_code is None or status_code >= 500
            if not retryable or attempt == OPENROUTER_MAX_RETRY:
                log.warning(f"OpenRouter erreur [{OPENROUTER_MODEL}] : {e}")
                return ''
            log.warning(
                f"OpenRouter indisponible [{OPENROUTER_MODEL}] "
                f"(tentative {attempt}/{OPENROUTER_MAX_RETRY}) — "
                f"attente {OPENROUTER_RETRY_WAIT}s"
            )
            time.sleep(OPENROUTER_RETRY_WAIT)
        except (KeyError, IndexError, TypeError, ValueError) as e:
            log.warning(f"Réponse OpenRouter invalide [{OPENROUTER_MODEL}] : {e}")
            return ''

    return ''


# ============================================================================
# UNIFIED LLM CALLER
# ============================================================================


def call_llm(prompt: str, system: str = '', max_tokens: int = 300, enrich: bool = False) -> str:
    """Use Gemini, then Groq, then OpenRouter for the rest of this run on failures."""
    global USE_GEMINI, USE_GROQ, USE_OPENROUTER
    model = GEMINI_MODEL_ENRICH if enrich else GEMINI_MODEL_FILTER
    
    # Try Gemini first
    if USE_GEMINI and GEMINI_AVAILABLE:
        try:
            response = _call_gemini(prompt, system, max_tokens, model)
            if response:
                return response
            log.warning("Gemini indisponible, passage à Groq pour le reste de cette exécution")
            USE_GEMINI = False
        except Exception as e:
            log.warning(f"Gemini échoué, passage à Groq : {e}")
            USE_GEMINI = False  # Disable for this session
    
    if USE_GROQ:
        groq_model = GROQ_MODEL_ENRICH if enrich else GROQ_MODEL_FILTER
        try:
            response = _call_groq_fallback(prompt, system, max_tokens, groq_model)
            if response:
                return response
        except Exception as e:
            log.warning(f"Groq échoué, passage à OpenRouter : {e}")
        log.warning("Groq indisponible, passage à OpenRouter pour le reste de cette exécution")
        USE_GROQ = False

    if USE_OPENROUTER:
        try:
            response = _call_openrouter_fallback(prompt, system, max_tokens)
            if response:
                return response
        except Exception as e:
            log.warning(f"OpenRouter échoué : {e}")
        USE_OPENROUTER = False
    return ''


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


def groq_analyse_batch(
    articles: list,
    prompt_type: str = "rss",
    *,
    stage: str = "final",
) -> list:
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
            "Ces textes du Journal Officiel ont passé un premier filtre par mots-clés. "
            "Les contenus fournis sont des extraits courts : cette étape sert uniquement à présélectionner "
            "les textes possiblement pertinents, pas à confirmer leur pertinence ni à rédiger leur résumé final. "
            "Pour chaque texte, indique pertinent=true seulement s'il mérite une lecture intégrale.\n\n"
        )
    elif stage == "screen":
        system = 'Tu es éditeur d’une veille environnementale. JSON valide uniquement.'
        intro = (
            f"{ABC_CONTEXT_SHORT}\n\n"
            "Présélectionne largement ces candidats à partir du titre et du court extrait. Garde "
            "les tendances, chiffres, études et évolutions sectorielles, mais aussi les cas concrets "
            "qui peuvent éclairer un risque opérationnel pour ABC ou ses clients : chaleur, manque "
            "d'eau, énergie, continuité d'activité, adaptation ou contraintes environnementales. "
            "Un cas local ou centré sur une entreprise peut mériter une lecture complète même s'il "
            "ne prouve pas une tendance générale.\n"
            "Écarte en présélection les annonces purement promotionnelles ou commerciales sans "
            "enseignement apparent, et les sujets manifestement hors périmètre. Ne déduis pas une "
            "pertinence du seul mot-clé. Les titres et extraits courts sont ambigus : en cas "
            "d'incertitude, garde le candidat pour vérification du contenu complet.\n"
            "Cette étape est un filtre de rappel : pertinent=true signifie seulement « à vérifier "
            "sur l'article complet », pas « à publier ». Aucun résumé ni score définitif ici.\n\n"
        )
    else:  # rss final review
        system = 'Tu es analyste climat et RSE pour ABC. JSON valide uniquement.'
        intro = (
            f"{ABC_CONTEXT_SHORT}\n\n"
            "Analyse CHACUN de ces articles et réponds avec un JSON contenant les résultats.\n\n"
        )
    
    # Ajoute chaque article au prompt
    article_sections = []
    for idx, art in enumerate(articles, 1):
        titre = art.get("titre", "")[:120]
        contenu = art.get("contenu", "")[:3000 if stage == "final" else 500]
        section = f"Texte {idx}: [TITRE] {titre} [EXTRAIT] {contenu}"
        themes = art.get("themes", [])
        if prompt_type == "rss" and themes:
            section += f" [THÈMES INDICATIFS DE LA SOURCE — ne suffisent pas seuls] {', '.join(themes[:12])}"
        if prompt_type == "jorf":
            section += " [EXTRAIT TRONQUÉ — présélection uniquement]"
        article_sections.append(section)

    prompt = intro + "\n\n".join(article_sections) + "\n\n"
    if prompt_type == "rss" and stage == "screen":
        prompt += 'Format: {"texte_1": {"pertinent": boolean}, "texte_2": {...}, ...}'
    else:
        prompt += 'Format: {"texte_1": {"pertinent": boolean, "resume": "...", "score": 1}, "texte_2": {...}, ...}'
    
    # Use the unified call_llm which will use Gemini by default
    raw = call_llm(prompt, system, max_tokens=1000)
    result = extract_json(raw)
    
    if not result:
        if prompt_type == "rss":
            raise ValueError("LLM batch RSS : réponse JSON absente ou invalide")
        return [{'pertinent': False, 'resume': '', 'score': 1} for _ in articles]
    
    # Parse les résultats
    outputs = []
    for idx in range(1, len(articles) + 1):
        key = f"texte_{idx}"
        if key in result:
            value = result[key]
            if prompt_type == "rss" and (
                not isinstance(value, dict)
                or not isinstance(value.get('pertinent'), bool)
            ):
                raise ValueError(f"LLM batch RSS : résultat invalide pour {key}")
            outputs.append(value)
        else:
            if prompt_type == "rss":
                raise ValueError(f"LLM batch RSS : résultat manquant pour {key}")
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
        "Lis l'intégralité du texte fourni avant de décider. Vérifie les dispositions, leur champ "
        "d'application, les dates, les conditions et les exceptions. Le résumé doit décrire précisément "
        "ce que le texte prévoit pour ABC, sans extrapoler ni reprendre uniquement son titre.\n\n"
        "Score (si pertinent) :\n"
        "  1 = information de veille\n"
        "  2 = évolution réglementaire à anticiper\n"
        "  3 = obligation immédiate ou risque direct\n\n"
        f"TITRE: {titre}\n"
        f"TEXTE INTÉGRAL:\n{contenu}\n\n"
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


def _categorise_rss_with_llm(titre: str, contenu: str) -> str:
    categories = ', '.join(CATEGORIES)
    prompt = (
        "Classe cet article selon son sujet principal. Ignore les mentions secondaires, les liens "
        "et les éléments de navigation. Réponds avec une catégorie de cette liste uniquement : "
        f"{categories}. En cas de doute, choisis Environnement.\n\n"
        f"TITRE: {titre}\n"
        f"CONTENU: {contenu[:600]}\n\n"
        'JSON: {"categorie": "une catégorie de la liste"}'
    )
    raw = call_llm(
        prompt,
        'Tu classes des articles de veille environnementale. JSON valide uniquement.',
        max_tokens=100,
        enrich=False,
    )
    return valid_category(extract_json(raw).get('categorie'))


def groq_analyse_rss(titre: str, contenu: str) -> dict:
    if reglements_match(titre):
        return {
            'pertinent': True,
            'score': 3,
            'resume': titre[:200],
            'pourquoi': 'Mention de réglementation prioritaire.',
            'categorie': _categorise_rss_with_llm(titre, contenu),
        }
    
    system = 'Tu es analyste climat et RSE pour ABC. JSON valide uniquement.'
    prompt = (
        f"{ABC_CONTEXT_SHORT}\n\n"
        "Tu sélectionnes des articles pour une veille environnementale utile à un responsable "
        "Environnement/Climat d'une entreprise de services. La veille doit à la fois donner du recul "
        "sur les tendances et faire remonter des cas concrets qui éclairent les risques et décisions "
        "opérationnels d'ABC ou de ses clients. Ne la limite pas aux études nationales ni aux articles "
        "qui prouvent une tendance statistique.\n\n"
        "RETENIR (score 1, « Articles thématiques ») si l'article apporte au moins un éclairage "
        "environnemental utile et crédible, notamment :\n"
        "- Une tendance, donnée agrégée ou historique, étude, comparaison, évolution sectorielle "
        "ou analyse de politique publique.\n"
        "- Un cas réel d'entreprise ou de site révélant une vulnérabilité, un risque opérationnel, "
        "une mesure d'adaptation ou une conséquence environnementale transposable à ABC ou à ses "
        "clients : par exemple une activité interrompue par la chaleur ou le manque d'eau, des "
        "contraintes sur l'énergie ou les matières, ou une adaptation face à un aléa climatique. "
        "Un cas documenté peut être utile même s'il est local et ne démontre pas à lui seul une "
        "tendance générale.\n"
        "- Une innovation ou pratique particulière si l'article donne un enseignement concret au-delà "
        "de la promotion d'un acteur.\n\n"
        "ÉCARTER les annonces d'entreprise purement promotionnelles ou commerciales (levée de fonds, "
        "nouveau contrat, lancement d'offre, partenariat ou test) quand elles n'apportent ni donnée "
        "sectorielle ni enseignement opérationnel. Écarter aussi le fait divers ou l'incident sans "
        "lien environnemental établi, les opinions sans éléments concrets, et les rapprochements avec "
        "ABC qui reposent seulement sur une association d'idées. La présence d'une entreprise, d'une "
        "ville ou d'un site dans le titre n'est jamais, à elle seule, un motif d'inclusion ou d'exclusion.\n\n"
        "Exemple de discernement : un article sur une usine de chips arrêtée lors d'une canicule faute "
        "d'eau est intéressant pour les risques de chaleur, de stress hydrique et de continuité "
        "d'activité, même si le cas est local ; il peut être retenu en score 1 sans prétendre qu'il "
        "prouve une tendance nationale. Une simple annonce de test d'un outil par Enedis ou RTE n'est "
        "pas suffisante, sauf si l'article explique ce qu'elle révèle d'une évolution plus large des "
        "réseaux ou de la flexibilité énergétique.\n\n"
        "Score 2 ou 3, « À suivre », uniquement si l'article établit une implication concrète et "
        "pour ABC, ses opérations ou ses clients. Une implication peut être un risque illustré par un "
        "cas concret ; il n'est pas nécessaire qu'elle constitue une obligation réglementaire :\n"
        "- 2 = évolution ou risque à anticiper, avec action plausible à identifier dans les 12 mois\n"
        "- 3 = obligation imminente, risque direct et significatif, ou opportunité stratégique claire\n"
        "Ne donne pas 2/3 sur la seule base d'un exemple anecdotique ou d'une conséquence hypothétique. "
        "Un cas de vulnérabilité peut rester score 1 s'il est instructif mais ne permet pas d'établir "
        "un risque direct pour ABC.\n\n"
        "Domaines à considérer : climat et adaptation, chaleur, eau et sécheresse, énergie, bâtiment "
        "et efficacité, mobilité, émissions, déchets et biodiversité ; enjeux clients et continuité "
        "des prestations compris. Ces thèmes orientent l'analyse mais ne suffisent pas à rendre un "
        "article pertinent.\n\n"
        "Rejeter les contenus clairement hors périmètre :\n"
        "- ICPE/SEVESO/REACH (ABC non exploitant), élections, géopolitique pure\n"
        "- Agriculture/pêche/sylviculture, immobilier résidentiel, finance de marché\n"
        "- Catastrophes naturelles à l'étranger sans lien réglementaire FR/UE\n"
        "- Faits divers, sport, défense, santé humaine sans angle entreprise\n"
        "Reste équilibré : ne transforme pas tout cas local en tendance générale, mais ne rejette pas "
        "un cas concret utile simplement parce qu'il est local ou centré sur une entreprise. Si le "
        "titre et l'extrait sont ambigus, utilise le contenu fourni avant de trancher ; ne conclus "
        "pas à partir du seul mot-clé ou du seul titre.\n\n"
        "Attribue aussi une seule catégorie selon le sujet principal de l'article, pas les mentions secondaires "
        "ou les mots présents dans des liens/navigation : "
        f"{', '.join(CATEGORIES)}. En cas de doute, choisis Environnement.\n\n"
        "Résumé : pour le score 1, donne en une phrase soit l'enseignement général, soit le risque ou "
        "l'adaptation que le cas concret met en lumière, sans extrapoler. Pour le score 2/3, explique "
        "en 1-2 phrases l'implication concrète pour ABC ou ses clients et distingue les faits de "
        "l'interprétation.\n\n"
        "Le contenu transmis peut être un extrait si la page complète est inaccessible ou trop longue. "
        "N'invente ni chiffres, ni causalité, ni implication non étayée. Si l'extrait ne suffit pas à "
        "confirmer un élément précis, reste prudent dans le résumé et le score plutôt que de le présenter "
        "comme un fait établi.\n\n"
        f"TITRE: {titre}\n"
        f"CONTENU: {contenu[:600]}\n\n"
        'JSON: {"pertinent": boolean, "resume": "résumé conforme aux règles ci-dessus (vide si non pertinent)", '
        '"pourquoi": "", "score": 1, "categorie": "..."}'
    )

    raw = call_llm(prompt, system, max_tokens=300, enrich=False)
    result = extract_json(raw)
    if not result:
        return {'pertinent': False, 'resume': '', 'pourquoi': '', 'score': 1}

    result['categorie'] = valid_category(result.get('categorie'))
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
    """Generate a daily briefing only from texts already confirmed as relevant."""
    if not articles:
        return ''

    candidates = [a for a in articles if not any(p in a['titre'].lower() for p in _BRIEFING_SKIP)]
    if not candidates:
        return ''

    lines = []
    for i, a in enumerate(candidates[:80]):
        nature = a.get('nature') or a.get('contenu', '').split(' — ')[0] or 'Texte'
        line = f"{i + 1}. [{nature}] {a['titre']}"
        if a.get('resume'):
            line += f"\nRésumé vérifié sur le texte intégral : {a['resume']}"
        if a.get('pourquoi'):
            line += f"\nImplication pour ABC : {a['pourquoi']}"
        lines.append(line)

    system = 'Tu es expert RSE. Réponds UNIQUEMENT avec ce JSON exact : {"briefing": "ton texte ici"}'
    prompt = (
        f"Textes du Journal Officiel du {today_str} :\n\n"
        + '\n'.join(lines)
        + "\n\nEn 2-3 phrases maximum, résume ce qui est significatif pour un "
        "Responsable Environnement/Climat d'une grande entreprise de services. "
        "N'ajoute aucune obligation ou conséquence qui n'est pas étayée par les résumés "
        "vérifiés fournis. "
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
