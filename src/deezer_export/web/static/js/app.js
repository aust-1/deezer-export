// Deezer Export : bascule clair / sombre et suivi de l'avancement d'un export.
// Tout fonctionne sans JS (le suivi se rafraîchit alors via <noscript>) : ce fichier ne fait
// qu'améliorer l'expérience.
(function () {
  'use strict';

  // --- Thème : mémorisé dans localStorage (clé partagée avec le portfolio) -------------------
  var root = document.documentElement;
  var toggle = document.querySelector('[data-theme-toggle]');

  function currentTheme() {
    if (root.dataset.theme === 'light' || root.dataset.theme === 'dark') return root.dataset.theme;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  function renderToggle() {
    if (!toggle) return;
    var next = currentTheme() === 'dark' ? 'clair' : 'sombre';
    toggle.textContent = next.charAt(0).toUpperCase() + next.slice(1);
    toggle.setAttribute('aria-label', 'Passer en thème ' + next);
  }

  if (toggle) {
    toggle.addEventListener('click', function () {
      var next = currentTheme() === 'dark' ? 'light' : 'dark';
      root.dataset.theme = next;
      try {
        localStorage.setItem('er-theme', next);
      } catch (e) {
        /* stockage indisponible : le choix vaut pour cette page */
      }
      renderToggle();
    });
    renderToggle();
  }

  // --- Suivi d'un export ------------------------------------------------------------------
  var progress = document.querySelector('[data-progress-url]');
  if (!progress) return;

  var url = progress.getAttribute('data-progress-url');
  var percent = progress.querySelector('[data-percent]');
  var fill = progress.querySelector('[data-fill]');
  var step = progress.querySelector('[data-step]');
  var count = progress.querySelector('[data-count]');
  var failures = 0;

  function update(state) {
    percent.textContent = state.percent + ' %';
    fill.style.setProperty('--w', state.percent + '%');
    step.textContent = state.step;
    count.textContent = state.total ? state.done + ' / ' + state.total + ' albums' : '';
  }

  function poll() {
    fetch(url, { headers: { Accept: 'application/json' }, cache: 'no-store' })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (state) {
        failures = 0;
        if (state.status === 'done' || state.status === 'error') {
          window.location.reload();
          return;
        }
        update(state);
        window.setTimeout(poll, 1000);
      })
      .catch(function () {
        failures += 1;
        window.setTimeout(poll, Math.min(1000 * Math.pow(2, failures), 15000));
      });
  }

  window.setTimeout(poll, 600);
})();
