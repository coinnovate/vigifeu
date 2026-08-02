/* Sentifeu — carte MapLibre (enrichissement progressif, Spec 04 P3/§8).
   Le contenu (synthèse, liste des feux) est complet sans ce script ; la carte n'ajoute
   que l'interaction. Le GeoJSON est pré-généré côté serveur ; la clé MapTiler vient de
   /static/carte-config.js (jamais du HTML des fiches). Sans clé ou sans MapLibre, on
   masque proprement le conteneur et on laisse l'alternative textuelle. */
(function () {
  "use strict";
  var KEY = window.SENTIFEU_MAPTILER_KEY || "";
  var MAP = window.SENTIFEU_MAPTILER_MAP || "dataviz";

  // Couleurs du cycle de vie — doublées par l'ordre des couches, pas seulement la teinte.
  var COULEUR = { front_actif: "#c0392b", recent: "#e67e22", plus_detecte: "#8a8a8a" };
  // POI/enjeux (Spec 06 §4) : teintes MUETTES par catégorie, jamais le rouge/orange du feu.
  var POI_COULEUR = {
    camping: "#2e7d5b", ecole: "#3f6fb0", hopital: "#b0555b",
    ehpad: "#7a5ba8", station_service: "#a8823f", icpe_seveso: "#555555"
  };

  function fond() {
    return {
      version: 8,
      sources: {
        fond: {
          type: "raster",
          tiles: ["https://api.maptiler.com/maps/" + MAP + "/{z}/{x}/{y}.png?key=" + KEY],
          tileSize: 256,
          attribution: "© MapTiler © OpenStreetMap contributors"
        }
      },
      layers: [{ id: "fond", type: "raster", source: "fond" }]
    };
  }

  function bounds(geojson) {
    var b = new maplibregl.LngLatBounds();
    (geojson.features || []).forEach(function (f) {
      var g = f.geometry;
      if (!g) return;
      var coords = g.type === "Point" ? [g.coordinates]
        : g.type === "Polygon" ? g.coordinates[0]
        : g.type === "MultiPolygon" ? g.coordinates.flat(2) : [];
      coords.forEach(function (c) { if (c && c.length >= 2) b.extend(c); });
    });
    return b.isEmpty() ? null : b;
  }

  function fitTo(map, geojson) {
    var b = bounds(geojson);
    if (b) map.fitBounds(b, { padding: 30, maxZoom: 12, duration: 0 });
  }

  function initFeu(el, geojson) {
    var map = new maplibregl.Map({ container: el, style: fond(), center: [2.5, 46.6], zoom: 5 });
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    map.on("load", function () {
      map.addSource("feu", { type: "geojson", data: geojson });
      map.addLayer({
        id: "cellules", type: "fill", source: "feu",
        filter: ["==", ["get", "couche"], "cellule"],
        paint: {
          "fill-color": ["match", ["get", "state"],
            "front_actif", COULEUR.front_actif, "recent", COULEUR.recent,
            "plus_detecte", COULEUR.plus_detecte, "#999"],
          "fill-opacity": 0.55
        }
      });
      map.addLayer({
        id: "enveloppe", type: "line", source: "feu",
        filter: ["==", ["get", "couche"], "enveloppe"],
        paint: { "line-color": "#333", "line-dasharray": [2, 2], "line-width": 1.5 }
      });
      // Enjeux (POI) : marqueurs par catégorie, bord foncé si DANS la zone détectée.
      // Non cliquables (infobulle seule) — Spec 06 §4.
      map.addLayer({
        id: "poi", type: "circle", source: "feu",
        filter: ["==", ["get", "couche"], "poi"],
        paint: {
          "circle-radius": 5,
          "circle-color": ["match", ["get", "category"],
            "camping", POI_COULEUR.camping, "ecole", POI_COULEUR.ecole,
            "hopital", POI_COULEUR.hopital, "ehpad", POI_COULEUR.ehpad,
            "station_service", POI_COULEUR.station_service,
            "icpe_seveso", POI_COULEUR.icpe_seveso, "#666666"],
          "circle-stroke-color": ["case", ["==", ["get", "tier"], "emprise"], "#1a1a1a", "#ffffff"],
          "circle-stroke-width": ["case", ["==", ["get", "tier"], "emprise"], 2, 1],
          "circle-opacity": 0.85
        }
      });
      var popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false });
      map.on("mouseenter", "poi", function (e) {
        map.getCanvas().style.cursor = "help";
        var p = e.features[0].properties;
        var suffixe = p.tier === "emprise" ? " (dans la zone détectée)" : " (à proximité)";
        popup.setLngLat(e.lngLat).setText(p.libelle + suffixe).addTo(map);
      });
      map.on("mouseleave", "poi", function () {
        map.getCanvas().style.cursor = ""; popup.remove();
      });
      // Toggle de la légende (visible par défaut, masquable) — Spec 06 §4.
      var toggle = document.getElementById("toggle-poi");
      if (toggle) toggle.addEventListener("change", function () {
        map.setLayoutProperty("poi", "visibility", toggle.checked ? "visible" : "none");
      });
      fitTo(map, geojson);
    });
  }

  function initNational(el, geojson) {
    var map = new maplibregl.Map({ container: el, style: fond(), center: [2.5, 46.6], zoom: 4.5 });
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    map.on("load", function () {
      map.addSource("feux", { type: "geojson", data: geojson });
      map.addLayer({
        id: "feux", type: "circle", source: "feux",
        paint: {
          // Rayon proportionnel à la surface ESTIMÉE : on interpole sur √surface pour que
          // l'AIRE du cercle (perçue) soit proportionnelle à la surface du feu (symbole
          // proportionnel honnête). area_ha NULL → plancher (petit cercle), jamais 0/invisible.
          "circle-radius": [
            "interpolate", ["linear"],
            ["sqrt", ["max", 1, ["coalesce", ["get", "area_ha"], 1]]],
            1, 4,    // ~1 ha
            4, 6,    // ~16 ha
            10, 9,   // ~100 ha
            22, 13,  // ~500 ha
            32, 16,  // ~1000 ha
            71, 22   // ~5000 ha (mégafeu)
          ],
          "circle-color": COULEUR.front_actif,
          "circle-stroke-color": "#fff", "circle-stroke-width": 1.5, "circle-opacity": 0.9
        }
      });
      map.on("click", "feux", function (e) {
        var url = e.features[0].properties.url;
        if (url) window.location.href = url;
      });
      map.on("mouseenter", "feux", function () { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", "feux", function () { map.getCanvas().style.cursor = ""; });
    });
  }

  function boot() {
    var cartes = document.querySelectorAll("[data-carte]");
    if (!cartes.length) return;
    if (!KEY || typeof maplibregl === "undefined") {
      // Sans fond disponible : on masque le conteneur, l'alternative textuelle reste.
      cartes.forEach(function (el) { el.hidden = true; });
      return;
    }
    cartes.forEach(function (el) {
      var kind = el.getAttribute("data-carte");
      fetch(el.getAttribute("data-geojson"))
        .then(function (r) { return r.json(); })
        .then(function (gj) { (kind === "national" ? initNational : initFeu)(el, gj); })
        .catch(function () { el.hidden = true; });
    });
  }

  if (document.readyState !== "loading") boot();
  else document.addEventListener("DOMContentLoaded", boot);
})();
