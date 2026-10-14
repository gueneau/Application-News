import logging
import sys
from datetime import datetime
from pathlib import Path

from .output import write_output
from .sources.an_agenda import fetch_an_agenda
from .sources.jorf import fetch_jorf
from .sources.rss import fetch_rss
from .sources.vigieau import fetch_vigieau, fetch_vigieau_zones
from .config import GEMINI_MODEL
from .llm import get_llm_stats


def _setup_logging(log_path: Path):
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler(log_path, encoding='utf-8'),
            logging.StreamHandler(sys.stdout),
        ],
    )


def main() -> int:
    today_str = datetime.now().strftime('%Y-%m-%d')

    # The package lives in `NEWS/abc_pipeline`, so the workspace root is parents[1].
    script_dir = Path(__file__).resolve().parents[1]
    log_path = script_dir / 'pipeline.log'
    _setup_logging(log_path)
    log = logging.getLogger(__name__)

    log.info('=' * 60)
    log.info(f'Pipeline ABC Veille Environnementale — {today_str}')
    log.info(f'Modèle LLM : {GEMINI_MODEL} via Google Gemini API')
    log.info('=' * 60)

    start = datetime.now()
    errors = []
    meta_errors = []
    items = []
    stats = {}
    source_counts = {}
    restrictions = []
    jorf_autres = []
    jorf_items = []
    briefing_jorf = []
    jorf_issue_date = None

    # 1. JORF
    try:
        (
            jorf_items,
            jorf_autres,
            jorf_total,
            briefing_jorf,
            jorf_issue_date,
        ) = fetch_jorf(today_str)
        stats['jo_analyses'] = jorf_total
        stats['jo_retenus'] = len(jorf_items)
        source_counts['JORF'] = len(jorf_items)
        if len(jorf_items) == 0:
            log.warning("WARNING: SOURCE VIDE — JORF")
            meta_errors.append("SOURCE VIDE — JORF : 0 textes retenus")
    except Exception as e:
        log.error(f"JORF fatal : {e}")
        errors.append('JORF')
        meta_errors.append(f"JORF fatal : {e}")
        stats.update({'jo_analyses': 0, 'jo_retenus': 0})
        source_counts['JORF'] = 0

    # 2. RSS / Presse
    try:
        rss_items = fetch_rss(today_str, script_dir)
        items.extend(rss_items)
        source_counts['RSS'] = len(rss_items)
        if len(rss_items) == 0:
            log.warning("WARNING: SOURCE VIDE — RSS")
            meta_errors.append("SOURCE VIDE — RSS : 0 articles retenus")
    except Exception as e:
        log.error(f"RSS fatal : {e}")
        errors.append('RSS')
        meta_errors.append(f"RSS fatal : {e}")
        source_counts['RSS'] = 0

    # 3. VigiEau
    try:
        restrictions = fetch_vigieau()
        stats['depts_restriction'] = len(restrictions)
        source_counts['VigiEau'] = len(restrictions)
        # Supabase: zone-level sync for persistence and change tracking
        try:
            from .supabase_sync import SupabaseSync
            zones = fetch_vigieau_zones()
            SupabaseSync().sync_water_restrictions(zones)
        except Exception as e_sync:
            log.warning(f"VigiEau Supabase sync skipped : {e_sync}")
    except Exception as e:
        log.error(f"VigiEau fatal : {e}")
        errors.append('VigiEau')
        meta_errors.append(f"VigiEau fatal : {e}")
        stats['depts_restriction'] = 0
        source_counts['VigiEau'] = 0

    # 4. Ordre du jour Assemblée nationale
    an_agenda = []
    try:
        an_agenda = fetch_an_agenda(today_str)
        source_counts['AN Agenda'] = len(an_agenda)
        if len(an_agenda) == 0:
            log.warning("WARNING: SOURCE VIDE — AN Agenda")
            meta_errors.append("SOURCE VIDE — AN Agenda : 0 séance")
    except Exception as e:
        log.error(f"AN Agenda fatal : {e}")
        errors.append('AN Agenda')
        meta_errors.append(f"AN Agenda fatal : {e}")
        source_counts['AN Agenda'] = 0

    # Dedup by id then by normalized title (catches Google News duplicates)
    seen_ids = set()
    seen_titles = set()
    unique = []
    for item in sorted(
        items,
        key=lambda x: (x.get('date', '2000-01-01'), x.get('criticite', 1)),
        reverse=True,
    ):
        norm = ' '.join(item.get('titre', '').lower().split())[:80]
        if item['id'] not in seen_ids and norm not in seen_titles:
            seen_ids.add(item['id'])
            if norm:
                seen_titles.add(norm)
            unique.append(item)

    elapsed = round((datetime.now() - start).total_seconds())
    llm_stats = get_llm_stats()
    pipeline_meta = {
        'run_at': datetime.now().isoformat(timespec='seconds'),
        'sources': source_counts,
        'errors': meta_errors,
        'llm_calls': llm_stats['calls'],
        'llm_seconds': llm_stats['seconds'],
    }
    json_data = {
        'date': today_str,
        'generated_at': datetime.now().strftime('%H:%M'),
        'elapsed_seconds': elapsed,
        'errors': errors,
        'stats': stats,
        'pipeline_meta': pipeline_meta,
        'items': unique,
        'restrictions_eau': restrictions,
        'jo_autres': jorf_autres,
        'jo_retenus': jorf_items,
        'jorf_issue_date': jorf_issue_date,
        'briefing_jorf': briefing_jorf,
        'an_agenda': an_agenda,
    }

    log.info(
        f"JSON : {len(unique)} items RSS, {len(jorf_items)} items JORF, "
        f"{len(restrictions)} depts eau"
    )
    write_output(script_dir, json_data, today_str)

    duration = (datetime.now() - start).total_seconds()
    status = 'OK' if not errors else 'PARTIEL'
    log.info(f"Pipeline terminé en {duration:.1f}s — {status}")
    return 0 if not errors else 1


if __name__ == '__main__':
    sys.exit(main())
