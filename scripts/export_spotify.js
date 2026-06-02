/**
 * ytmusic-importer — Script d'export Spotify
 *
 * Colle ce script dans la console du navigateur (F12 → Console)
 * depuis la page de ta playlist sur open.spotify.com
 *
 * Le CSV se télécharge automatiquement une fois le scroll terminé.
 */

(async function spotifyExport() {
  const tracks = new Map();

  // Trouve le conteneur de scroll principal (le plus grand scrollHeight)
  function getMainScroller() {
    const viewports = [...document.querySelectorAll('[data-overlayscrollbars-viewport]')];
    return viewports.sort((a, b) => b.scrollHeight - a.scrollHeight)[0]
      || document.documentElement;
  }

  function extractVisible() {
    document.querySelectorAll('[data-testid="tracklist-row"]').forEach(row => {
      // Titre
      const titleEl = row.querySelector('[data-testid="internal-track-link"]');
      const title = titleEl?.textContent?.trim();
      if (!title) return;

      // Artiste(s)
      const col2 = row.querySelector('[aria-colindex="2"]');
      const artistEls = col2
        ? col2.querySelectorAll('a[href*="/artist/"]')
        : row.querySelectorAll('a[href*="/artist/"]');
      const artist = [...artistEls].map(a => a.textContent.trim()).join(', ');

      // Album
      const col3 = row.querySelector('[aria-colindex="3"]');
      const album = col3?.querySelector('a[href*="/album/"]')?.textContent?.trim()
        || col3?.textContent?.trim()
        || '';

      // Date d'ajout (colonne 4)
      const col4 = row.querySelector('[aria-colindex="4"]');
      const dateAdded = col4?.textContent?.trim() || '';

      // Durée (colonne 5)
      const col5 = row.querySelector('[aria-colindex="5"]')
        || row.querySelector('[data-testid="tracklist-duration"]');
      const duration = col5?.textContent?.trim() || '';

      const key = title + '|||' + artist;
      if (!tracks.has(key)) {
        tracks.set(key, { title, artist, album, dateAdded, duration });
      }
    });
  }

  const scroller = getMainScroller();
  console.log('%cSpotify Exporter démarré', 'color: #1DB954; font-size: 14px; font-weight: bold');
  console.log('Conteneur scroll :', scroller.tagName, scroller.scrollHeight + 'px de haut');

  // Scroll progressif jusqu'à ce que 5 passes consécutives ne trouvent rien de nouveau
  let stableRounds = 0;
  let lastSize = -1;

  while (stableRounds < 5) {
    extractVisible();
    scroller.scrollBy(0, 1500);
    await new Promise(r => setTimeout(r, 700));

    if (tracks.size === lastSize) {
      stableRounds++;
    } else {
      stableRounds = 0;
      console.log(`%c${tracks.size} titres collectés...`, 'color: #1DB954');
    }
    lastSize = tracks.size;
  }

  if (tracks.size === 0) {
    console.warn('⚠️ Aucun titre trouvé. Vérifie que tu es sur la page d\'une playlist.');
    return;
  }

  // Export CSV avec BOM UTF-8 (pour compatibilité Excel et ytmusic-importer)
  const escape = v => `"${(v || '').replace(/"/g, '""')}"`;
  const header = ['Titre', 'Artiste', 'Album', "Date d'ajout", 'Durée'].join(',');
  const rows = [...tracks.values()].map(t =>
    [t.title, t.artist, t.album, t.dateAdded, t.duration].map(escape).join(',')
  );

  const csv = '﻿' + [header, ...rows].join('\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const a = Object.assign(document.createElement('a'), {
    href: URL.createObjectURL(blob),
    download: 'spotify_playlist.csv'
  });
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);

  console.log(
    `%c✓ Terminé ! ${tracks.size} titres exportés dans spotify_playlist.csv`,
    'color: #1DB954; font-weight: bold'
  );
})();
