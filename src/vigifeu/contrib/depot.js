/* Parcours de dépôt d'une contribution photo — Sentifeu / Spec 10 §4 (étape 9).
 *
 * Modal auto-portante (styles injectés une fois) déclenchée par un bouton
 * « Déposer une photo». Enchaîne : géolocalisation → feu à proximité (présélectionné sur une
 * fiche feu, vérifié par la géoloc) → CAPTURE EN DIRECT (getUserMedia, caméra arrière, canvas ;
 * AUCUNE galerie/fichier) → e-mail optionnel + consentement → POST /api/contrib/deposer.
 *
 * Point dur (§4) : dans les navigateurs INTÉGRÉS des apps sociales (Facebook/Instagram/
 * LinkedIn…), getUserMedia est souvent bloqué. On DÉTECTE l'indisponibilité et on affiche un
 * repli clair (« ouvrez dans Safari/Chrome »), jamais un écran caméra noir.
 *
 * La position exacte de l'auteur n'est jamais envoyée pour être stockée : le serveur ne
 * conserve que la distance au hotspot (§0). Ici lat/lon servent à trouver/vérifier le feu.
 *
 * Usage :
 *   <button data-sentifeu-depot data-fire-public-id="2026-saumos" data-lieu="Le Porge">
 *     Déposer une photo</button>
 *   <script src="/api/contrib/depot.js" defer></script>
 */
(function () {
  "use strict";

  var API = "/api/contrib";
  // Licence d'AFFICHAGE non-exclusive (l'auteur garde ses droits) + garantie d'auteur +
  // absence de personne identifiable (aide au droit à l'image en amont de la modération, §11).
  var CONSENT_TXT =
    "Je certifie être l'auteur de cette photo, prise à l'instant, et qu'elle ne montre pas de " +
    "personne identifiable. J'accorde à Sentifeu une licence non exclusive d'affichage sur le " +
    "site et j'accepte les conditions de contribution.";
  var SECU_TXT =
    "⚠️ Ne vous approchez jamais des flammes ni des zones d'intervention. " +
    "Sentifeu n'est pas un service de secours — en cas d'urgence, appelez le 18 ou le 112.";

  // --- styles (injectés une seule fois) ----------------------------------
  function injecterStyles() {
    if (document.getElementById("sentifeu-depot-styles")) return;
    var s = document.createElement("style");
    s.id = "sentifeu-depot-styles";
    s.textContent =
      ".sentifeu-modal{position:fixed;inset:0;background:rgba(0,0,0,.85);display:flex;" +
      "align-items:center;justify-content:center;z-index:9999;font-family:system-ui}" +
      ".sentifeu-modal-boite{background:#fff;color:#111;max-width:560px;width:92%;max-height:92vh;" +
      "overflow:auto;border-radius:12px;padding:1.25rem}" +
      ".sentifeu-modal video,.sentifeu-modal img.apercu{width:100%;border-radius:8px;background:#000}" +
      ".sentifeu-modal button{font:inherit;padding:.6rem 1rem;border-radius:8px;border:1px solid #ccc;" +
      "cursor:pointer;margin:.25rem .25rem 0 0}" +
      ".sentifeu-modal .primaire{background:#c1440e;color:#fff;border-color:#c1440e}" +
      ".sentifeu-fermer{float:right;border:none;background:none;font-size:1.4rem;line-height:1}" +
      ".sentifeu-erreur{color:#b00020}.sentifeu-feux label{display:block;margin:.2rem 0}" +
      ".sentifeu-secu{margin-top:1rem;padding-top:.75rem;border-top:1px solid #eee;" +
      "color:#b00020;font-size:.8rem}" +
      ".sentifeu-flottant{position:fixed;right:16px;bottom:16px;z-index:9990;height:48px;" +
      "padding:0 1rem;border:0;border-radius:24px;background:#c1440e;color:#fff;font:600 15px system-ui;" +
      "box-shadow:0 2px 10px rgba(0,0,0,.35);cursor:pointer}";
    document.head.appendChild(s);
  }

  // --- petites fabriques DOM ---------------------------------------------
  function el(tag, attrs, txt) {
    var n = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    if (txt != null) n.textContent = txt;
    return n;
  }

  function Modal() {
    injecterStyles();
    var overlay = el("div", { class: "sentifeu-modal", role: "dialog", "aria-modal": "true" });
    var boite = el("div", { class: "sentifeu-modal-boite" });
    var fermer = el("button", { class: "sentifeu-fermer", "aria-label": "Fermer" }, "×");
    var corps = el("div");
    var secu = el("p", { class: "sentifeu-secu" }, SECU_TXT); // rappel sécurité, toujours visible
    boite.appendChild(fermer);
    boite.appendChild(corps);
    boite.appendChild(secu);
    overlay.appendChild(boite);

    var flux = null; // MediaStream à couper à la fermeture
    function stopFlux() {
      if (flux) { flux.getTracks().forEach(function (t) { t.stop(); }); flux = null; }
    }
    function close() { stopFlux(); document.removeEventListener("keydown", onKey); overlay.remove(); }
    function onKey(e) { if (e.key === "Escape") close(); }
    fermer.addEventListener("click", close);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) close(); });
    document.addEventListener("keydown", onKey);
    document.body.appendChild(overlay);

    return {
      corps: corps,
      close: close,
      setFlux: function (f) { flux = f; },
      stopFlux: stopFlux,
      vider: function () { corps.innerHTML = ""; },
    };
  }

  function message(modal, titre, texte, erreur) {
    modal.vider();
    modal.corps.appendChild(el("h2", null, titre));
    modal.corps.appendChild(el("p", erreur ? { class: "sentifeu-erreur" } : null, texte));
  }

  // --- étapes -------------------------------------------------------------
  function captureDisponible() {
    return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  }

  function estMobile() {
    // Canal MOBILE-TERRAIN (§0) : sur PC la géoloc est imprécise et la « caméra » est une
    // webcam → dépôt sans valeur. On invite plutôt à passer sur téléphone.
    if (navigator.userAgentData && typeof navigator.userAgentData.mobile === "boolean") {
      return navigator.userAgentData.mobile;
    }
    return !!(window.matchMedia && window.matchMedia("(pointer: coarse)").matches);
  }

  function etapeDesktop(modal) {
    modal.vider();
    modal.corps.appendChild(el("h2", null, "Depuis votre téléphone"));
    modal.corps.appendChild(el("p", null,
      "Pour garantir une photo prise sur place, le dépôt se fait depuis un téléphone, " +
      "à proximité du feu et à distance de sécurité. Ouvrez cette page sur votre mobile :"));
    var lien = el("p");
    var a = el("a", { href: window.location.href });
    a.textContent = window.location.href;
    lien.appendChild(a);
    modal.corps.appendChild(lien);
  }

  function geolocaliser() {
    return new Promise(function (resolve, reject) {
      if (!navigator.geolocation) { reject(new Error("no-geo")); return; }
      navigator.geolocation.getCurrentPosition(
        function (pos) { resolve({ lat: pos.coords.latitude, lon: pos.coords.longitude }); },
        function () { reject(new Error("geo-refus")); },
        { enableHighAccuracy: true, timeout: 15000 }
      );
    });
  }

  function chargerFeux(pos) {
    return fetch(API + "/feux-proches?lat=" + pos.lat + "&lon=" + pos.lon)
      .then(function (r) { return r.ok ? r.json() : { feux: [] }; })
      .then(function (d) { return d.feux || []; });
  }

  function etapeFeu(modal, opts, pos) {
    chargerFeux(pos).then(function (feux) {
      if (!feux.length) {
        message(modal, "Aucun feu à proximité",
          "Aucun feu détecté dans un rayon de 10 km. Le dépôt est réservé aux photos prises " +
          "à proximité d'un feu, en restant à distance de sécurité.");
        return;
      }
      // Présélection : le feu de la page s'il est dans la liste (vérifié par la géoloc),
      // sinon le plus proche (la réalité physique prime en cas de conflit, §4).
      var idx = 0;
      if (opts.firePublicId) {
        feux.forEach(function (f, i) { if (f.public_id === opts.firePublicId) idx = i; });
      }
      modal.vider();
      modal.corps.appendChild(el("h2", null, "Confirmer le feu"));
      var form = el("div", { class: "sentifeu-feux" });
      feux.forEach(function (f, i) {
        var label = el("label");
        var radio = el("input", { type: "radio", name: "feu", value: String(i) });
        if (i === idx) radio.checked = true;
        label.appendChild(radio);
        label.appendChild(document.createTextNode(
          " Feu " + f.public_id + " — à " + f.distance_km.toFixed(1) + " km"));
        form.appendChild(label);
      });
      modal.corps.appendChild(form);
      var suivant = el("button", { class: "primaire" }, "Prendre la photo");
      suivant.addEventListener("click", function () {
        var choisi = form.querySelector("input[name=feu]:checked");
        etapeCapture(modal, opts, pos, feux[parseInt(choisi.value, 10)]);
      });
      modal.corps.appendChild(suivant);
    });
  }

  function etapeCapture(modal, opts, pos, feu) {
    modal.vider();
    modal.corps.appendChild(el("h2", null, "Prendre la photo"));
    var video = el("video", { autoplay: "", playsinline: "", muted: "" });
    modal.corps.appendChild(video);
    var capturer = el("button", { class: "primaire" }, "Capturer");
    modal.corps.appendChild(capturer);

    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false })
      .then(function (flux) {
        modal.setFlux(flux);
        video.srcObject = flux;
      })
      .catch(function () {
        // Permission refusée / caméra inaccessible (souvent navigateur in-app).
        etapeRepliInApp(modal);
      });

    capturer.addEventListener("click", function () {
      var canvas = el("canvas");
      canvas.width = video.videoWidth || 1280;
      canvas.height = video.videoHeight || 960;
      canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
      modal.stopFlux();
      canvas.toBlob(function (blob) { etapeConsentement(modal, opts, pos, feu, blob, canvas); },
        "image/jpeg", 0.92);
    });
  }

  function etapeConsentement(modal, opts, pos, feu, blob, canvas) {
    modal.vider();
    modal.corps.appendChild(el("h2", null, "Valider l'envoi"));
    var apercu = el("img", { class: "apercu", alt: "Aperçu de votre photo" });
    apercu.src = canvas.toDataURL("image/jpeg");
    modal.corps.appendChild(apercu);

    var email = el("input", { type: "email", placeholder: "E-mail (facultatif, pour être prévenu)",
      style: "width:100%;padding:.5rem;margin:.5rem 0" });
    modal.corps.appendChild(email);

    var labConsent = el("label", { style: "display:block;margin:.5rem 0" });
    var consent = el("input", { type: "checkbox" });
    labConsent.appendChild(consent);
    labConsent.appendChild(document.createTextNode(" " + CONSENT_TXT));
    modal.corps.appendChild(labConsent);

    var err = el("p", { class: "sentifeu-erreur" });
    modal.corps.appendChild(err);
    var envoyer = el("button", { class: "primaire" }, "Envoyer");
    modal.corps.appendChild(envoyer);

    envoyer.addEventListener("click", function () {
      if (!consent.checked) { err.textContent = "Le consentement est obligatoire."; return; }
      envoyer.disabled = true;
      err.textContent = "";
      var fd = new FormData();
      fd.append("image", blob, "photo.jpg");
      fd.append("fire_event_id", feu.fire_event_id);
      fd.append("hotspot_raw_id", feu.hotspot_raw_id);
      fd.append("lat", pos.lat);
      fd.append("lon", pos.lon);
      fd.append("consent", "1");
      if (email.value.trim()) fd.append("email", email.value.trim());

      fetch(API + "/deposer", { method: "POST", body: fd })
        .then(function (r) { return r.json().then(function (j) { return { code: r.status, body: j }; }); })
        .then(function (res) {
          if (res.code === 201 || res.code === 200) {
            message(modal, "Merci !",
              "Votre photo a bien été reçue. Elle sera visible après vérification.");
          } else {
            envoyer.disabled = false;
            err.textContent = messageErreur(res.code, res.body);
          }
        })
        .catch(function () { envoyer.disabled = false; err.textContent = "Envoi impossible. Réessayez."; });
    });
  }

  function messageErreur(code, body) {
    if (code === 429) return "Vous avez atteint la limite de dépôts. Réessayez plus tard.";
    if (code === 403) return "Dépôt refusé.";
    if (code === 413) return "Photo trop volumineuse.";
    if (code === 422) return "Ce feu n'est pas valide à votre position.";
    return (body && body.error) ? body.error : "Une erreur est survenue.";
  }

  function etapeRepliInApp(modal) {
    message(modal, "Ouvrez dans votre navigateur",
      "La prise de photo n'est pas disponible ici. Ouvrez cette page dans Safari ou Chrome " +
      "pour déposer une photo.", true);
  }

  // --- point d'entrée -----------------------------------------------------
  function ouvrir(opts) {
    opts = opts || {};
    var modal = Modal();
    if (!estMobile()) { etapeDesktop(modal); return; }       // PC → invitation mobile (§0)
    if (!captureDisponible()) { etapeRepliInApp(modal); return; }
    message(modal, "Localisation…", "Autorisez la géolocalisation pour trouver le feu proche.");
    geolocaliser().then(
      function (pos) { etapeFeu(modal, opts, pos); },
      function () {
        message(modal, "Localisation requise",
          "La géolocalisation est nécessaire pour rattacher votre photo à un feu. " +
          "Autorisez-la puis réessayez.", true);
      }
    );
  }

  function contexteBouton(b) {
    return {
      firePublicId: b.getAttribute("data-fire-public-id") || null,
      lieu: b.getAttribute("data-lieu") || "",
    };
  }

  function init() {
    var boutons = document.querySelectorAll("[data-sentifeu-depot]");
    Array.prototype.forEach.call(boutons, function (b) {
      b.addEventListener("click", function () { ouvrir(contexteBouton(b)); });
    });
    // Bouton FLOTTANT sur mobile (mobile-terrain) : toujours visible en scrollant, reprend le
    // contexte du 1er CTA de la page (le feu). Sur desktop, aucun (le dépôt se fait au tél).
    if (boutons.length && estMobile()) {
      injecterStyles();
      var flot = el("button", { type: "button", class: "sentifeu-flottant",
        "aria-label": "Déposer une photo" }, "📷 Déposer");
      flot.addEventListener("click", function () { ouvrir(contexteBouton(boutons[0])); });
      document.body.appendChild(flot);
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();

  window.SentifeuDepot = { ouvrir: ouvrir };
})();
