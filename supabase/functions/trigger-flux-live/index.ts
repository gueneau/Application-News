// Supabase Edge Function — proxies the GitHub "Flux Live RSS" workflow_dispatch.
//
// Why this exists: triggering a GitHub Actions workflow from the browser needs
// a token with `actions:write`. That token can NOT live in index.html (public
// static site, source visible to everyone) — it would leak instantly. This
// function holds the token server-side as a Supabase secret and is the only
// thing allowed to call GitHub on the button's behalf.
//
// Deploy:
//   npx supabase functions deploy trigger-flux-live --project-ref fsucjfaogepwvczdyikz
//   npx supabase secrets set GITHUB_TOKEN=ghp_xxx --project-ref fsucjfaogepwvczdyikz
//
// GITHUB_TOKEN must be a fine-grained PAT scoped ONLY to this repository
// (gueneau/Application-News) with "Actions: Read and write" permission —
// nothing else. Create it at https://github.com/settings/personal-access-tokens.

const GITHUB_OWNER = 'gueneau';
const GITHUB_REPO = 'Application-News';
const WORKFLOW_FILE = 'flux-live.yml';
const MIN_INTERVAL_MS = 2 * 60 * 1000; // refuse re-trigger within 2 minutes

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
  });
}

Deno.serve(async (req: Request) => {
  if (req.method === 'OPTIONS') return new Response('ok', { headers: CORS_HEADERS });
  if (req.method !== 'POST') return json({ error: 'Method not allowed' }, 405);

  const token = Deno.env.get('GITHUB_TOKEN');
  if (!token) return json({ error: 'GITHUB_TOKEN non configuré côté serveur.' }, 500);

  const ghHeaders = {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': 'flux-live-trigger',
  };
  const base = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${WORKFLOW_FILE}`;

  try {
    // Avoid spamming the Actions API / GitHub rate limits from a publicly
    // reachable endpoint: refuse if a run started very recently.
    const runsResp = await fetch(`${base}/runs?per_page=1`, { headers: ghHeaders });
    if (runsResp.ok) {
      const runs = await runsResp.json();
      const latest = runs.workflow_runs?.[0];
      if (latest) {
        const age = Date.now() - new Date(latest.created_at).getTime();
        if (latest.status === 'in_progress' || latest.status === 'queued') {
          return json({ error: 'Un rafraîchissement est déjà en cours.' }, 429);
        }
        if (age < MIN_INTERVAL_MS) {
          const waitSec = Math.ceil((MIN_INTERVAL_MS - age) / 1000);
          return json({ error: `Trop tôt, réessaie dans ${waitSec}s.` }, 429);
        }
      }
    }

    const dispatchResp = await fetch(`${base}/dispatches`, {
      method: 'POST',
      headers: { ...ghHeaders, 'Content-Type': 'application/json' },
      body: JSON.stringify({ ref: 'main' }),
    });

    if (dispatchResp.status !== 204) {
      const detail = await dispatchResp.text();
      return json({ error: `GitHub API error (${dispatchResp.status}): ${detail}` }, 502);
    }

    return json({ ok: true, message: 'Rafraîchissement déclenché.' }, 202);
  } catch (e) {
    return json({ error: String(e) }, 500);
  }
});
