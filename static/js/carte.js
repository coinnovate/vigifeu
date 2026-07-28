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
          "circle-radius": 7, "circle-color": COULEUR.front_actif,
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
