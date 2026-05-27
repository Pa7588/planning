# -*- coding: utf-8 -*-
import requests
from bs4 import BeautifulSoup
from datetime import datetime, date, timedelta
import re
import json

# ─── CONFIG ───────────────────────────────────────────────────────────────────

CIBLES_URG = [
    "M. Belhomme De Franqueville",
    "L. Okoyi Ossouka Mapangou",
    "E. Beros",
    "P. Messina",
    "J. Peyres",
    "S. Niane",
    "C. Vasseur",
    "J. Blanc",
    "A. Khadraoui",
    "M. Harhour",
    "E. Perthuisot",
    "R. Pebay",
    "I. Mokhtari",
    "A. Ponton",
    "E. Macabiau",
    "J. Langlois",
    "A. Wilhelm",
    "C. Viola",
]

CIBLES_GERIA = [
    "P. Lorette",
    "Y. Esteves",
    "E. Salgues",
    "S. Kassou",
    "T. Bourot",
    "M. Gratesac",
    "C. Gorra",
]

CIBLES = CIBLES_URG + CIBLES_GERIA

PLANNINGS = {
    "gardes_purpan":   "https://app.planning.lifen.health/external/plannings/513b393c3c6a11e88b24",
    "gardes_rangueil": "https://app.planning.lifen.health/external/plannings/647003302b22a53d19c1",
    "urgences":        "https://app.planning.lifen.health/external/plannings/d9620e86592712e23672",
    "urgences_jf":     "https://app.planning.lifen.health/external/plannings/78ef22ade246745d3835",
    "sauv":            "https://app.planning.lifen.health/external/plannings/bfe39d8a0bc17b5e8906",
    "geriatrie":       "https://app.planning.lifen.health/external/plannings/55ed4e1c59041a69a363",
}

MOIS_FR = ["Janvier","Février","Mars","Avril","Mai","Juin",
           "Juillet","Août","Septembre","Octobre","Novembre","Décembre"]
MOIS_NUM = {m: i+1 for i, m in enumerate(MOIS_FR)}

NON_ATTRIBUE = {"non attribué", "r. planning urg toulouse"}
JOURS_SEMAINE = {"lun","mar","mer","jeu","ven","sam","dim"}

PARASITES = {
    "lifen planning", "créez votre compte", "voir les échanges",
    "télécharger", "du", "au", "actions", "ajouter vos indisponibilités",
    "plannings", "actifs", "terminés", "publié et disponible",
    "© 2014", "centre d'aide", "contacter le support", "suggérer une évolution",
    "fr", "en", "tableau de bord", "agenda", "échanges", "disponibilités",
}

# Postes gériatrie reconnus
POSTES_GERIA = {
    "garonne-soins palliatifs",
    "garonne-soins palliatifs-pum",
    "pug-albarède",
    "pug albarède",
    "pug rangueil",
    "pug-rangueil jf",
}

# Jours fériés 2026 (date → True)
JOURS_FERIES = {
    date(2026, 5,  8),   # Victoire 1945
    date(2026, 5, 14),   # Ascension
    date(2026, 5, 25),   # Lundi de Pentecôte
    date(2026, 7, 14),   # Fête nationale
    date(2026, 8, 15),   # Assomption
    date(2026, 11, 1),   # Toussaint
}

DEBUG = True

# ─── HELPERS DATE ─────────────────────────────────────────────────────────────

def est_jour_ferie(d):
    return d in JOURS_FERIES

def est_weekend_ou_ferie(d):
    return d.weekday() >= 5 or est_jour_ferie(d)

def est_samedi(d):
    return d.weekday() == 6 - 1  # samedi = weekday 5

def est_dimanche_ou_ferie(d):
    return d.weekday() == 6 or est_jour_ferie(d)

# ─── CATÉGORISATION ───────────────────────────────────────────────────────────

def categoriser_urg(poste):
    """Catégorisation pour les postes urgences."""
    p = poste.lower()
    if re.search(r'\blds\b', p):
        return ('violet', 'lds')
    if 'nuit' in p:
        return ('jaune', 'garde')
    if re.search(r'13\s*h?\s*[-–]\s*8\s*h?', p) or '13-8' in p or '13 - 8' in p:
        return ('jaune', 'garde')
    if re.search(r'18\s*h?\s*[-–]\s*8\s*h?', p) or '18-8' in p or '18h-8h' in p:
        return ('jaune', 'garde')
    if re.search(r'8\s*h?\s*[-–]\s*13\s*h?', p) or '8-13' in p:
        return ('jaune', 'demi-garde')
    if re.search(r'13\s*h?\s*[-–]\s*18\s*h?', p) or '13-18' in p or '13h-18' in p:
        return ('jaune', 'demi-garde')
    return ('rouge', 'jour')

def categoriser_geria(poste, jour_date):
    """
    Catégorisation pour les postes gériatrie selon la date.
    - Semaine hors JF : stage jour + garde soir → orange + repos lendemain
    - Samedi : garde 13h-8h30 → jaune + repos lendemain
    - Dimanche ou JF : garde 24h mais indispo journée → rouge + repos lendemain
    """
    p = poste.lower()

    # PUG-Rangueil JF : traité comme dimanche/JF quelle que soit la date
    if 'jf' in p:
        return ('rouge', 'geria-jf')

    if est_dimanche_ou_ferie(jour_date):
        # Garde 24h mais journée bloquée → rouge
        return ('rouge', 'geria-dim')
    elif est_samedi(jour_date):
        # Garde 13h-8h30 → jaune
        return ('jaune', 'geria-sam')
    else:
        # Semaine : stage + garde soir → orange
        return ('orange', 'geria-semaine')

def repos_apres_garde_urg(poste, type_poste):
    if type_poste == 'demi-garde':
        return False
    p = poste.lower()
    if 'nuit' in p: return True
    if re.search(r'13\s*h?\s*[-–]\s*8\s*h?', p) or '13-8' in p or '13 - 8' in p: return True
    if re.search(r'18\s*h?\s*[-–]\s*8\s*h?', p) or '18-8' in p or '18h-8h' in p: return True
    if re.search(r'\blds\b', p): return True
    return False

def est_poste_geria(poste):
    return poste.lower().strip() in POSTES_GERIA

# ─── FETCH ────────────────────────────────────────────────────────────────────

def fetch_planning(nom, url):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.get(url, timeout=15, headers=headers)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        texte = soup.get_text('\n')
        if DEBUG:
            with open(f"debug_{nom}.txt", "w", encoding="utf-8") as f:
                f.write(texte)
        return texte
    except Exception as e:
        print(f"  ⚠ Erreur fetch {url}: {e}")
        return ""

# ─── PARSER ───────────────────────────────────────────────────────────────────

def est_nom_personne(s):
    return bool(re.match(r'^[A-Z]\.\s+[A-Z][a-zA-ZÀ-ÿ\s\-]+$', s))

def est_poste_urg(s):
    mots = s.lower()
    return any(k in mots for k in [
        'hub', 'amct', 'cmct', 'lds', 'sauv', 'ua ', 'ua/', 'nuit', 'jour',
        'sam ', 'we/', 'week-end', 'rééval', '8h', '13h', '18h',
        '8-13', '13-8', '18-8',
    ])

def est_poste_valide(s):
    return est_poste_urg(s) or est_poste_geria(s)

def parse_texte(texte):
    lignes = [l.strip() for l in texte.splitlines() if l.strip()]
    resultats = []
    mois_courant = None
    jour_courant = None
    poste_courant = None

    for ligne in lignes:
        ligne_low = ligne.lower()
        if ligne_low in PARASITES:
            continue
        if any(ligne_low.startswith(p) for p in [
            "du ", "au ", "© ", "gardes urg", "urgences", "sauv été",
            "hopital", "hôpital", "chu ", "gardes gér",
        ]):
            continue
        if re.match(r'^\d+\s+nouvelles?\s+', ligne_low):
            continue
        if ligne in MOIS_FR:
            mois_courant = ligne
            poste_courant = None
            continue
        if mois_courant is None:
            continue
        if re.match(r'^\d{1,2}$', ligne):
            jour_courant = int(ligne)
            poste_courant = None
            continue
        if ligne_low in JOURS_SEMAINE:
            continue
        if est_nom_personne(ligne):
            if poste_courant and jour_courant and mois_courant:
                if ligne.lower() not in NON_ATTRIBUE:
                    resultats.append({
                        "mois": mois_courant,
                        "jour": jour_courant,
                        "poste": poste_courant,
                        "personne": ligne,
                    })
            poste_courant = None
            continue
        if est_poste_valide(ligne):
            poste_courant = ligne
            continue

    return resultats

# ─── CONSTRUCTION DU PLANNING ─────────────────────────────────────────────────

def construire_planning(toutes_entrees):
    planning = {}
    for m in MOIS_FR:
        planning[m] = {}
        for d in range(1, 32):
            planning[m][d] = {
                nom: {"couleur": "vert", "postes": [], "repos": False}
                for nom in CIBLES
            }

    for e in toutes_entrees:
        nom = e["personne"]
        if nom not in CIBLES:
            continue
        mois, jour, poste = e["mois"], e["jour"], e["poste"]

        # Calcul de la date réelle pour la logique gériatrie
        try:
            jour_date = date(2026, MOIS_NUM[mois], jour)
        except ValueError:
            continue

        cell = planning[mois][jour][nom]

        # Choisir la logique selon le type de poste
        if est_poste_geria(poste):
            couleur, type_poste = categoriser_geria(poste, jour_date)
            genere_repos = True  # toujours en gériatrie
        else:
            couleur, type_poste = categoriser_urg(poste)
            genere_repos = repos_apres_garde_urg(poste, type_poste)

        # Remplissage de la cellule
        if cell["repos"]:
            if type_poste not in ('lds',):
                cell["postes"].append(f"⚠ {poste}")
                cell["couleur"] = "orange"
        elif not cell["postes"]:
            cell["couleur"] = couleur
            cell["postes"].append(poste)
        else:
            if poste not in cell["postes"]:
                cell["postes"].append(poste)
                cell["couleur"] = "orange"

        # Repos le lendemain
        if genere_repos:
            d_l = jour_date + timedelta(days=1)
            m_l = MOIS_FR[d_l.month - 1]
            j_l = d_l.day
            if m_l in planning and j_l in planning[m_l]:
                cell_r = planning[m_l][j_l][nom]
                if not cell_r["postes"]:
                    couleur_r = "violet-repos" if type_poste == 'lds' else "jaune-repos"
                    label_r = "Repos LDS" if type_poste == 'lds' else "Repos de garde"
                    cell_r["couleur"] = couleur_r
                    cell_r["postes"].append(label_r)
                    cell_r["repos"] = True

    return planning

# ─── GÉNÉRATION HTML ──────────────────────────────────────────────────────────

COULEURS = {
    "vert":         ("#1a6b3a", "#d4edda", "Libre"),
    "rouge":        ("#7f1d1d", "#fee2e2", "Poste de jour"),
    "jaune":        ("#78350f", "#fef3c7", "Garde"),
    "jaune-repos":  ("#78350f", "#fef9e7", "Repos de garde"),
    "violet":       ("#3b0764", "#ede9fe", "LDS"),
    "violet-repos": ("#3b0764", "#f5f3ff", "Repos LDS"),
    "orange":       ("#7c2d12", "#ffedd5", "Double poste / Stage + garde"),
}

def couleur_css(c):
    txt, bg, _ = COULEURS.get(c, ("#374151", "#f3f4f6", ""))
    return f"color:{txt};background:{bg};"

def nb_jours_mois(mois):
    if mois == "Février": return 28
    if mois in ["Avril","Juin","Septembre","Novembre"]: return 30
    return 31

def nom_court(n):
    parts = n.split(". ", 1)
    return parts[1].split()[0] if len(parts) > 1 else n

def generer_html(planning, date_maj):
    today = date.today()
    urg_set = set(CIBLES_URG)

    cibles_urg_json   = json.dumps(CIBLES_URG,  ensure_ascii=False)
    cibles_geria_json = json.dumps(CIBLES_GERIA, ensure_ascii=False)
    cibles_all_json   = json.dumps(CIBLES,       ensure_ascii=False)

    legende_html = "".join(
        f'<span class="leg-item" style="{couleur_css(c)}">{COULEURS[c][2]}</span>'
        for c in ["vert","rouge","jaune","jaune-repos","violet","violet-repos","orange"]
    )
    mois_tabs = "".join(
        f'<button class="tab-btn" onclick="showMonth(\'{m}\')" id="tab-{m}">{m}</button>'
        for m in MOIS_FR
    )

    # Checkboxes panneau personnalisé
    checkboxes_urg = ""
    for nom in CIBLES_URG:
        sid = re.sub(r'[\s\.\-]', '_', nom)
        checkboxes_urg += (
            f'<label class="cb-label" for="cb_{sid}">'
            f'<input type="checkbox" id="cb_{sid}" data-nom="{nom}" checked onchange="applyCustom()">'
            f'<span>{nom}</span></label>'
        )
    checkboxes_geria = ""
    for nom in CIBLES_GERIA:
        sid = re.sub(r'[\s\.\-]', '_', nom)
        checkboxes_geria += (
            f'<label class="cb-label" for="cb_{sid}">'
            f'<input type="checkbox" id="cb_{sid}" data-nom="{nom}" checked onchange="applyCustom()">'
            f'<span>{nom}</span></label>'
        )

    mois_sections = ""
    for mois in MOIS_FR:
        idx = MOIS_NUM[mois]
        try:
            premier = date(2026, idx, 1)
        except ValueError:
            continue
        decalage = premier.weekday()
        nb_j = nb_jours_mois(mois)
        a_donnees = any(
            planning[mois][d][n]["postes"]
            for d in range(1, nb_j+1)
            for n in CIBLES
        )
        jours_html = "".join('<div class="day-cell empty"></div>' for _ in range(decalage))

        for d in range(1, nb_j + 1):
            try:
                jour_date = date(2026, idx, d)
            except ValueError:
                continue
            is_we = jour_date.weekday() >= 5
            is_ferie = jour_date in JOURS_FERIES
            is_today = (jour_date == today)

            slots = ""
            for nom in CIBLES:
                cell = planning[mois][d][nom]
                c = cell["couleur"]
                label = " + ".join(cell["postes"]) if cell["postes"] else "Libre"
                prenom = nom_court(nom)
                groupe = "urg" if nom in urg_set else "geria"
                nom_attr = nom.replace('"', '&quot;')
                slots += (
                    f'<div class="slot {groupe}" data-nom="{nom_attr}" '
                    f'style="{couleur_css(c)}" title="{nom_attr} — {label}">'
                    f'<span class="slot-name">{prenom}</span>'
                    f'<span class="slot-poste">{label}</span></div>'
                )

            we_class     = " weekend" if is_we else ""
            ferie_class  = " ferie"   if is_ferie else ""
            today_class  = " today"   if is_today else ""
            today_badge  = '<span class="today-badge">Aujourd\'hui</span>' if is_today else ""
            ferie_badge  = '<span class="ferie-badge">JF</span>' if is_ferie else ""

            jours_html += (
                f'<div class="day-cell{we_class}{ferie_class}{today_class}">'
                f'<div class="day-num">{d}{ferie_badge}{today_badge}</div>{slots}</div>'
            )

        badge = "" if a_donnees else ' <span class="no-data">Pas de données</span>'
        mois_sections += f'''
        <div class="month-section" id="month-{mois}" style="display:none">
            <h2>{mois} 2026{badge}</h2>
            <div class="week-headers">
                <div>Lun</div><div>Mar</div><div>Mer</div>
                <div>Jeu</div><div>Ven</div>
                <div class="we-h">Sam</div><div class="we-h">Dim</div>
            </div>
            <div class="cal-grid">{jours_html}</div>
        </div>'''

    def _a_donnees(m):
        nb_j = nb_jours_mois(m)
        return any(planning[m][d][n]["postes"] for d in range(1, nb_j+1) for n in CIBLES)

    mois_courant = MOIS_FR[today.month - 1]
    mois_defaut = mois_courant if _a_donnees(mois_courant) else next(
        (m for m in MOIS_FR if _a_donnees(m)), MOIS_FR[0]
    )

    return f'''<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Planning</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Syne:wght@700;800&display=swap" rel="stylesheet">
<style>
:root {{ --bg:#0f1117; --surface:#1a1d27; --border:#2a2d3a; --text:#e8eaf0; --text-dim:#6b7280; --accent:#4f8ef7; --radius:8px; --today:#4f8ef7; }}
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ font-family:'DM Mono',monospace; background:var(--bg); color:var(--text); min-height:100vh; }}
.header {{ padding:24px 40px; border-bottom:1px solid var(--border); display:flex; align-items:center; gap:16px; flex-wrap:wrap; }}
.header h1 {{ font-family:'Syne',sans-serif; font-size:2rem; font-weight:800; letter-spacing:-0.02em; flex:1; min-width:120px; }}
.maj-badge {{ font-size:0.7rem; color:var(--text-dim); background:var(--surface); border:1px solid var(--border); padding:4px 10px; border-radius:20px; white-space:nowrap; }}
.filter-bar {{ display:flex; gap:6px; align-items:center; flex-wrap:wrap; }}
.filter-btn {{ font-family:'DM Mono',monospace; font-size:0.7rem; padding:6px 14px; border:1px solid var(--border); border-radius:20px; background:transparent; color:var(--text-dim); cursor:pointer; transition:all 0.15s; text-transform:uppercase; letter-spacing:0.08em; white-space:nowrap; }}
.filter-btn:hover {{ color:var(--text); border-color:var(--accent); }}
.filter-btn.active {{ color:white; border-color:transparent; }}
.filter-btn[data-f="tous"].active {{ background:#374151; }}
.filter-btn[data-f="urg"].active {{ background:#c0392b; }}
.filter-btn[data-f="geria"].active {{ background:#1a6b3a; }}
.filter-btn[data-f="custom"].active {{ background:#7c3aed; }}
.custom-panel {{ display:none; position:fixed; top:0; right:0; bottom:0; width:320px; background:var(--surface); border-left:1px solid var(--border); z-index:100; overflow-y:auto; padding:24px; box-shadow:-8px 0 32px rgba(0,0,0,0.4); }}
.custom-panel.open {{ display:block; }}
.panel-header {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; }}
.panel-header h3 {{ font-family:'Syne',sans-serif; font-size:1rem; font-weight:800; }}
.panel-close {{ background:none; border:none; color:var(--text-dim); font-size:1.2rem; cursor:pointer; padding:4px 8px; border-radius:4px; }}
.panel-close:hover {{ color:var(--text); background:var(--border); }}
.panel-group-title {{ font-size:0.65rem; text-transform:uppercase; letter-spacing:0.1em; color:var(--text-dim); margin:16px 0 8px; display:flex; align-items:center; gap:8px; }}
.panel-group-title::after {{ content:''; flex:1; height:1px; background:var(--border); }}
.panel-group-title.urg-title {{ color:#e87070; }}
.panel-group-title.geria-title {{ color:#6bcf8f; }}
.panel-actions {{ display:flex; gap:6px; margin-bottom:16px; flex-wrap:wrap; }}
.panel-action-btn {{ font-family:'DM Mono',monospace; font-size:0.65rem; padding:4px 10px; border:1px solid var(--border); border-radius:4px; background:transparent; color:var(--text-dim); cursor:pointer; }}
.panel-action-btn:hover {{ color:var(--text); border-color:var(--accent); }}
.cb-label {{ display:flex; align-items:center; gap:10px; padding:6px 8px; border-radius:6px; cursor:pointer; font-size:0.75rem; color:var(--text); transition:background 0.1s; }}
.cb-label:hover {{ background:var(--border); }}
.cb-label input {{ accent-color:var(--accent); width:14px; height:14px; cursor:pointer; flex-shrink:0; }}
.legende {{ padding:12px 40px; display:flex; gap:8px; flex-wrap:wrap; border-bottom:1px solid var(--border); }}
.leg-item {{ font-size:0.65rem; padding:3px 8px; border-radius:4px; font-weight:500; letter-spacing:0.05em; text-transform:uppercase; }}
.tabs {{ padding:12px 40px; display:flex; gap:6px; flex-wrap:wrap; border-bottom:1px solid var(--border); position:sticky; top:0; background:var(--bg); z-index:10; }}
.tab-btn {{ font-family:'DM Mono',monospace; font-size:0.7rem; padding:6px 14px; border:1px solid var(--border); border-radius:20px; background:transparent; color:var(--text-dim); cursor:pointer; transition:all 0.15s; text-transform:uppercase; letter-spacing:0.08em; }}
.tab-btn:hover {{ color:var(--text); border-color:var(--accent); }}
.tab-btn.active {{ background:var(--accent); color:white; border-color:var(--accent); }}
.content {{ padding:24px 40px 60px; }}
.month-section h2 {{ font-family:'Syne',sans-serif; font-size:1.4rem; font-weight:800; margin-bottom:16px; display:flex; align-items:center; gap:12px; }}
.no-data {{ font-family:'DM Mono',monospace; font-size:0.65rem; color:var(--text-dim); background:var(--surface); border:1px solid var(--border); padding:3px 8px; border-radius:4px; font-weight:400; }}
.week-headers {{ display:grid; grid-template-columns:repeat(7,1fr); gap:4px; margin-bottom:4px; }}
.week-headers div {{ text-align:center; font-size:0.65rem; color:var(--text-dim); text-transform:uppercase; letter-spacing:0.1em; padding:4px; }}
.week-headers .we-h {{ color:#6366f1; }}
.cal-grid {{ display:grid; grid-template-columns:repeat(7,1fr); gap:4px; }}
.day-cell {{ background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); padding:6px; min-height:120px; transition:border-color 0.15s; }}
.day-cell:hover {{ border-color:#3a3d4a; }}
.day-cell.empty {{ background:transparent; border-color:transparent; }}
.day-cell.weekend {{ border-color:#2a2d4a; background:#1a1d30; }}
.day-cell.ferie {{ border-color:#b45309; background:#1c1508; }}
.day-cell.today {{ border:2px solid var(--today); box-shadow:0 0 8px #4f8ef733; }}
.day-num {{ font-size:0.7rem; font-weight:500; color:var(--text-dim); margin-bottom:4px; display:flex; align-items:center; gap:4px; flex-wrap:wrap; }}
.day-cell.weekend .day-num {{ color:#6366f1; }}
.day-cell.ferie .day-num {{ color:#f59e0b; }}
.day-cell.today .day-num {{ color:var(--today); font-weight:700; }}
.today-badge {{ font-size:0.5rem; background:var(--today); color:white; padding:1px 4px; border-radius:3px; text-transform:uppercase; }}
.ferie-badge {{ font-size:0.5rem; background:#b45309; color:white; padding:1px 4px; border-radius:3px; text-transform:uppercase; }}
.slot {{ border-radius:4px; padding:3px 5px; margin-bottom:3px; font-size:0.62rem; display:flex; flex-direction:column; gap:1px; line-height:1.2; }}
.slot.hidden {{ display:none; }}
.slot.urg   {{ border-left:2px solid #c0392b55; }}
.slot.geria {{ border-left:2px solid #1a6b3a55; }}
.slot-name {{ font-weight:500; }}
.slot-poste {{ opacity:0.8; font-size:0.58rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.overlay {{ display:none; position:fixed; inset:0; background:rgba(0,0,0,0.4); z-index:99; }}
.overlay.open {{ display:block; }}
@media (max-width:900px) {{
  .header,.legende,.tabs,.content {{ padding-left:16px; padding-right:16px; }}
  .header h1 {{ font-size:1.4rem; }}
  .cal-grid {{ grid-template-columns:repeat(7,minmax(0,1fr)); gap:2px; }}
  .day-cell {{ padding:3px; min-height:90px; }}
  .slot {{ font-size:0.55rem; }}
  .slot-poste {{ display:none; }}
  .custom-panel {{ width:100%; }}
}}
</style>
</head>
<body>

<div class="overlay" id="overlay" onclick="closePanel()"></div>

<div class="custom-panel" id="customPanel">
  <div class="panel-header">
    <h3>Sélection</h3>
    <button class="panel-close" onclick="closePanel()">✕</button>
  </div>
  <div class="panel-actions">
    <button class="panel-action-btn" onclick="selectAll()">Tout cocher</button>
    <button class="panel-action-btn" onclick="selectNone()">Tout décocher</button>
    <button class="panel-action-btn" onclick="selectGroup('urg')">Urg seuls</button>
    <button class="panel-action-btn" onclick="selectGroup('geria')">Géria seuls</button>
  </div>
  <div class="panel-group-title urg-title">Urgences</div>
  <div id="cbsUrg">{checkboxes_urg}</div>
  <div class="panel-group-title geria-title">Gériatrie</div>
  <div id="cbsGeria">{checkboxes_geria}</div>
</div>

<div class="header">
  <h1>Planning</h1>
  <div class="filter-bar">
    <button class="filter-btn active" data-f="tous" onclick="setFilter('tous')">Tous</button>
    <button class="filter-btn" data-f="urg" onclick="setFilter('urg')">Urgences</button>
    <button class="filter-btn" data-f="geria" onclick="setFilter('geria')">Gériatrie</button>
    <button class="filter-btn" data-f="custom" onclick="openPanel()">✎ Personnalisé</button>
  </div>
  <div class="maj-badge">⟳ Mis à jour le {date_maj}</div>
</div>

<div class="legende">{legende_html}</div>
<div class="tabs">{mois_tabs}</div>
<div class="content">{mois_sections}</div>

<script>
const CIBLES_URG   = {cibles_urg_json};
const CIBLES_GERIA = {cibles_geria_json};
const CIBLES_ALL   = {cibles_all_json};

let currentFilter = localStorage.getItem('planning_filtre') || 'tous';
let customSet = new Set(JSON.parse(localStorage.getItem('planning_custom') || 'null') || CIBLES_ALL);

function applyVisibility(visibleSet) {{
  document.querySelectorAll('.slot').forEach(s => {{
    s.classList.toggle('hidden', !visibleSet.has(s.dataset.nom));
  }});
}}

function setFilter(f) {{
  currentFilter = f;
  localStorage.setItem('planning_filtre', f);
  document.querySelectorAll('.filter-btn').forEach(b => {{
    b.classList.toggle('active', b.dataset.f === f);
  }});
  if (f === 'tous')  applyVisibility(new Set(CIBLES_ALL));
  if (f === 'urg')   applyVisibility(new Set(CIBLES_URG));
  if (f === 'geria') applyVisibility(new Set(CIBLES_GERIA));
  if (f === 'custom') applyCustom();
}}

function applyCustom() {{
  customSet = new Set(
    [...document.querySelectorAll('#customPanel input[type=checkbox]')]
      .filter(cb => cb.checked).map(cb => cb.dataset.nom)
  );
  localStorage.setItem('planning_custom', JSON.stringify([...customSet]));
  applyVisibility(customSet);
}}

function openPanel() {{
  document.querySelectorAll('#customPanel input[type=checkbox]').forEach(cb => {{
    cb.checked = customSet.has(cb.dataset.nom);
  }});
  document.getElementById('customPanel').classList.add('open');
  document.getElementById('overlay').classList.add('open');
  document.querySelectorAll('.filter-btn').forEach(b => {{
    b.classList.toggle('active', b.dataset.f === 'custom');
  }});
  currentFilter = 'custom';
  localStorage.setItem('planning_filtre', 'custom');
  applyCustom();
}}

function closePanel() {{
  document.getElementById('customPanel').classList.remove('open');
  document.getElementById('overlay').classList.remove('open');
}}

function selectAll()  {{ document.querySelectorAll('#customPanel input').forEach(cb => cb.checked=true);  applyCustom(); }}
function selectNone() {{ document.querySelectorAll('#customPanel input').forEach(cb => cb.checked=false); applyCustom(); }}
function selectGroup(g) {{
  const s = new Set(g==='urg' ? CIBLES_URG : CIBLES_GERIA);
  document.querySelectorAll('#customPanel input').forEach(cb => {{ cb.checked = s.has(cb.dataset.nom); }});
  applyCustom();
}}

function showMonth(m) {{
  document.querySelectorAll('.month-section').forEach(el => el.style.display='none');
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  const sec=document.getElementById('month-'+m);
  const btn=document.getElementById('tab-'+m);
  if(sec) sec.style.display='block';
  if(btn) btn.classList.add('active');
  localStorage.setItem('planning_mois',m);
}}

const savedMois = localStorage.getItem('planning_mois');
showMonth(savedMois || '{mois_defaut}');
setFilter(currentFilter);
</script>
</body>
</html>'''


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    print("🏥 Génération du planning...")
    toutes_entrees = []
    for nom, url in PLANNINGS.items():
        print(f"  → Fetch {nom}...")
        texte = fetch_planning(nom, url)
        entrees = parse_texte(texte)
        cibles = [e for e in entrees if e["personne"] in CIBLES]
        print(f"     {len(entrees)} entrées, dont {len(cibles)} pour les cibles")
        if DEBUG and cibles:
            for e in cibles[:2]:
                print(f"       {e}")
        toutes_entrees.extend(entrees)

    print(f"\n  Total : {len(toutes_entrees)} entrées")
    for nom in CIBLES:
        n = sum(1 for e in toutes_entrees if e["personne"] == nom)
        if n > 0:
            print(f"    {nom}: {n} entrées")

    planning = construire_planning(toutes_entrees)
    date_maj = datetime.now().strftime("%d/%m/%Y à %H:%M")
    html = generer_html(planning, date_maj)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n✅ index.html généré ({len(html)//1024} Ko)")

if __name__ == "__main__":
    main()
