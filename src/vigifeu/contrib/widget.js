/* Widget « Photos de visiteurs » — Sentifeu / Spec 10 §7.
 *
 * Peuplé CÔTÉ CLIENT (le générateur statique n'est pas modifié). Même widget pour la fiche
 * feu et la page commune : seule l'URL d'endpoint change. Dégradation gracieuse : toute
 * erreur laisse le conteneur vide (le site statique reste intact), l'onglet reste masqué s'il
 * n'y a aucune photo publiée.
 *
 * Usage :
 *   <div data-sentifeu-photos="/api/contrib/feu/aZ3.../photos"
 *        data-lieu="Le Porge"></div>
 *   <script src="/api/contrib/widget.js" defer></script>
 *
 * Contenu tiers NON fiable : jamais de HTML utilisateur, uniquement des médias + du texte
 * échappé par le DOM (textContent / attributs).
 */
(function () {
  "use strict";

  var BADGE = "Photo de visiteur — non vérifiée par Sentifeu";

  // Styles auto-portants (le gabarit statique n'est pas modifié) : grille + lightbox.
  function injecterStyles() {
    if (document.getElementById("sentifeu-photos-styles")) return;
    var s = document.createElement("style");
    s.id = "sentifeu-photos-styles";
    s.textContent =
      ".sentifeu-grille{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));" +
      "gap:.5rem;margin:.5rem 0}" +
      ".sentifeu-vignette{padding:0;border:0;background:none;cursor:zoom-in;line-height:0}" +
      ".sentifeu-vignette img{width:100%;height:100%;object-fit:cover;border-radius:6px}" +
      ".sentifeu-lightbox{position:fixed;inset:0;background:rgba(0,0,0,.9);z-index:9998;display:flex;" +
      "flex-direction:column;align-items:center;justify-content:center;padding:1rem}" +
      ".sentifeu-lightbox img{max-width:96vw;max-height:80vh;border-radius:6px}" +
      ".sentifeu-lightbox-legende{color:#fff;margin:.6rem 0 0;font:14px system-ui}" +
      ".sentifeu-badge{color:#ddd;font:12px system-ui;margin-top:.2rem}";
    document.head.appendChild(s);
  }

  function fmtDate(iso) {
    try {
      return new Date(iso).toLocaleString("fr-FR", {
        day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit",
      });
    } catch (e) {
      return iso;
    }
  }

  function altText(lieu, iso) {
    var ou = lieu ? "du feu de " + lieu : "d'un feu";
    return "Photo " + ou + " prise le " + fmtDate(iso);
  }

  // --- lightbox -----------------------------------------------------------
  function ouvrirLightbox(photos, index, lieu) {
    var i = index;
    var overlay = document.createElement("div");
    overlay.className = "sentifeu-lightbox";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.tabIndex = -1;

    var img = document.createElement("img");
    var legende = document.createElement("p");
    legende.className = "sentifeu-lightbox-legende";
    var badge = document.createElement("span");
    badge.className = "sentifeu-badge";
    badge.textContent = BADGE;

    function rendre() {
      var p = photos[i];
      img.src = p.url;
      img.alt = altText(lieu, p.captured_at);
      legende.textContent = "Prise le " + fmtDate(p.captured_at);
    }
    function fermer() {
      document.removeEventListener("keydown", onKey);
      overlay.remove();
    }
    function onKey(e) {
      if (e.key === "Escape") fermer();
      else if (e.key === "ArrowRight") { i = (i + 1) % photos.length; rendre(); }
      else if (e.key === "ArrowLeft") { i = (i - 1 + photos.length) % photos.length; rendre(); }
    }

    overlay.appendChild(img);
    overlay.appendChild(legende);
    overlay.appendChild(badge);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) fermer(); });
    document.addEventListener("keydown", onKey);
    document.body.appendChild(overlay);
    rendre();
    overlay.focus();
  }

  // --- grille -------------------------------------------------------------
  function rendreGrille(conteneur, photos, lieu) {
    var grille = document.createElement("div");
    grille.className = "sentifeu-grille";
    photos.forEach(function (p, idx) {
      var bouton = document.createElement("button");
      bouton.type = "button";
      bouton.className = "sentifeu-vignette";
      var im = document.createElement("img");
      im.src = p.thumb_url;
      im.loading = "lazy";
      im.alt = altText(lieu, p.captured_at);
      if (p.thumb_largeur) im.width = p.thumb_largeur;
      if (p.thumb_hauteur) im.height = p.thumb_hauteur;
      bouton.appendChild(im);
      bouton.addEventListener("click", function () { ouvrirLightbox(photos, idx, lieu); });
      grille.appendChild(bouton);
    });
    conteneur.innerHTML = "";
    conteneur.appendChild(grille);
  }

  function monter(conteneur) {
    var endpoint = conteneur.getAttribute("data-sentifeu-photos");
    if (!endpoint) return;
    injecterStyles();
    var lieu = conteneur.getAttribute("data-lieu") || "";
    // Le conteneur part `hidden` côté gabarit : on ne le révèle QUE s'il y a des photos
    // (onglet masqué si vide, §7.3). Erreur/API down → reste masqué (dégradation gracieuse).
    fetch(endpoint, { headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data || !data.photos || data.photos.length === 0) return;
        conteneur.removeAttribute("hidden");
        // Révèle aussi la section parente masquée (titre « Photos » + grille) — seulement
        // maintenant qu'on sait qu'il y a des photos (onglet masqué si vide, §7.3).
        var section = conteneur.closest("[hidden]");
        if (section) section.removeAttribute("hidden");
        rendreGrille(conteneur, data.photos, lieu);
      })
      .catch(function () {});
  }

  function init() {
    var noeuds = document.querySelectorAll("[data-sentifeu-photos]");
    Array.prototype.forEach.call(noeuds, monter);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.SentifeuPhotos = { monter: monter };
})();
