import datetime
import math
import os
from pathlib import Path
import altair as alt
import folium
import pandas as pd
import requests
import streamlit as st
from streamlit_folium import st_folium
from streamlit_searchbox import st_searchbox
from xml.etree import ElementTree as ET

st.set_page_config(
    page_title="ROADRIC - Road-Trip Moto",
    page_icon="🏍️",
    layout="wide",
)

st.markdown(
    """
    <style>
    @media (max-width: 768px) {
        .block-container { padding: 1rem 0.75rem 3rem; }
        .st-key-resume_metrics [data-testid="stHorizontalBlock"] {
            flex-wrap: wrap !important;
            gap: 0.5rem !important;
        }
        .st-key-resume_metrics [data-testid="stColumn"] {
            flex: 1 1 calc(50% - 0.5rem) !important;
            min-width: calc(50% - 0.5rem) !important;
        }
        .st-key-resume_metrics [data-testid="stMetric"] { padding: 0.55rem; }
        .st-key-resume_metrics [data-testid="stMetricLabel"] { font-size: 0.78rem; }
        .st-key-resume_metrics [data-testid="stMetricValue"] { font-size: 1.2rem; }
        .stButton > button, [data-testid="stLinkButton"] > a { min-height: 46px; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🏍️ ROADRIC — Générateur de Road-Trip Moto")


@st.dialog("🏍️ Bienvenue sur ROADRIC")
def afficher_bienvenue():
    st.write("Préparez votre prochaine balade moto en quelques instants.")
    st.caption("Développé par Eric, un passionné de moto.")
    st.markdown(
        "- **Aller simple** : choisissez un départ, une arrivée et, si besoin, une étape.\n"
        "- **Balade en boucle** : indiquez une durée et une direction.\n"
        "- Les autoroutes sont évitées pour privilégier les routes de balade."
    )
    st.markdown("---")
    st.caption("🏍️ Club moto à découvrir")
    st.link_button(
        "Pleins Phares 82",
        "https://www.facebook.com/groups/656601229660372/",
        use_container_width=True,
    )
    if st.button("🏁 Commencer", use_container_width=True):
        st.session_state["bienvenue_vue"] = True
        st.rerun()


if not st.session_state.get("bienvenue_vue", False):
    afficher_bienvenue()

# -----------------------------------------------------------------------------
# FONCTIONS DE GÉOCODAGE ET CALCULS
# -----------------------------------------------------------------------------
def haversine_distance(coord1, coord2):
    R = 6371000
    lat1, lon1 = math.radians(coord1[0]), math.radians(coord1[1])
    lat2, lon2 = math.radians(coord2[0]), math.radians(coord2[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


@st.cache_data(ttl=86400)
def rechercher_villes(nom_ville):
    """Renvoie les communes françaises les plus proches de la saisie."""
    if not nom_ville or not nom_ville.strip():
        return []

    url_ban = "https://api-adresse.data.gouv.fr/search/"
    params_ban = {"q": nom_ville.strip(), "type": "municipality", "limit": 6}

    try:
        resp = requests.get(url_ban, params=params_ban, timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            villes = []
            for feature in data.get("features", []):
                coords = feature["geometry"]["coordinates"]
                props = feature["properties"]

                nom_trouve = props.get("city", props.get("label", ""))
                code_dept = props.get("context", "")
                postcode = props.get("postcode", "")
                label_affiche = f"{nom_trouve} ({postcode[:2]})" if postcode else nom_trouve
                villes.append({
                    "nom": nom_trouve,
                    "postcode": postcode,
                    "departement": code_dept,
                    "label": label_affiche,
                    "lat": coords[1],
                    "lon": coords[0],
                })
            return villes
    except Exception:
        pass

    return []


def geocode_ville_details(nom_ville):
    """Géocodage ciblé sur les communes françaises."""
    villes = rechercher_villes(nom_ville)
    if villes:
        details = villes[0]
        return (details["lat"], details["lon"]), details
    return None, {}


def proposer_villes(saisie):
    """Format attendu par la case d'autocomplétion intégrée."""
    if len(saisie.strip()) < 3:
        return []
    return [
        (
            f"{ville['nom']} ({ville['postcode']}) — {ville['departement']}",
            f"{ville['nom']} {ville['postcode']}",
        )
        for ville in rechercher_villes(saisie)
    ]


def saisir_ville_avec_suggestions(libelle, valeur_defaut, cle):
    """Une seule case : saisie libre et propositions dans sa liste déroulante."""
    return st_searchbox(
        proposer_villes,
        label=libelle,
        placeholder="Tapez au moins 3 lettres…",
        default=valeur_defaut,
        default_searchterm=valeur_defaut,
        default_use_searchterm=True,
        clear_on_submit=False,
        edit_after_submit="option",
        debounce=250,
        key=f"{cle}_ville",
        help="Tapez le nom de la ville puis choisissez une proposition dans cette même case.",
    )


def nommer_coordonnee(lat, lon):
    """Trouve la commune la plus proche pour une coordonnée donnée."""
    url = f"https://api-adresse.data.gouv.fr/reverse/?lon={lon}&lat={lat}"
    try:
        resp = requests.get(url, timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            features = data.get("features", [])
            if features:
                props = features[0]["properties"]
                city = props.get("city", props.get("nom", "Secteur inconnu"))
                dept = props.get("postcode", "")[:2]
                return f"{city} ({dept})" if dept else city
    except Exception:
        pass
    return f"Secteur {lat:.2f}, {lon:.2f}"


# -----------------------------------------------------------------------------
# ROUTING & DÉCOUPE DES PAUSES / STATIONS TOUS LES 200 KM
# -----------------------------------------------------------------------------
def router_sans_autoroute(waypoints, zones_a_eviter=None):
    """Itinéraire BRouter, sans autoroutes ni voies express assimilées."""
    url = "https://brouter.de/brouter"
    params = {
        "lonlats": "|".join(f"{lon},{lat}" for lat, lon in waypoints),
        "profile": "car-eco",
        "alternativeidx": 0,
        "format": "geojson",
        # Le profil car-eco traite aussi les voies motorroad=yes comme à éviter.
        "profile:avoid_motorways": 1,
    }
    if zones_a_eviter:
        # -1 crée une zone infranchissable ; les extrémités restent toutefois
        # utilisables comme départ et arrivée par BRouter.
        params["nogos"] = "|".join(
            f"{lon},{lat},{rayon_m},-1" for lat, lon, rayon_m in zones_a_eviter
        )

    erreurs = []
    for _ in range(2):
        try:
            resp = requests.get(
                url,
                params=params,
                headers={"User-Agent": "ROADRIC/1.0"},
                timeout=45,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("features"):
                return None, 0, 0, "BRouter n'a trouvé aucun itinéraire compatible."

            feature = data["features"][0]
            geometry = feature["geometry"]["coordinates"]
            props = feature["properties"]
            coords = [(pt[1], pt[0]) for pt in geometry]
            return (
                coords,
                float(props["track-length"]) / 1000.0,
                float(props["total-time"]) / 60.0,
                None,
            )
        except (requests.RequestException, ValueError, KeyError) as exc:
            erreurs.append(str(exc))

    return None, 0, 0, "BRouter est temporairement indisponible. Réessayez dans un instant."


def obtenir_cle_graphhopper():
    """Lit la clé administrateur sans jamais l'afficher dans l'interface."""
    cle = os.getenv("GRAPHHOPPER_API_KEY", "")
    if cle:
        return cle.strip()
    for nom_fichier in (".env", "graphhopper.env"):
        try:
            for ligne in Path(__file__).with_name(nom_fichier).read_text(encoding="utf-8").splitlines():
                if ligne.startswith("GRAPHHOPPER_API_KEY="):
                    return ligne.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            pass
    try:
        return str(st.secrets.get("GRAPHHOPPER_API_KEY", "")).strip()
    except Exception:
        return ""


def router_boucle_graphhopper(depart, duree_cible_min, cap_initial):
    """Génère un circuit en excluant les autoroutes et grands axes."""
    cle_api = obtenir_cle_graphhopper()
    if not cle_api:
        return None, 0, 0, (
            "Le mode boucle nécessite la clé administrateur GraphHopper. "
            "Ajoutez GRAPHHOPPER_API_KEY dans le fichier .env."
        )

    distance_m = int(max(20000, min(300000, duree_cible_min / 60 * 45000)))
    # Les classes OSM MOTORWAY, TRUNK et PRIMARY couvrent les autoroutes et
    # l'essentiel des voies rapides / nationales. « 0 » les rend interdites,
    # ce n'est pas une simple préférence d'itinéraire.
    payload = {
        "points": [[depart[1], depart[0]]],
        "profile": "car",
        "algorithm": "round_trip",
        "round_trip.distance": distance_m,
        "round_trip.seed": int(abs(depart[0] * 1000 + depart[1] * 10000 + cap_initial)),
        # GraphHopper utilise ce cap pour orienter le début du circuit.
        "heading": cap_initial,
        "heading_penalty": 600,
        "points_encoded": False,
        "locale": "fr",
        "custom_model": {
            "priority": [
                {
                    "if": "road_class == MOTORWAY || road_class == TRUNK || road_class == PRIMARY",
                    "multiply_by": "0",
                }
            ]
        },
    }
    try:
        response = requests.post(
            "https://graphhopper.com/api/1/route",
            params={"key": cle_api},
            json=payload,
            timeout=45,
        )
        data = response.json()
        if response.status_code == 200 and data.get("paths"):
            path = data["paths"][0]
            coords = [(point[1], point[0]) for point in path["points"]["coordinates"]]
            return coords, path["distance"] / 1000.0, path["time"] / 60000.0, None
        message = data.get("message", "réponse inattendue")
        if message == "Connection between locations not found":
            message = (
                "aucune boucle n'est possible sans emprunter un axe interdit depuis ce départ. "
                "Essayez un départ placé sur une petite route."
            )
        return None, 0, 0, f"GraphHopper : {message}"
    except (requests.RequestException, ValueError, KeyError) as exc:
        return None, 0, 0, f"Connexion à GraphHopper impossible : {exc}"


def point_a_distance(lat, lon, distance_km, cap_deg):
    """Calcule un point à une distance et un cap donnés depuis le départ."""
    rayon_terre_km = 6371.0
    distance_angulaire = distance_km / rayon_terre_km
    cap = math.radians(cap_deg)
    lat1, lon1 = math.radians(lat), math.radians(lon)

    lat2 = math.asin(
        math.sin(lat1) * math.cos(distance_angulaire)
        + math.cos(lat1) * math.sin(distance_angulaire) * math.cos(cap)
    )
    lon2 = lon1 + math.atan2(
        math.sin(cap) * math.sin(distance_angulaire) * math.cos(lat1),
        math.cos(distance_angulaire) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lon2)


def router_boucle_par_troncons(waypoints, zones_a_eviter):
    """Assemble une boucle tronçon par tronçon pour empêcher les raccourcis."""
    coords_total, distance_totale, duree_totale = [], 0.0, 0.0

    for i in range(len(waypoints) - 1):
        coords, distance_km, duree_min, erreur = router_sans_autoroute(
            [waypoints[i], waypoints[i + 1]], zones_a_eviter=zones_a_eviter
        )
        if erreur or not coords:
            return None, 0, 0, erreur or "Un tronçon de la boucle est introuvable."

        # Le premier point du tronçon suivant est identique au dernier précédent.
        coords_total.extend(coords if i == 0 else coords[1:])
        distance_totale += distance_km
        duree_totale += duree_min

    return coords_total, distance_totale, duree_totale, None


def segments_se_croisent(a, b, c, d):
    """Détecte le croisement de deux segments géographiques simplifiés."""
    def orientation(p, q, r):
        valeur = (q[1] - p[1]) * (r[0] - q[0]) - (q[0] - p[0]) * (r[1] - q[1])
        if abs(valeur) < 1e-12:
            return 0
        return 1 if valeur > 0 else 2

    o1, o2 = orientation(a, b, c), orientation(a, b, d)
    o3, o4 = orientation(c, d, a), orientation(c, d, b)
    return o1 != o2 and o3 != o4 and 0 not in (o1, o2, o3, o4)


def compter_croisements(coords):
    """Compte les croisements d'un tracé, hors segments voisins."""
    if len(coords) < 5:
        return 0

    pas = max(1, len(coords) // 280)
    points = coords[::pas]
    if points[-1] != coords[-1]:
        points.append(coords[-1])

    croisements = 0
    for i in range(len(points) - 1):
        for j in range(i + 3, len(points) - 1):
            # Ignore le contact naturel entre le tout premier et dernier segment.
            if i == 0 and j == len(points) - 2:
                continue
            if segments_se_croisent(points[i], points[i + 1], points[j], points[j + 1]):
                croisements += 1
    return croisements


@st.cache_data(ttl=86400)
def chercher_villes_a_eviter(lat, lon, rayon_m):
    """Retourne des zones d'évitement pour les villes et bourgs proches."""
    query = f'''[out:json][timeout:20];
    node["place"~"city|town"](around:{rayon_m},{lat},{lon});
    out tags;'''
    try:
        response = requests.get(
            "https://overpass-api.de/api/interpreter",
            params={"data": query},
            headers={"User-Agent": "ROADRIC/1.0"},
            timeout=35,
        )
        response.raise_for_status()
        zones = []
        for element in response.json().get("elements", []):
            tags = element.get("tags", {})
            # Rayon plus large pour une ville que pour un bourg.
            rayon_zone = 6000 if tags.get("place") == "city" else 2500
            zones.append((element["lat"], element["lon"], rayon_zone))
        return zones[:30]
    except (requests.RequestException, ValueError, KeyError):
        return []


def calculer_boucle_sans_autoroute(depart, duree_cible_min):
    """Construit une boucle et sélectionne la variante qui se croise le moins."""
    # 45 km/h est une moyenne adaptée à une balade sur des routes secondaires.
    distance_cible_km = duree_cible_min / 60 * 45
    rayon_km = max(8, min(85, distance_cible_km / 5.6))
    villes = chercher_villes_a_eviter(
        depart[0], depart[1], int(max(25000, min(120000, rayon_km * 1700)))
    )

    # Six points répartis sur le pourtour évitent les grandes branches qui se
    # rejoignent au milieu : chaque côté du circuit est calculé séparément.
    caps = (20, 80, 140, 200, 260, 320)
    points_intermediaires = [
        point_a_distance(depart[0], depart[1], rayon_km, cap) for cap in caps
    ]
    waypoints = [depart, *points_intermediaires, depart]
    rayon_nogo_m = int(max(3500, min(20000, rayon_km * 700)))
    resultat = router_boucle_par_troncons(
        waypoints,
        zones_a_eviter=[(depart[0], depart[1], rayon_nogo_m), *villes],
    )
    coords, _, _, erreur = resultat
    if erreur or not coords:
        return None, 0, 0, erreur or "Impossible de générer une boucle continue."
    return resultat


def creer_gpx(coords, nom="Roadric - Road-trip moto"):
    """Construit un fichier GPX téléchargeable à partir du tracé calculé."""
    gpx = ET.Element(
        "gpx",
        {
            "version": "1.1",
            "creator": "ROADRIC",
            "xmlns": "http://www.topografix.com/GPX/1/1",
        },
    )
    trk = ET.SubElement(gpx, "trk")
    ET.SubElement(trk, "name").text = nom
    segment = ET.SubElement(trk, "trkseg")

    for lat, lon in coords:
        ET.SubElement(segment, "trkpt", {"lat": str(lat), "lon": str(lon)})

    return ET.tostring(gpx, encoding="utf-8", xml_declaration=True)


def calculer_étapes_pauses_et_plein(coords, dist_totale_km, duree_min_totale, heure_dep, intervalle_plein_km=200):
    """
    Calcule les points d'arrêt pour :
    - Pauses carburant tous les X km (200km par défaut)
    - Pause café (~toutes les 1h30 de roulage)
    - Pause repas STRICTEMENT programmée si le trajet franchit la plage 12h00-13h00
    """
    if not coords or len(coords) < 2:
        return [], []

    etapes_pauses = []
    stations_recommandees = []

    cumul_dist = 0.0
    dernier_plein_km = 0.0
    prochain_cafe_km = 90.0
    repas_place = False

    dt_dep = datetime.datetime.combine(datetime.date.today(), heure_dep)

    for i in range(1, len(coords)):
        d = haversine_distance(coords[i-1], coords[i]) / 1000.0
        cumul_dist += d

        ratio_progression = cumul_dist / dist_totale_km
        minutes_ecoulees = ratio_progression * duree_min_totale
        dt_estime = dt_dep + datetime.timedelta(minutes=minutes_ecoulees)

        # PAUSE REPAS STRICTEMENT ENTRE 12H ET 13H
        if (12 <= dt_estime.hour < 13) and not repas_place:
            pt = coords[i]
            nom_lieu = nommer_coordonnee(pt[0], pt[1])
            etapes_pauses.append({
                "type": "🍽️ Pause Repas (12h - 13h)",
                "km": round(cumul_dist, 1),
                "heure_estimee": dt_estime.strftime("%Hh%M"),
                "lieu": nom_lieu,
                "lat": pt[0],
                "lon": pt[1]
            })
            repas_place = True
            prochain_cafe_km = cumul_dist + 100.0

        # PAUSE CAFÉ / DÉTENTE
        elif cumul_dist >= prochain_cafe_km and cumul_dist < (dist_totale_km - 20):
            if not (11 <= dt_estime.hour < 12 and dt_estime.minute >= 45):
                pt = coords[i]
                nom_lieu = nommer_coordonnee(pt[0], pt[1])
                etapes_pauses.append({
                    "type": "☕ Pause Café / Détente",
                    "km": round(cumul_dist, 1),
                    "heure_estimee": dt_estime.strftime("%Hh%M"),
                    "lieu": nom_lieu,
                    "lat": pt[0],
                    "lon": pt[1]
                })
                prochain_cafe_km = cumul_dist + 90.0

    return etapes_pauses, stations_recommandees


def obtenir_denivele_et_virages(coords):
    if not coords or len(coords) < 3:
        return 0, 0, 0, []

    step = max(1, len(coords) // 50)
    sampled_coords = coords[::step]
    if coords[-1] not in sampled_coords:
        sampled_coords.append(coords[-1])

    locations = [{"latitude": lat, "longitude": lon} for lat, lon in sampled_coords]

    denivele_pos = 0
    denivele_neg = 0
    elevations = []

    try:
        resp = requests.post(
            "https://api.open-elevation.com/api/v1/lookup",
            json={"locations": locations},
            timeout=6,
        )
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            elevations = [r["elevation"] for r in results]

            for i in range(1, len(elevations)):
                diff = elevations[i] - elevations[i - 1]
                if diff > 0:
                    denivele_pos += diff
                else:
                    denivele_neg += abs(diff)
    except Exception:
        pass

    virages_count = 0
    for i in range(len(coords) - 2):
        lat1, lon1 = coords[i]
        lat2, lon2 = coords[i + 1]
        lat3, lon3 = coords[i + 2]

        angle1 = math.atan2(lat2 - lat1, lon2 - lon1)
        angle2 = math.atan2(lat3 - lat2, lon3 - lon2)
        diff_deg = abs(math.degrees(angle2 - angle1))

        if 25 < diff_deg < 160:
            virages_count += 1

    pct_virages = min(100, int((virages_count / len(coords)) * 100 * 2.5))

    return denivele_pos, denivele_neg, pct_virages, elevations


def chercher_pistes_trail_overpass(center_lat, center_lon, rayon_m=15000):
    query = f"""
    [out:json][timeout:8];
    (
      way["highway"="track"](around:{rayon_m},{center_lat},{center_lon});
      way["surface"~"unpaved|compacted|gravel|dirt|earth"](around:{rayon_m},{center_lat},{center_lon});
    );
    out body geom 30;
    """
    url = "https://overpass-api.de/api/interpreter"
    try:
        resp = requests.post(url, data={"data": query}, timeout=8)
        if resp.status_code == 200:
            elements = resp.json().get("elements", [])
            pistes_coords = []
            for el in elements:
                if "geometry" in el:
                    piste = [(pt["lat"], pt["lon"]) for pt in el["geometry"]]
                    pistes_coords.append(piste)
            return pistes_coords
    except Exception:
        pass
    return []


def chercher_stations_essence_overpass(coords, rayon_m=8000, intervalle_km=12):
    """Recherche de vraies stations-service OpenStreetMap près du tracé."""
    if not coords:
        return [], "Tracé indisponible pour rechercher des stations."

    # Échantillonne le tracé pour garder une requête Overpass raisonnable.
    points = [coords[0]]
    distance = 0.0
    for i in range(1, len(coords)):
        distance += haversine_distance(coords[i - 1], coords[i]) / 1000
        if distance >= intervalle_km:
            points.append(coords[i])
            distance = 0.0
    if points[-1] != coords[-1]:
        points.append(coords[-1])

    zones = "".join(
        f'nwr["amenity"="fuel"](around:{rayon_m},{lat},{lon});'
        for lat, lon in points[:70]
    )
    # L'ordre `tags center` est important dans la syntaxe Overpass.
    query = f"[out:json][timeout:25];({zones});out tags center;"
    data = None
    erreurs = []
    for url in (
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
    ):
        try:
            # L'instance publique principale accepte plus fiablement les
            # requêtes Overpass en GET ; POST peut répondre 406 sans résultat.
            response = requests.get(
                url,
                params={"data": query},
                headers={"User-Agent": "ROADRIC/1.0"},
                timeout=45,
            )
            response.raise_for_status()
            data = response.json()
            break
        except (requests.RequestException, ValueError) as exc:
            erreurs.append(str(exc))

    if data is None:
        return [], "La recherche des stations est temporairement indisponible (Overpass)."

    stations, vus = [], set()
    for element in data.get("elements", []):
        identifiant = (element.get("type"), element.get("id"))
        if identifiant in vus:
            continue
        vus.add(identifiant)

        centre = element.get("center", element)
        lat, lon = centre.get("lat"), centre.get("lon")
        if lat is None or lon is None:
            continue

        tags = element.get("tags", {})
        stations.append({
            "nom": tags.get("name") or tags.get("brand") or "Station-service",
            "marque": tags.get("brand", ""),
            "adresse": " ".join(filter(None, [
                tags.get("addr:housenumber"),
                tags.get("addr:street"),
                tags.get("addr:postcode"),
                tags.get("addr:city"),
            ])),
            "horaires": tags.get("opening_hours", "Horaires non renseignés"),
            "lat": lat,
            "lon": lon,
        })
    if not stations:
        return [], "Aucune station OpenStreetMap trouvée à moins de 8 km du tracé."
    return stations, None


def positionner_stations_sur_trajet(stations, coords):
    """Ajoute le kilométrage de trajet et l'écart au tracé à chaque station."""
    cumul = [0.0]
    for i in range(1, len(coords)):
        cumul.append(cumul[-1] + haversine_distance(coords[i - 1], coords[i]) / 1000)

    resultat = []
    for station in stations:
        index_proche, distance_min = min(
            (
                (i, haversine_distance((station["lat"], station["lon"]), point) / 1000)
                for i, point in enumerate(coords)
            ),
            key=lambda item: item[1],
        )
        station = station.copy()
        station["km"] = round(cumul[index_proche], 1)
        station["ecart_route_km"] = round(distance_min, 1)
        resultat.append(station)
    return resultat


def recommander_stations(stations, distance_totale_km, autonomie_km):
    """Choisit des vraies stations atteignables avant chaque plein."""
    stations = sorted(stations, key=lambda station: station["km"])
    recommandations, alertes = [], []
    dernier_plein = 0.0

    while distance_totale_km - dernier_plein > autonomie_km:
        limite = dernier_plein + autonomie_km
        accessibles = [
            station for station in stations
            if dernier_plein + 5 < station["km"] <= limite
        ]
        if not accessibles:
            alertes.append(
                f"Aucune station OSM recensée entre le km {dernier_plein:.0f} "
                f"et le km {limite:.0f}."
            )
            break

        # Priorité à une station vers 80 % du plein, sans compromettre l'étape suivante.
        objectif = min(
            limite,
            max(
                dernier_plein + autonomie_km * 0.8,
                distance_totale_km - autonomie_km,
            ),
        )
        station = min(accessibles, key=lambda item: abs(item["km"] - objectif))
        recommandations.append(station)
        dernier_plein = station["km"]

    return recommandations, alertes


# -----------------------------------------------------------------------------
# BARRE LATÉRALE
# -----------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Configuration Itinéraire")

    type_itineraire = st.radio(
        "🧭 Type d'itinéraire",
        options=["➡️ Aller simple", "🔁 Balade en boucle"],
    )
    categorie = st.radio(
        "🎯 Mode de Pratique",
        options=["🛣️ Routière / GT", "🏍️ Trail / Tout-terrain"],
        help="Le mode Trail affiche en vert les chemins et pistes non goudronnées."
    )

    ville_depart = saisir_ville_avec_suggestions("📍 Ville de départ (Point A)", "Montbeton", "depart")
    est_boucle = "boucle" in type_itineraire
    if est_boucle:
        duree_boucle_h = st.slider(
            "⏱️ Durée de roulage souhaitée",
            min_value=1.0,
            max_value=8.0,
            value=3.0,
            step=0.5,
            help="Le moteur spécialisé génère un circuit au plus près de cette durée.",
        )
        direction_boucle = st.radio(
            "🧭 Direction principale de la boucle",
            options=["⬆️ Nord", "➡️ Est", "⬇️ Sud", "⬅️ Ouest"],
            horizontal=True,
        )
        st.caption("Circuit automatique, départ et arrivée identiques.")
        etape_intermediaire = ""
        ville_arrivee = ville_depart
    else:
        duree_boucle_h = None
        direction_boucle = None
        etape_intermediaire = saisir_ville_avec_suggestions(
            "📌 Étape intermédiaire (Optionnel)", "Auch", "etape"
        )
        ville_arrivee = saisir_ville_avec_suggestions("🏁 Ville d'arrivée (Point B)", "Lourdes", "arrivee")

    heure_depart = st.time_input("🕒 Heure de départ", datetime.time(9, 0), step=900)
    autonomie_km = st.slider("⛽ Autonomie réservoir / Plein (km)", 150, 350, 200, step=10)

    btn_generer = st.button("🚀 Calculer l'Itinéraire", use_container_width=True)


# -----------------------------------------------------------------------------
# CALCUL DE L'ITINÉRAIRE
# -----------------------------------------------------------------------------
if btn_generer:
    with st.spinner("Calcul du tracé, calcul du % de virages et des pauses..."):
        coords_dep, info_dep = geocode_ville_details(ville_depart)
        if est_boucle:
            coords_etape, info_etape = None, {}
            coords_arr, info_arr = coords_dep, info_dep
        else:
            coords_etape, info_etape = (
                geocode_ville_details(etape_intermediaire)
                if etape_intermediaire.strip()
                else (None, {})
            )
            coords_arr, info_arr = geocode_ville_details(ville_arrivee)

        villes_non_trouvees = []
        if not coords_dep:
            villes_non_trouvees.append(f"Départ ({ville_depart})")
        if etape_intermediaire.strip() and not coords_etape:
            villes_non_trouvees.append(f"Étape ({etape_intermediaire})")
        if not coords_arr:
            villes_non_trouvees.append(f"Arrivée ({ville_arrivee})")

        if villes_non_trouvees:
            st.error(f"❌ Impossible de géolocaliser : {', '.join(villes_non_trouvees)}.")
        else:
            is_trail = "Trail" in categorie
            if est_boucle:
                caps_direction = {"⬆️ Nord": 0, "➡️ Est": 90, "⬇️ Sud": 180, "⬅️ Ouest": 270}
                coords_trace, dist_reelle, duree_min, erreur_routage = router_boucle_graphhopper(
                    coords_dep, int(duree_boucle_h * 60), caps_direction[direction_boucle]
                )
            else:
                waypoints = [coords_dep]
                if coords_etape:
                    waypoints.append(coords_etape)
                waypoints.append(coords_arr)
                coords_trace, dist_reelle, duree_min, erreur_routage = router_sans_autoroute(waypoints)

            if coords_trace:
                d_pos, d_neg, pct_virages, elevations = obtenir_denivele_et_virages(coords_trace)

                pistes_overlay = []
                if is_trail:
                    mid_pt = coords_trace[len(coords_trace) // 2]
                    pistes_overlay = chercher_pistes_trail_overpass(mid_pt[0], mid_pt[1], rayon_m=20000)

                etapes_pauses, _ = calculer_étapes_pauses_et_plein(
                    coords_trace, dist_reelle, duree_min, heure_depart, intervalle_plein_km=autonomie_km
                )
                if dist_reelle > autonomie_km:
                    stations_osm, erreur_stations = chercher_stations_essence_overpass(coords_trace)
                    if erreur_stations:
                        stations_recommandees = []
                        alertes_carburant = [erreur_stations]
                    else:
                        stations_sur_trajet = positionner_stations_sur_trajet(
                            stations_osm, coords_trace
                        )
                        stations_recommandees, alertes_carburant = recommander_stations(
                            stations_sur_trajet, dist_reelle, autonomie_km
                        )
                else:
                    stations_recommandees, alertes_carburant = [], []

                dt_dep = datetime.datetime.combine(datetime.date.today(), heure_depart)
                temps_total_min = duree_min + (len(etapes_pauses) * 20) + 45
                dt_arr = dt_dep + datetime.timedelta(minutes=temps_total_min)

                st.session_state["trajet_resultat"] = {
                    "coords": coords_trace,
                    "dist_km": round(dist_reelle, 1),
                    "duree_roulage": f"{int(duree_min // 60)}h{int(duree_min % 60):02d}",
                    "heure_arr": dt_arr.strftime("%Hh%M"),
                    "info_dep": info_dep,
                    "info_etape": info_etape,
                    "info_arr": info_arr,
                    "is_trail": is_trail,
                    "est_boucle": est_boucle,
                    "duree_boucle_cible": duree_boucle_h,
                    "direction_boucle": direction_boucle,
                    "pistes": pistes_overlay,
                    "coords_etape": coords_etape,
                    "d_pos": d_pos,
                    "d_neg": d_neg,
                    "pct_virages": pct_virages,
                    "etapes_pauses": etapes_pauses,
                    "stations_recommandees": stations_recommandees,
                    "alertes_carburant": alertes_carburant,
                    "elevations": elevations
                }
                st.session_state["gpx_exporte"] = False
            else:
                st.error(f"❌ {erreur_routage or 'Impossible de calculer le tracé. Réessayez.'}")


# -----------------------------------------------------------------------------
# AFFICHAGE
# -----------------------------------------------------------------------------
if "trajet_resultat" in st.session_state and st.session_state["trajet_resultat"]:
    res = st.session_state["trajet_resultat"]

    libelle_type = "Balade en boucle" if res.get("est_boucle") else "Trajet A → B"
    st.subheader(
        f"📋 Résumé — {libelle_type} ({'Mode Trail' if res['is_trail'] else 'Mode Routière'})"
    )
    if res.get("est_boucle"):
        st.caption(
            f"Objectif : environ {res['duree_boucle_cible']:.1f} h de roulage, "
            f"en partant vers {res['direction_boucle']}."
        )

    # Affichage sur 5 colonnes incluant désormais le % de virages
    with st.container(key="resume_metrics"):
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("📏 Distance Totale", f"{res['dist_km']} km")
        c2.metric("⏱️ Temps Roulage", res["duree_roulage"])
        c3.metric("🏁 Arrivée Estimée", res["heure_arr"], help="Inclut les pauses café + repas")
        c4.metric("🔄 Courbes & Virages", f"{res['pct_virages']}%")
        c5.metric("🏔️ Dénivelé (+ / -)", f"+{res['d_pos']}m / -{res['d_neg']}m")

    st.markdown("---")

    # TABLEAU DES PAUSES & STATIONS ESSENCE
    col_p1, col_p2 = st.columns(2)

    with col_p1:
        st.subheader("☕ / 🍽️ Pauses Programmées")
        if res["etapes_pauses"]:
            for p in res["etapes_pauses"]:
                st.info(f"**{p['type']}** à **{p['heure_estimee']}** (au km {p['km']})\n📍 Lieu : **{p['lieu']}**")
        else:
            st.write("Aucune pause intermédiaire nécessaire sur cette courte distance.")

    with col_p2:
        st.subheader("⛽ Stations-service réelles recommandées")
        for alerte in res.get("alertes_carburant", []):
            st.error(f"⚠️ {alerte}")

        if res["stations_recommandees"]:
            for station in res["stations_recommandees"]:
                marque = f" — {station['marque']}" if station["marque"] else ""
                detour = (
                    f" (à {station['ecart_route_km']} km du tracé)"
                    if station["ecart_route_km"] else ""
                )
                st.warning(
                    f"**{station['nom']}{marque}** — km **{station['km']}**{detour}\n"
                    f"📍 {station['adresse'] or 'Adresse non renseignée'}\n"
                    f"🕒 {station['horaires']}"
                )
        elif not res.get("alertes_carburant"):
            st.success("✅ Trajet réalisable sur un seul plein !")

    st.markdown("---")
    st.subheader("🗺️ Carte du Parcours et Points d'Arrêt")

    m = folium.Map(location=res["coords"][0], zoom_start=9, tiles="OpenStreetMap")

    folium.PolyLine(locations=res["coords"], color="#0066cc", weight=5, opacity=0.8, popup="Liaison").add_to(m)

    if res["is_trail"] and res["pistes"]:
        for piste in res["pistes"]:
            folium.PolyLine(locations=piste, color="#2e7d32", weight=4, opacity=0.9, popup="Piste / Chemin").add_to(m)

    for p in res["etapes_pauses"]:
        folium.Marker(
            [p["lat"], p["lon"]],
            popup=f"{p['type']} - {p['lieu']} (km {p['km']})",
            icon=folium.Icon(color="blue" if "Café" in p["type"] else "red", icon="coffee" if "Café" in p["type"] else "utensils", prefix="fa")
        ).add_to(m)

    for st_rec in res["stations_recommandees"]:
        folium.Marker(
            [st_rec["lat"], st_rec["lon"]],
            popup=(
                f"{st_rec['nom']} — km {st_rec['km']}<br>"
                f"{st_rec['adresse'] or 'Adresse non renseignée'}<br>"
                f"{st_rec['horaires']}"
            ),
            icon=folium.Icon(color="orange", icon="gas-pump", prefix="fa")
        ).add_to(m)

    folium.Marker(res["coords"][0], popup=f"Départ : {res['info_dep'].get('label', '')}", icon=folium.Icon(color="green", icon="play")).add_to(m)
    if res["coords_etape"]:
        folium.Marker(res["coords_etape"], popup=f"Étape : {res['info_etape'].get('label', '')}", icon=folium.Icon(color="orange", icon="info-sign")).add_to(m)
    folium.Marker(res["coords"][-1], popup=f"Arrivée : {res['info_arr'].get('label', '')}", icon=folium.Icon(color="red", icon="stop")).add_to(m)

    st_folium(m, width="100%", height=440, returned_objects=[])

    if res["elevations"]:
        st.subheader("📈 Profil d'Élévation")
        df_elev = pd.DataFrame({
            "Point": list(range(len(res["elevations"]))),
            "Altitude (m)": res["elevations"]
        })
        chart = alt.Chart(df_elev).mark_area(
            line={'color':'#0066cc'},
            color=alt.Gradient(
                gradient='linear',
                stops=[alt.GradientStop(color='white', offset=0), alt.GradientStop(color='#0066cc', offset=1)],
                x1=1, x2=1, y1=1, y2=0
            )
        ).encode(
            x=alt.X('Point', title='Progression sur le trajet'),
            y=alt.Y('Altitude (m)', title='Altitude (m)')
        ).properties(height=200)
        st.altair_chart(chart, use_container_width=True)

else:
    st.info("👈 Saisissez vos villes dans la barre latérale et cliquez sur **🚀 Calculer l'Itinéraire**.")


if st.session_state.get("trajet_resultat"):
    with st.sidebar:
        resultat_gps = st.session_state["trajet_resultat"]
        # Sur une boucle, une appli de navigation classique ne peut pas recevoir
        # tout le circuit : on lui propose le premier quart du parcours.
        index_guidage = (
            max(1, len(resultat_gps["coords"]) // 4)
            if resultat_gps.get("est_boucle")
            else -1
        )
        lat_guidage, lon_guidage = resultat_gps["coords"][index_guidage]
        st.link_button(
            "🧭 Ouvrir dans Plans (iPhone)",
            f"https://maps.apple.com/?daddr={lat_guidage:.6f},{lon_guidage:.6f}&dirflg=d",
            help="Ouvre Plans d'Apple avec un itinéraire en voiture depuis votre position actuelle.",
            use_container_width=True,
        )
        st.link_button(
            "🧭 Ouvrir dans Google Maps (Android)",
            f"https://www.google.com/maps/dir/?api=1&destination={lat_guidage:.6f},{lon_guidage:.6f}&travelmode=driving",
            help="Ouvre Google Maps si l'application est installée, sinon le navigateur.",
            use_container_width=True,
        )
        st.caption("Pour suivre tout le tracé, utilisez toujours l'export GPX.")


# Le bloc est créé après le calcul afin que le bouton apparaisse immédiatement
# lors de l'affichage du résultat, sous le bouton de calcul de la barre latérale.
if st.session_state.get("trajet_resultat") and not st.session_state.get("gpx_exporte", False):
    with st.sidebar:
        export_effectue = st.download_button(
            "⬇️ Exporter l'itinéraire en GPX",
            data=creer_gpx(st.session_state["trajet_resultat"]["coords"]),
            file_name="roadric-itineraire.gpx",
            mime="application/gpx+xml",
            use_container_width=True,
        )
        if export_effectue:
            st.session_state["gpx_exporte"] = True
            st.rerun()
