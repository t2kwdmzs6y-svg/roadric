import datetime
import math
import altair as alt
import folium
import pandas as pd
import requests
import streamlit as st
from streamlit_folium import st_folium

st.set_page_config(
    page_title="ROADRIC - Road-Trip Moto",
    page_icon="🏍️",
    layout="wide",
)

st.title("🏍️ ROADRIC — Générateur de Road-Trip Moto")

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
def geocode_ville_details(nom_ville):
    """Géocodage ciblé sur les communes françaises."""
    if not nom_ville or not nom_ville.strip():
        return None, {}

    url_ban = "https://api-adresse.data.gouv.fr/search/"
    params_ban = {"q": nom_ville.strip(), "type": "municipality", "limit": 1}

    try:
        resp = requests.get(url_ban, params=params_ban, timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            features = data.get("features", [])
            if features:
                coords = features[0]["geometry"]["coordinates"]
                props = features[0]["properties"]

                nom_trouve = props.get("city", props.get("label", ""))
                code_dept = props.get("context", "")
                postcode = props.get("postcode", "")

                label_affiche = f"{nom_trouve} ({postcode[:2]})" if postcode else nom_trouve

                details = {
                    "nom": nom_trouve,
                    "postcode": postcode,
                    "departement": code_dept,
                    "label": label_affiche,
                    "lat": coords[1],
                    "lon": coords[0]
                }

                return (coords[1], coords[0]), details
    except Exception:
        pass

    return None, {}


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
def router_osrm(waypoints):
    """Routing OSRM avec récupération du trajet et profil temporel."""
    coords_str = ";".join([f"{lon},{lat}" for lat, lon in waypoints])
    url = f"http://router.project-osrm.org/route/v1/driving/{coords_str}?overview=full&geometries=geojson&steps=true"

    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if "routes" in data and len(data["routes"]) > 0:
                route = data["routes"][0]
                geometry = route["geometry"]["coordinates"]
                coords = [(pt[1], pt[0]) for pt in geometry]
                dist_km = route["distance"] / 1000.0
                duree_min = route["duration"] / 60.0
                return coords, dist_km, duree_min
    except Exception:
        pass
    return None, 0, 0


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

        # Ravitaillement Carburant tous les X km (ex: 200km)
        if (cumul_dist - dernier_plein_km) >= intervalle_plein_km:
            pt = coords[i]
            nom_lieu = nommer_coordonnee(pt[0], pt[1])
            stations_recommandees.append({
                "km": round(cumul_dist, 1),
                "lat": pt[0],
                "lon": pt[1],
                "nom": f"⛽ Station essence conseillée - Proche de {nom_lieu}"
            })
            dernier_plein_km = cumul_dist

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


# -----------------------------------------------------------------------------
# BARRE LATÉRALE
# -----------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Configuration Itinéraire")

    categorie = st.radio(
        "🎯 Mode de Pratique",
        options=["🛣️ Routière / GT", "🏍️ Trail / Tout-terrain"],
        help="Le mode Trail affiche en vert les chemins et pistes non goudronnées."
    )

    ville_depart = st.text_input("📍 Ville de départ (Point A)", "Montbeton")
    etape_intermediaire = st.text_input("📌 Étape intermédiaire (Optionnel)", "Auch")
    ville_arrivee = st.text_input("🏁 Ville d'arrivée (Point B)", "Lourdes")

    heure_depart = st.time_input("🕒 Heure de départ", datetime.time(9, 0), step=900)
    autonomie_km = st.slider("⛽ Autonomie réservoir / Plein (km)", 150, 350, 200, step=10)

    btn_generer = st.button("🚀 Calculer l'Itinéraire", use_container_width=True)


# -----------------------------------------------------------------------------
# CALCUL DE L'ITINÉRAIRE
# -----------------------------------------------------------------------------
if btn_generer:
    with st.spinner("Calcul du tracé, calcul du % de virages et des pauses..."):
        coords_dep, info_dep = geocode_ville_details(ville_depart)
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
            waypoints = [coords_dep]
            if coords_etape:
                waypoints.append(coords_etape)
            waypoints.append(coords_arr)

            is_trail = "Trail" in categorie
            coords_trace, dist_reelle, duree_min = router_osrm(waypoints)

            if coords_trace:
                d_pos, d_neg, pct_virages, elevations = obtenir_denivele_et_virages(coords_trace)

                pistes_overlay = []
                if is_trail:
                    mid_pt = coords_trace[len(coords_trace) // 2]
                    pistes_overlay = chercher_pistes_trail_overpass(mid_pt[0], mid_pt[1], rayon_m=20000)

                etapes_pauses, stations_recommandees = calculer_étapes_pauses_et_plein(
                    coords_trace, dist_reelle, duree_min, heure_depart, intervalle_plein_km=autonomie_km
                )

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
                    "pistes": pistes_overlay,
                    "coords_etape": coords_etape,
                    "d_pos": d_pos,
                    "d_neg": d_neg,
                    "pct_virages": pct_virages,
                    "etapes_pauses": etapes_pauses,
                    "stations_recommandees": stations_recommandees,
                    "elevations": elevations
                }
            else:
                st.error("❌ Impossible de calculer le tracé. Réessayez.")


# -----------------------------------------------------------------------------
# AFFICHAGE
# -----------------------------------------------------------------------------
if "trajet_resultat" in st.session_state and st.session_state["trajet_resultat"]:
    res = st.session_state["trajet_resultat"]

    st.subheader(f"📋 Résumé du Trajet ({'Mode Trail' if res['is_trail'] else 'Mode Routière'})")

    # Affichage sur 5 colonnes incluant désormais le % de virages
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
        st.subheader(f"⛽ Ravitaillement Carburant (Tous les {autonomie_km} km)")
        if res["stations_recommandees"]:
            for st_rec in res["stations_recommandees"]:
                st.warning(f"**{st_rec['nom']}**\n📍 Kilomètre du trajet : **{st_rec['km']} km**")
        else:
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
            popup=f"{st_rec['nom']} (km {st_rec['km']})",
            icon=folium.Icon(color="orange", icon="gas-pump", prefix="fa")
        ).add_to(m)

    folium.Marker(res["coords"][0], popup=f"Départ : {res['info_dep'].get('label', '')}", icon=folium.Icon(color="green", icon="play")).add_to(m)
    if res["coords_etape"]:
        folium.Marker(res["coords_etape"], popup=f"Étape : {res['info_etape'].get('label', '')}", icon=folium.Icon(color="orange", icon="info-sign")).add_to(m)
    folium.Marker(res["coords"][-1], popup=f"Arrivée : {res['info_arr'].get('label', '')}", icon=folium.Icon(color="red", icon="stop")).add_to(m)

    st_folium(m, width="100%", height=550, returned_objects=[])

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
