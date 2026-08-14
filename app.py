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
