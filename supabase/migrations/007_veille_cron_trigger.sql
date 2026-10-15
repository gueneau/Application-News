-- ═══════════════════════════════════════════════════════════════
-- Reliable cron trigger for the "Veille Environnementale ABC" GitHub
-- Actions pipeline, replacing its native `schedule:` trigger.
--
-- Why: GitHub Actions' built-in `schedule:` cron is documented as
-- best-effort and observed on this repo to drop the large majority of
-- its ticks (veille.yml's own 4x/day cron and flux-live.yml's 30-min
-- cron both showed multi-hour gaps instead of firing on time).
-- workflow_dispatch (an on-demand API call), by contrast, has fired
-- reliably every time it's been used manually on this repo.
--
-- This moves the actual scheduling into Postgres (pg_cron), which
-- ticks every hour and only dispatches the workflow when the current
-- Paris local time matches 8h or 14h — computed via Postgres's own
-- IANA timezone database, so it self-adjusts for CET/CEST twice a
-- year with no manual cron edits.
--
-- MANUAL STEP (do NOT commit a real token to this file): before this
-- migration is useful, store a GitHub PAT in Vault via the SQL Editor:
--
--   select vault.create_secret(
--     '<fine-grained PAT, "Actions: Read and write" on gueneau/Application-News only>',
--     'github_actions_pat',
--     'Used by cron.schedule(''veille-trigger'') to call workflow_dispatch on veille.yml'
--   );
--
-- The same PAT already created for the Flux Live trigger Edge Function
-- can be reused (same repo, same scope) — paste it here too, or mint a
-- fresh one with the same permissions.
-- ═══════════════════════════════════════════════════════════════

create extension if not exists pg_cron with schema extensions;
create extension if not exists pg_net with schema extensions;
-- supabase_vault is enabled by default on every Supabase project.

select cron.schedule(
  'veille-trigger',
  '5 * * * *',  -- every hour at :05 (any fixed UTC minute works; the Paris-hour check below is what actually gates it)
  $$
  do $job$
  begin
    if extract(hour from (now() at time zone 'Europe/Paris'))::int in (8, 14) then
      perform net.http_post(
        url := 'https://api.github.com/repos/gueneau/Application-News/actions/workflows/veille.yml/dispatches',
        headers := jsonb_build_object(
          'Authorization', 'Bearer ' || (
            select decrypted_secret from vault.decrypted_secrets where name = 'github_actions_pat'
          ),
          'Accept', 'application/vnd.github+json',
          'Content-Type', 'application/json'
        ),
        body := jsonb_build_object('ref', 'main')
      );
    end if;
  end;
  $job$;
  $$
);
