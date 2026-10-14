"""
Supabase sync for VigiEau water restriction zones (Veille ABC).

Usage:
  from .supabase_sync import SupabaseSync
  sync = SupabaseSync()                    # connects lazily; safe if env vars absent
  sync.sync_water_restrictions(zones)      # upsert/delete restriction zones + events
"""

import logging
import os

log = logging.getLogger(__name__)


class SupabaseSync:
    def __init__(self):
        self._client = None
        self._ready = False
        self._init()

    def _init(self):
        url = os.environ.get('SUPABASE_URL', '').strip()
        key = os.environ.get('SUPABASE_ANON_KEY', '').strip()
        if not url or not key:
            log.info("Supabase: variables SUPABASE_URL / SUPABASE_ANON_KEY absentes — sync désactivé")
            return
        try:
            from supabase import create_client
            self._client = create_client(url, key)
            self._ready = True
            log.info("Supabase: client initialisé")
        except ImportError:
            log.warning("Supabase: package 'supabase' non installé — sync désactivé")
        except Exception as e:
            log.warning(f"Supabase: échec d'initialisation — {e}")

    @property
    def ready(self) -> bool:
        return self._ready

    def sync_water_restrictions(self, zones: list) -> int:
        """
        Sync active restriction zones to Supabase.

        First run  : inserts all zones.
        Next runs  : upserts changed zones (level changed → est_nouveau=True),
                     deletes zones that disappeared from PMTiles (lifted restrictions).
        Unchanged zones are left untouched — preserves est_nouveau and date_maj.

        Returns number of rows inserted/updated/deleted.
        """
        if not self._ready:
            return 0
        from datetime import datetime, timezone
        try:
            existing = (
                self._client.table('restrictions_eau')
                .select('code_zone,niveau_actuel,nom_zone,departement,type_eau')
                .execute()
            )
            current = {r['code_zone']: r for r in (existing.data or [])}
            incoming_codes = {z['code_zone'] for z in zones if z.get('code_zone')}
            zones_by_code = {z['code_zone']: z for z in zones if z.get('code_zone')}

            now_iso = datetime.now(timezone.utc).isoformat()
            to_upsert = []
            events = []

            for z in zones:
                code = z.get('code_zone', '')
                if not code:
                    continue
                prev = current.get(code)
                if prev and prev['niveau_actuel'] == z['niveau_actuel']:
                    continue
                # New zone or level change
                niveau_precedent = prev['niveau_actuel'] if prev else None
                to_upsert.append({
                    'code_zone':       code,
                    'type_eau':        z['type_eau'],
                    'nom_zone':        z.get('nom_zone', ''),
                    'departement':     z.get('departement', ''),
                    'niveau_actuel':   z['niveau_actuel'],
                    'niveau_precedent': niveau_precedent,
                    'date_maj':        now_iso,
                    'url_arrete':      z.get('url_arrete', ''),
                    'est_nouveau':     True,
                })
                events.append({
                    'code_zone':   code,
                    'nom_zone':    z.get('nom_zone', ''),
                    'departement': z.get('departement', ''),
                    'type_eau':    z['type_eau'],
                    'event_type':  'changed' if niveau_precedent else 'added',
                    'niveau_avant': niveau_precedent,
                    'niveau_apres': z['niveau_actuel'],
                    'created_at':  now_iso,
                })

            if to_upsert:
                self._client.table('restrictions_eau').upsert(
                    to_upsert, on_conflict='code_zone'
                ).execute()

            # Reset est_nouveau=False for unchanged zones
            unchanged = [
                code for code, row in current.items()
                if code in incoming_codes and row['niveau_actuel'] == zones_by_code[code]['niveau_actuel']
            ]
            if unchanged:
                self._client.table('restrictions_eau').update(
                    {'est_nouveau': False}
                ).in_('code_zone', unchanged).eq('est_nouveau', True).execute()

            # Delete lifted zones + log 'ended' events
            stale = [code for code in current if code not in incoming_codes]
            if stale:
                for code in stale:
                    row = current[code]
                    events.append({
                        'code_zone':   code,
                        'nom_zone':    row.get('nom_zone', ''),
                        'departement': row.get('departement', ''),
                        'type_eau':    row.get('type_eau', ''),
                        'event_type':  'ended',
                        'niveau_avant': row['niveau_actuel'],
                        'niveau_apres': None,
                        'created_at':  now_iso,
                    })
                self._client.table('restrictions_eau').delete().in_('code_zone', stale).execute()

            if events:
                self._client.table('restrictions_eau_events').insert(events).execute()

            total = len(to_upsert) + len(stale)
            log.info(f"Supabase water sync: {len(to_upsert)} upsert, {len(stale)} levées, {len(unchanged)} inchangées, {len(events)} events")
            return total

        except Exception as e:
            log.warning(f"Supabase sync_water_restrictions: {e}")
            return 0
