import csv
import io
import logging
import re

import requests

from ..config import AN_AGENDA_URL, AN_AGENDA_MAX_SESSIONS, TIMEOUT

log = logging.getLogger(__name__)


def fetch_an_agenda(today_str: str) -> list:
    """
    Upcoming public-session agenda ("ordre du jour en séance publique") from
    the Assemblée nationale's own open data export — a plain CSV, not a
    scrape of their dynamic site. Dates are already ISO (YYYY-MM-DD), so no
    date-guessing heuristics are needed.
    """
    log.info("=== Ordre du jour AN ===")
    try:
        resp = requests.get(AN_AGENDA_URL, timeout=TIMEOUT)
        resp.raise_for_status()
        text = resp.content.decode('latin-1')

        reader = csv.DictReader(io.StringIO(text), delimiter=';')
        rows = []
        for row in reader:
            date = (row.get('Date') or '').strip()
            if date < today_str:
                continue
            # The CSV separates distinct agenda items within one slot with runs
            # of whitespace (no delimiter character) — normalize to " · ".
            ordre_du_jour = re.sub(r'\s{2,}', ' · ', (row.get('Ordre(s) du jour') or '').strip())
            rows.append({
                'date': date,
                'heure': (row.get('Heure') or '').strip(),
                'ordre_du_jour': ordre_du_jour,
            })

        rows.sort(key=lambda r: (r['date'], r['heure']))
        rows = rows[:AN_AGENDA_MAX_SESSIONS]
        log.info(f"Ordre du jour AN : {len(rows)} séance(s) à venir")
        return rows
    except Exception as e:
        log.warning(f"Ordre du jour AN error : {e}")
        return []
