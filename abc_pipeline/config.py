"""
Static configuration for the ABC pipeline.

Dynamic values like paths and "today" are handled in `abc_pipeline.main`.
"""

# ─────────────────────────────────────────────
# External URLs
# ─────────────────────────────────────────────

JORF_BASE_URL = 'https://echanges.dila.gouv.fr/OPENDATA/JORF/'
VIGIEAU_DEPTS_URL = 'https://api.vigieau.gouv.fr/api/departements'
VIGIEAU_PMTILES_URL = 'https://regleau.s3.gra.perf.cloud.ovh.net/pmtiles/zones_arretes_en_vigueur.pmtiles'


# ─────────────────────────────────────────────
# LLM — providers and models
# ─────────────────────────────────────────────

MISTRAL_MODEL = 'mistral-small-latest'
MISTRAL_MIN_INTERVAL = 1.2  # 1 req/s max -> margin


# Google Gemini configuration
GEMINI_MODEL = 'gemini-3.5-flash-lite'
GEMINI_MODEL_FILTER = 'gemini-3.5-flash-lite'
GEMINI_MODEL_ENRICH = 'gemini-3.5-flash-lite'
GEMINI_MAX_RETRY = 3
GEMINI_RETRY_WAIT = 62
GEMINI_MIN_INTERVAL = 10.0  # seconds between calls (rate limit: 15 RPM)

# Legacy Groq configuration (kept for backwards compatibility)
GROQ_MODEL_FILTER = 'openai/gpt-oss-20b'
GROQ_MODEL_ENRICH = 'openai/gpt-oss-120b'
GROQ_MODEL = GROQ_MODEL_FILTER  # alias compat

GROQ_MAX_RETRY = 3
GROQ_RETRY_WAIT = 62
GROQ_MIN_INTERVAL = 10.0  # seconds between Groq calls


# ─────────────────────────────────────────────
# Timeouts
# ─────────────────────────────────────────────

TIMEOUT = 30


# ─────────────────────────────────────────────
# ABC context injected into prompts
# ─────────────────────────────────────────────

ABC_CONTEXT = """
ABC est un prestataire multiservices (propreté, maintenance, FM) qui intervient sur les
sites de ses clients — il n'est PAS exploitant ICPE.

Enjeux environnement / climat / carbone :
- Flotte de véhicules (scope 1 significatif) : électrification en cours
- Consommation énergétique locaux et prestations (eau, électricité, chauffage)
- Déchets produits lors des prestations (tri, traçabilité, éco-organismes)
- Obligation BGES et plan climat / trajectoire de réduction des émissions
- CSRD / reporting extra-financier (grande entreprise de services)
- Certifications ISO 14001, démarches RSE et bas-carbone

Risques indirects et chaîne de valeur :
- 3 000+ sites clients exposés aux aléas climatiques (inondations, sécheresse, canicules)
  → impacts sur volumes de prestations, contrats, résilience opérationnelle
- Exigences carbone croissantes des clients (nucléaire, santé, agroalimentaire, industrie)
  sur empreinte, réduction déchets, éco-efficacité des prestations
""".strip()

ABC_CONTEXT_SHORT = (
    "ABC : prestataire propreté/FM, intervient chez ses clients (non exploitant ICPE). "
    "Enjeux : BGES/plan climat, flotte véhicules (scope 1), énergie, déchets prestations, "
    "CSRD/reporting, ISO 14001. "
    "Risques indirects : aléas climatiques sur 3 000+ sites clients, "
    "exigences carbone des clients (nucléaire, santé, industrie)."
)


# ─────────────────────────────────────────────
# Réglementations prioritaires — score 3 automatique
# Tout article mentionnant l'un de ces termes dans son titre
# est forcé pertinent=True, score=3, sans appel LLM de filtrage.
# ─────────────────────────────────────────────

REGLEMENTS_PRIORITAIRES = [
    'CSRD', 'CSDDD', 'CS3D', 'SPASER', 'EUDR', 'CBAM', 'MACF',
    'ISSB', 'BGES', 'BEGES', 'DPEF', 'ZFE',
    'ISO 14001', 'PNACC', 'SNBC',
    'Loi Énergie-Climat', 'Loi Energie-Climat',
    'Décret Tertiaire', 'Decret Tertiaire',
    'Loi DDADUE', 'Omnibus', 'ESRS', 'SFDR', 'NFRD',
    'SBTi', 'ESPR',
    'Loi PACTE', 'Loi Devoir de Vigilance', 'Devoir de Vigilance',
    'Loi Climat et Résilience', 'Loi Climat Résilience',
    'Loi AGEC', 'Loi EGAlim', 'EGAlim',
    'Ordonnance 2023-1142', 'Décret 2023-1394',
    'Taxonomie Verte', 'Taxonomie verte',
    'Green Deal', 'Loi Industrie Verte', 'Industrie Verte',
    'Green Claims', 'Règlement Batteries', 'Nature Restoration',
    'RED III', 'RED 3',
]


# ─────────────────────────────────────────────
# Keywords — first-pass relevance filter
# ─────────────────────────────────────────────

KEYWORDS = [
    # Climat & décarbonation
    'climat', 'climatique', 'réchauffement', 'décarbonation', 'carbone',
    'GES', 'gaz à effet de serre', 'neutralité carbone', 'net zéro',
    'SNBC', 'PNACC', 'adaptation', 'atténuation', 'trajectoire',
    'transition énergétique', 'transition écologique',
    'Accord de Paris', 'COP', 'GIEC', 'IPCC',
    'décarboner', 'bas-carbone', 'scope 1', 'scope 2', 'scope 3',
    'bilan carbone', 'empreinte carbone', 'plan climat',
    'loi énergie', 'loi climat', 'France 2030',

    # Reporting & obligations ESG
    'CSRD', 'reporting extra-financier', 'taxonomie verte',
    'reporting environnemental', 'reporting durabilité',
    'bilan GES', 'BGES',

    # Énergie
    'énergie', 'efficacité énergétique', 'renouvelable', 'DPE',
    'bâtiment tertiaire', 'rénovation énergétique',

    # Déchets (angle prestataire de services)
    'déchet', 'déchets', 'REP', 'responsabilité élargie',
    'tri', 'recyclage', 'éco-organisme', 'traçabilité déchets',

    # Risques climatiques physiques
    'inondation', 'sécheresse', 'canicule', 'événement extrême',
    'submersion', 'feu de forêt', 'catastrophe naturelle', 'PPR',

    # Mobilité & flotte
    'véhicule électrique', 'électrification', 'flotte',
    'mobilité durable', 'ZFE',

    # English keywords (ESG Today, Guardian)
    'climate', 'carbon', 'decarbonization', 'decarbonisation', 'net zero',
    'renewable', 'sustainability', 'sustainable', 'ESG', 'CSRD',
    'carbon tax', 'carbon price', 'emissions', 'greenhouse gas',
    'energy transition', 'clean energy', 'electric vehicle',
    'waste management', 'circular economy', 'biodiversity',
    'flood', 'drought', 'heatwave', 'climate risk',
    'scope 1', 'scope 2', 'scope 3', 'carbon footprint',
]


# ─────────────────────────────────────────────
# RSS sources
# ─────────────────────────────────────────────

RSS_SOURCES = [
    {
        'name': 'Actu-Environnement',
        'url': 'https://www.actu-environnement.com/flux/rss/environnement/',
        'categorie': 'Presse',
        'fallback_crawl': 'https://www.actu-environnement.com/ae/news/',
    },
    {
        'name': 'Reporterre',
        'url': 'https://reporterre.net/spip.php?page=backend',
        'categorie': 'Presse',
        'fallback_crawl': 'https://reporterre.net/',
    },
    {
        'name': 'Novethic',
        'url': 'https://www.novethic.fr/feed',
        'categorie': 'Presse',
        'fallback_crawl': 'https://www.novethic.fr/',
    },
    {
        'name': 'ADEME',
        'url': 'https://www.ademe.fr/feed/',
        'categorie': 'Climat',
        'fallback_crawl': 'https://www.ademe.fr/actualites/',
    },
    {
        'name': 'Haut Conseil pour le Climat',
        'url': 'https://www.hautconseilclimat.fr/feed/',
        'categorie': 'Climat',
        'fallback_crawl': 'https://www.hautconseilclimat.fr/actualites/',
    },
    {
        'name': 'France Stratégie',
        'url': 'https://www.strategie.gouv.fr/rss.xml',
        'categorie': 'Climat',
        'fallback_crawl': 'https://www.strategie.gouv.fr/publications',
        'require_keywords': [
            'climat', 'carbone', 'transition', 'énergie', 'décarbonation',
            'environnement', 'adaptation', 'neutralité', 'SNBC', 'empreinte',
            'biodiversité', 'trajectoire', 'bas-carbone', 'GES',
        ],
    },
    {
        'name': 'The Shift Project',
        'url': 'https://theshiftproject.org/feed/',
        'categorie': 'Climat',
        'fallback_crawl': 'https://theshiftproject.org/articles/',
    },
    {
        'name': 'I4CE',
        'url': 'https://www.i4ce.org/feed/',
        'categorie': 'Climat',
        'fallback_crawl': 'https://www.i4ce.org/publications/',
        'require_keywords': [
            'climat', 'carbone', 'transition', 'financement', 'investissement',
            'décarbonation', 'politique climatique', 'trajectoire', 'adaptation',
        ],
    },
    {
        'name': 'Carbone 4',
        'url': 'https://www.carbone4.com/feed',
        'categorie': 'Climat',
        'fallback_crawl': 'https://www.carbone4.com/publications',
    },
    {
        'name': 'Politico Energy EU',
        'url': 'https://www.politico.eu/section/energy-fr/feed/',
        'categorie': 'Réglementation',
        'fallback_crawl': 'https://www.politico.eu/section/energy-fr/',
        'require_keywords': [
            'climat', 'énergie', 'carbone', 'taxonomie', 'CSRD', 'Green Deal',
            'transition', 'renouvelable', 'émissions', 'règlement', 'directive',
        ],
    },
    {
        'name': 'Contexte Environnement',
        'url': 'https://www.contexte.com/articles/rss/edition/environnement',
        'categorie': 'Réglementation',
        'fallback_crawl': 'https://www.contexte.com/environnement/',
    },
    {
        'name': 'GreenUnivers',
        'url': 'https://www.greenunivers.com/feed/',
        'categorie': 'Climat',
        'fallback_crawl': 'https://www.greenunivers.com/',
    },
    {
        'name': 'Le Monde Planète',
        'url': 'https://www.lemonde.fr/planete/rss_full.xml',
        'categorie': 'Presse',
        'fallback_crawl': 'https://www.lemonde.fr/planete/',
        'require_keywords': [
            'climat', 'énergie', 'carbone', 'transition', 'environnement',
            'décarbonation', 'renouvelable', 'CSRD', 'adaptation', 'émissions',
            'biodiversité', 'déchets', 'REP', 'sécheresse', 'inondation',
        ],
    },
    {
        'name': 'ESG Today',
        'url': 'https://www.esgtoday.com/feed/',
        'categorie': 'Réglementation',
        'fallback_crawl': 'https://www.esgtoday.com/',
        'require_keywords': [
            'CSRD', 'carbon', 'climate', 'ESG', 'sustainability', 'emissions',
            'decarbonization', 'net zero', 'renewable', 'scope', 'reporting',
            'taxonomy', 'circular economy', 'waste',
        ],
    },
    {
        'name': 'The Guardian Environment',
        'url': 'https://www.theguardian.com/environment/rss',
        'categorie': 'Climat',
        'fallback_crawl': 'https://www.theguardian.com/environment',
        'require_keywords': [
            'carbon', 'climate', 'emissions', 'net zero', 'decarbonization',
            'renewable energy', 'carbon price', 'CSRD', 'ESG', 'scope',
            'energy transition', 'electric vehicle', 'sustainability reporting',
            'flood', 'drought', 'heatwave',
        ],
    },
    {
        'name': 'EFRAG',
        'url': 'https://invalid-no-rss',
        'categorie': 'Réglementation',
        'fallback_crawl': 'https://www.efrag.org/en/news-and-calendar/news',
        'article_url_contains': '/news-and-calendar/news/',
        'require_keywords': [
            'ESRS', 'CSRD', 'sustainability reporting', 'standard', 'reporting',
            'climate', 'carbon', 'ESG', 'disclosure', 'due diligence',
        ],
    },
    {
        'name': 'AEF Développement Durable',
        'url': 'https://invalid-no-rss',
        'categorie': 'Réglementation',
        'playwright_crawl': 'https://news.google.com/publications/CAAqJAgKIh5DQklTRUFnTWFnd0tDbUZsWm1sdVptOHVabklvQUFQAQ?hl=fr&gl=FR&ceid=FR%3Afr',
        'require_keywords': [
            'climat', 'énergie', 'carbone', 'transition', 'environnement',
            'CSRD', 'RSE', 'décarbonation', 'renouvelable', 'déchets', 'REP',
        ],
    },
    {
        'name': 'Acteurs Publics',
        'url': 'https://invalid-no-rss',
        'categorie': 'Réglementation',
        'fallback_crawl': 'https://acteurspublics.fr/actualites/',
        'require_keywords': [
            'climat', 'énergie', 'carbone', 'transition', 'environnement',
            'CSRD', 'décarbonation', 'renouvelable', 'déchets',
        ],
    },
    {
        'name': 'Les Echos Environnement',
        'url': 'https://news.google.com/rss/search?q=site:lesechos.fr+%28environnement+OR+%22transition+%C3%A9nerg%C3%A9tique%22+OR+carbone+OR+CSRD+OR+RSE+OR+%22%C3%A9nergies+renouvelables%22%29&hl=fr&gl=FR&ceid=FR:fr',
        'categorie': 'Presse',
        'require_keywords': [
            'climat', 'énergie', 'carbone', 'transition', 'environnement',
            'CSRD', 'RSE', 'décarbonation', 'renouvelable', 'déchets', 'REP',
        ],
    },
    {
        'name': 'Actuel HSE',
        'url': 'https://invalid-no-rss',
        'categorie': 'Réglementation',
        'fallback_crawl': 'https://www.actuel-hse.fr/?edition_date={today}',
        'article_url_contains': '/content/',
        'require_keywords': [
            'climat', 'énergie', 'carbone', 'transition', 'environnement',
            'CSRD', 'RSE', 'décarbonation', 'renouvelable', 'déchets', 'REP',
            'ISO 14001', 'bilan GES', 'BGES', 'émissions', 'pollution',
            'biodiversité', 'eau', 'sécheresse', 'inondation',
        ],
    },
    {
        'name': 'Fédération de la Propreté',
        'url': 'https://www.federation-proprete.com/feed/',
        'categorie': 'Presse',
        'require_keywords': [
            'climat', 'énergie', 'carbone', 'transition', 'environnement',
            'CSRD', 'RSE', 'décarbonation', 'renouvelable', 'déchets', 'REP',
            'ISO 14001', 'bilan GES', 'BGES', 'émissions', 'pollution',
            'véhicule électrique', 'flotte', 'mobilité', 'développement durable',
        ],
    },
]


# ─────────────────────────────────────────────
# VigiEau
# ─────────────────────────────────────────────

NIVEAUX_ORDRE = {
    'vigilance': 1,
    'alerte': 2,
    'alerte renforcée': 3,
    'crise': 4,
}

DEPT_NOMS = {
    '01': 'Ain', '02': 'Aisne', '03': 'Allier', '04': 'Alpes-de-Haute-Provence', '05': 'Hautes-Alpes',
    '06': 'Alpes-Maritimes', '07': 'Ardèche', '08': 'Ardennes', '09': 'Ariège', '10': 'Aube',
    '11': 'Aude', '12': 'Aveyron', '13': 'Bouches-du-Rhône', '14': 'Calvados', '15': 'Cantal',
    '16': 'Charente', '17': 'Charente-Maritime', '18': 'Cher', '19': 'Corrèze', '2A': 'Corse-du-Sud',
    '2B': 'Haute-Corse', '21': "Côte-d'Or", '22': "Côtes-d'Armor", '23': 'Creuse', '24': 'Dordogne',
    '25': 'Doubs', '26': 'Drôme', '27': 'Eure', '28': 'Eure-et-Loir', '29': 'Finistère',
    '30': 'Gard', '31': 'Haute-Garonne', '32': 'Gers', '33': 'Gironde', '34': 'Hérault',
    '35': 'Ille-et-Vilaine', '36': 'Indre', '37': 'Indre-et-Loire', '38': 'Isère', '39': 'Jura',
    '40': 'Landes', '41': 'Loir-et-Cher', '42': 'Loire', '43': 'Haute-Loire', '44': 'Loire-Atlantique',
    '45': 'Loiret', '46': 'Lot', '47': 'Lot-et-Garonne', '48': 'Lozère', '49': 'Maine-et-Loire',
    '50': 'Manche', '51': 'Marne', '52': 'Haute-Marne', '53': 'Mayenne', '54': 'Meurthe-et-Moselle',
    '55': 'Meuse', '56': 'Morbihan', '57': 'Moselle', '58': 'Nièvre', '59': 'Nord',
    '60': 'Oise', '61': 'Orne', '62': 'Pas-de-Calais', '63': 'Puy-de-Dôme', '64': 'Pyrénées-Atlantiques',
    '65': 'Hautes-Pyrénées', '66': 'Pyrénées-Orientales', '67': 'Bas-Rhin', '68': 'Haut-Rhin', '69': 'Rhône',
    '70': 'Haute-Saône', '71': 'Saône-et-Loire', '72': 'Sarthe', '73': 'Savoie', '74': 'Haute-Savoie',
    '75': 'Paris', '76': 'Seine-Maritime', '77': 'Seine-et-Marne', '78': 'Yvelines', '79': 'Deux-Sèvres',
    '80': 'Somme', '81': 'Tarn', '82': 'Tarn-et-Garonne', '83': 'Var', '84': 'Vaucluse',
    '85': 'Vendée', '86': 'Vienne', '87': 'Haute-Vienne', '88': 'Vosges', '89': 'Yonne',
    '90': 'Territoire de Belfort', '91': 'Essonne', '92': 'Hauts-de-Seine', '93': 'Seine-Saint-Denis',
    '94': 'Val-de-Marne', '95': "Val-d'Oise", '971': 'Guadeloupe', '972': 'Martinique',
    '973': 'Guyane', '974': 'La Réunion', '976': 'Mayotte',
}


# ─────────────────────────────────────────────
# Parlement (AN)
# ─────────────────────────────────────────────

PARLEMENT_MAX_GROQ = 10

STADES_ORDRE = [
    'Dépôt',
    'Commission',
    'Première lecture à l\'Assemblée nationale',
    'Première lecture au Sénat',
    'Commission mixte paritaire',
    'Deuxième lecture au Sénat',
    'Deuxième lecture à l\'Assemblée nationale',
    'Texte définitivement adopté',
    'Promulgation',
]

AN_BASE = 'https://www2.assemblee-nationale.fr'

AN_SCRAPER_SOURCES = [
    {
        'name': 'AN Projets de loi',
        'url': (f'{AN_BASE}/documents/liste'
                '?limit=30&type=projets-loi&legis=17&type_tri=DATE_MISE_LIGNE'),
    },
    {
        'name': 'AN Textes adoptés',
        'url': (f'{AN_BASE}/documents/liste'
                '?limit=30&type=ta&legis=17&type_tri=DATE_MISE_LIGNE'),
    },
]

STADE_PATTERNS = [
    ('Promulgué',  ['promulgu', 'loi n°', 'parue au jo', 'publiée au jo']),
    ('Adopté',     ['adopté définitivement', 'adoption définitive', 'texte adopté',
                    'définitivement adopté', 'loi adoptée', 'adoptée par',
                    'vote solennel', 'vote conforme']),
    ('Commission mixte paritaire', ['commission mixte paritaire', 'cmp']),
    ('Sénat 2ème lecture', ['sénat en deuxième lecture', 'deuxième lecture au sénat']),
    ('AN 2ème lecture', ['assemblée nationale en deuxième lecture', '2e lecture', 'deuxième lecture', 'nouvelle lecture']),
    ('Sénat 1ère lecture', ['sénat', 'haute assemblée', 'première lecture au sénat']),
    ('Séance publique AN', ['séance publique', 'hémicycle', 'discussion générale', 'examen en séance']),
    ('Commission', [
        'en commission',
        'examiné par la commission',
        'rapporteur désigné',
        'renvoyé en commission',
        'commission saisie',
    ]),
]

