import hashlib

from .config import KEYWORDS, REGLEMENTS_PRIORITAIRES

CATEGORIES = (
    'Climat',
    'ICPE',
    'Eau',
    'Énergie',
    'Biodiversité',
    'Déchets',
    'Émissions',
    'Environnement',
)


def make_id(source: str, titre: str) -> str:
    return hashlib.md5(f"{source}:{titre}".encode()).hexdigest()[:12]


def reglements_match(text: str) -> str | None:
    """Return the first matching priority regulation keyword found in text, or None."""
    t = (text or '').lower()
    for kw in REGLEMENTS_PRIORITAIRES:
        if kw.lower() in t:
            return kw
    return None


def keyword_match(text: str) -> bool:
    t = (text or '').lower()
    return any(kw.lower() in t for kw in KEYWORDS)


def categorise(text: str) -> str:
    t = (text or '').lower()
    if any(
        k in t
        for k in [
            'snbc', 'pnacc', 'accord de paris', 'cop ', 'giec', 'ipcc',
            'neutralité carbone', 'net zéro', 'trajectoire carbone',
            'plan national adaptation', 'stratégie nationale bas-carbone',
            'canicule', 'inondation', 'feu de forêt', 'submersion',
            'événement extrême', 'catastrophe climatique', 'réchauffement',
            'catastrophe naturelle',
        ]
    ):
        return 'Climat'
    if any(
        k in t
        for k in [
            'csrd', 'taxonomie', 'reporting durabilité', 'devoir de vigilance',
            'décarbonation', 'bilan carbone', 'scope', 'bas-carbone',
            'transition énergétique', 'transition écologique',
        ]
    ):
        return 'Climat'
    if any(k in t for k in ['icpe', 'installation classée', 'seveso', 'autorisation', 'enregistrement']):
        return 'ICPE'
    if any(k in t for k in ['eau', 'rejet', 'assainissement', 'captage', 'nappe', 'sécheresse']):
        return 'Eau'
    if any(k in t for k in ['énergie', 'dpe', 'thermique', 'renouvelable', 'carbone', 'ges']):
        return 'Énergie'
    if any(k in t for k in ['biodiversité', 'espèce', 'natura', 'faune', 'flore', 'erc']):
        return 'Biodiversité'
    if any(k in t for k in ['déchet', 'rep', 'vhu', 'tri', 'recyclage', 'traitement']):
        return 'Déchets'
    if any(k in t for k in ['émission', 'cov', 'pollution', 'bruit', 'air', 'formaldéhyde']):
        return 'Émissions'
    return 'Environnement'


def valid_category(value: object) -> str:
    """Return a supported LLM category, or a neutral category if it is invalid."""
    if isinstance(value, str) and value.strip() in CATEGORIES:
        return value.strip()
    return 'Environnement'
