/* Sentifeu — carte MapLibre (enrichissement progressif, Spec 04 P3/§8).
   Le contenu (synthèse, liste des feux) est complet sans ce script ; la carte n'ajoute
   que l'interaction. Le GeoJSON est pré-généré côté serveur ; la clé MapTiler vient de
   /static/carte-config.js (jamais du HTML des fiches). Sans clé ou sans MapLibre, on
   masque proprement le conteneur et on laisse l'alternative textuelle. */
(function () {
  "use strict";
  var KEY = window.SENTIFEU_MAPTILER_KEY || "";
  var MAP = window.SENTIFEU_MAPTILER_MAP || "dataviz";
  var SH = window.SENTIFEU_SH || {};   // imagerie Sentinel-2 via CDSE Sentinel Hub (Spec 06 §5)

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

  // Imagerie Sentinel-2 (Spec 06 §5) — voir le commentaire au point d'appel dans initFeu.
  function imageryToggle(el, map, geojson) {
    var tImg = document.getElementById("toggle-imagerie");
    var note = document.getElementById("imagerie-note");
    var from = el.getAttribute("data-img-from");
    var to = el.getAttribute("data-img-to");
    var maxcc = parseFloat(el.getAttribute("data-img-maxcc") || "20");
    var legendTpl = el.getAttribute("data-img-legend") || "";
    var degrade = el.getAttribute("data-img-degrade") || "";
    if (!(tImg && from && SH.instance && SH.wms && SH.wfs)) {
      if (tImg && tImg.parentNode) tImg.parentNode.hidden = true;  // pas d'imagerie → pas de bouton mort
      return;
    }
    var frDate = function (iso) { var p = iso.split("-"); return p[2] + "/" + p[1] + "/" + p[0]; };
    var showNote = function (t) { if (note) { note.textContent = t; note.hidden = false; } };
    // Sous l'imagerie, les cellules (emprise, opaques) masqueraient la cicatrice réelle :
    // quand une image claire s'affiche, on ne garde que le contour (enveloppe) ; sinon elles restent.
    var showCells = function (v) {
      if (map.getLayer("cellules")) map.setLayoutProperty("cellules", "visibility", v ? "visible" : "none");
    };
    var state = "idle";      // idle → loading → ready | none
    var legendText = "";

    function addImagery(date) {
      if (map.getSource("imagerie")) return;
      map.addSource("imagerie", {
        type: "raster", tileSize: 512,
        tiles: [SH.wms + "/" + SH.instance + "?SERVICE=WMS&REQUEST=GetMap&VERSION=1.1.1" +
                "&LAYERS=" + encodeURIComponent(SH.layer) + "&FORMAT=image/png&SRS=EPSG:3857" +
                "&WIDTH=512&HEIGHT=512&TIME=" + date + "&BBOX={bbox-epsg-3857}"],
        attribution: SH.source || "Copernicus Sentinel-2"
      });
      map.addLayer({ id: "imagerie", type: "raster", source: "imagerie" }, "cellules"); // sous les cellules
    }

    function resolve() {
      var b = bounds(geojson);
      // WFS EPSG:4326 attend miny,minx,maxy,maxx (lat,lon).
      var bbox = b ? [b.getSouth(), b.getWest(), b.getNorth(), b.getEast()].join(",") : null;
      var url = SH.wfs + "/" + SH.instance + "?SERVICE=WFS&REQUEST=GetFeature&TYPENAMES=" +
                encodeURIComponent(SH.typename || "DSS2") + "&OUTPUTFORMAT=application/json" +
                "&SRSNAME=EPSG:4326&BBOX=" + bbox + "&TIME=" + from + "/" + to;
      state = "loading";
      showNote("Recherche d'une vue satellite claire…");
      fetch(url).then(function (r) { return r.json(); }).then(function (j) {
        var best = null;   // date la plus récente < seuil de nuages
        (j.features || []).forEach(function (f) {
          var p = f.properties || {}, d = p.date, cc = p.cloudCoverPercentage;
          if (d && d >= from && cc != null && cc <= maxcc && (best === null || d > best)) best = d;
        });
        if (best) {
          state = "ready";
          legendText = legendTpl.replace("{date}", frDate(best));
          if (tImg.checked) { addImagery(best); showNote(legendText); showCells(false); }
        } else {
          state = "none";
          if (tImg.checked) showNote(degrade);   // pas de vue claire post-feu → dégradé honnête
        }
      }).catch(function () { state = "none"; if (tImg.checked) showNote(degrade); });
    }

    tImg.addEventListener("change", function () {
      if (!tImg.checked) {
        if (map.getLayer("imagerie")) map.setLayoutProperty("imagerie", "visibility", "none");
        if (note) note.hidden = true;
        showCells(true);   // imagerie coupée → l'emprise redevient visible
        return;
      }
      if (state === "ready") {
        if (map.getLayer("imagerie")) map.setLayoutProperty("imagerie", "visibility", "visible");
        showNote(legendText);
        showCells(false);
      } else if (state === "none") {
        showNote(degrade);   // pas d'image affichée → on garde l'emprise
        showCells(true);
      } else if (state === "idle") {
        resolve();
      }  // "loading" : on attend la résolution en cours
    });
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
      // Imagerie Sentinel-2 (Spec 06 §5, cran 2) — POLITIQUE : n'afficher QUE s'il existe un passage
      // S2 CLAIR depuis le début du feu, avec sa VRAIE date. Au 1er clic sur le toggle, on interroge
      // le WFS (auth par ID d'instance, sans OAuth) pour les passages [from, to] + % nuages, on retient
      // le plus récent < seuil, on épingle le WMS à CETTE date et on affiche la date réelle. Aucun
      // passage clair → dégradé honnête (jamais une image antérieure au feu, trompeuse). Calque SOUS
      // les cellules (le feu reste au-dessus). Sans instance/WFS/fenêtre : toggle masqué (pas de bouton mort).
      imageryToggle(el, map, geojson);
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
          // Rouge = feu actif ; gris = plus détecté (silence, en attente d'archivage).
          // La teinte est doublée par le badge de la liste (accessibilité, jamais la couleur seule).
          "circle-color": ["match", ["get", "lifecycle"],
            "plus_detecte", COULEUR.plus_detecte, COULEUR.front_actif],
          "circle-stroke-color": "#fff", "circle-stroke-width": 1.5, "circle-opacity": 0.9
        }
      });
      // Infobulle au survol : le nom du feu, pour ne pas cliquer en aveugle (même motif
      // que les POI des fiches). setText → pas d'injection ; on ancre au point, pas au curseur.
      var popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false });
      map.on("click", "feux", function (e) {
        var url = e.features[0].properties.url;
        if (url) window.location.href = url;
      });
      map.on("mouseenter", "feux", function (e) {
        map.getCanvas().style.cursor = "pointer";
        var f = e.features[0];
        popup.setLngLat(f.geometry.coordinates.slice()).setText(f.properties.nom).addTo(map);
      });
      map.on("mouseleave", "feux", function () {
        map.getCanvas().style.cursor = ""; popup.remove();
      });
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
