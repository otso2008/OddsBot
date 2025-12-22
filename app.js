/*
 * Revised Oddsbank frontend logic.
 *
 * Tämä versio lisää liiganavigaation, +EV- ja arbitraasi-listat sekä
 * automaattisen päivityksen. Sivusto näyttää vain tulevat ottelut.
 */

(function() {
  'use strict';
  const API_KEY = 'Goala411';
  const REFRESH_MS = 15000;

  // Helper: format ISO date string to localised date/time string in Finnish.
  function formatDateTime(isoStr) {
    try {
      const date = new Date(isoStr);
      return date.toLocaleString('fi-FI', {
        timeZone: 'Europe/Helsinki',
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch (e) {
      return isoStr;
    }
  }

  // DOM references
  const topBar = document.getElementById('top-bar');
  const matchesView = document.getElementById('matches-view');
  const matchesList = document.getElementById('matches-list');
  const matchView = document.getElementById('match-view');
  const backBtn = document.getElementById('back-btn');
  const matchHeading = document.getElementById('match-heading');
  const matchMeta = document.getElementById('match-meta');
  const marketsContainer = document.getElementById('markets-container');
  const evView = document.getElementById('ev-view');
  const evList = document.getElementById('ev-list');
  const arbsView = document.getElementById('arbs-view');
  const arbsList = document.getElementById('arbs-list');

  // Application state
  let currentView = null;
  let currentLeague = null;
  let currentMatch = null;
  let refreshTimer = null;

  function clearRefresh() {
    if (refreshTimer) {
      clearInterval(refreshTimer);
      refreshTimer = null;
    }
  }

  function setRefresh(fn) {
    clearRefresh();
    refreshTimer = setInterval(fn, REFRESH_MS);
  }

  function showView(view) {
    // hide all views
    matchesView.classList.add('hidden');
    matchView.classList.add('hidden');
    evView.classList.add('hidden');
    arbsView.classList.add('hidden');
    if (view === 'league') {
      matchesView.classList.remove('hidden');
    } else if (view === 'match') {
      matchView.classList.remove('hidden');
    } else if (view === 'ev') {
      evView.classList.remove('hidden');
    } else if (view === 'arbs') {
      arbsView.classList.remove('hidden');
    }
    currentView = view;
  }

  /**
   * Render all markets from odds and fair data.
   * @param {Array} fairData
   * @param {Array} oddsData
   */
  function renderMarkets(fairData, oddsData) {
    // Map fair probabilities: market_code -> outcome -> no_vig_odds
    const noVigByMarket = {};
    fairData.forEach((item) => {
      const market = item.market_code;
      const outcome = item.outcome;
      noVigByMarket[market] = noVigByMarket[market] || {};
      noVigByMarket[market][outcome] = item.no_vig_odds;
    });

    // Map odds: (market_code + line) -> bookmaker -> outcome -> price
    const oddsByMarket = {};
    oddsData.forEach((item) => {
      let key = item.market_code;
      if (item.line !== null && item.line !== undefined) {
        key = `${item.market_code} (line ${item.line})`;
      }
      oddsByMarket[key] = oddsByMarket[key] || {};
      const bm = item.bookmaker_name;
      oddsByMarket[key][bm] = oddsByMarket[key][bm] || {};
      oddsByMarket[key][bm][item.outcome] = item.price;
    });

    const marketKeys = Object.keys(oddsByMarket);
    marketKeys.sort();
    marketsContainer.innerHTML = '';
    if (marketKeys.length === 0) {
      marketsContainer.innerHTML = '<p>Ei saatavilla olevia kertoimia.</p>';
      return;
    }
    marketKeys.forEach((key) => {
      renderMarketSection(key, oddsByMarket[key], noVigByMarket);
    });
  }

  /**
   * Render a single market section including the table.
   * @param {string} key
   * @param {Object} bookieMap
   * @param {Object} noVigByMarket
   */
  function renderMarketSection(key, bookieMap, noVigByMarket) {
    const match = key.match(/^(.*?)(\s*\(line.*\))?$/);
    const baseMarketCode = match ? match[1] : key;
    // Collect outcomes
    const outcomeSet = new Set();
    if (noVigByMarket[baseMarketCode]) {
      Object.keys(noVigByMarket[baseMarketCode]).forEach((outcome) => outcomeSet.add(outcome));
    }
    Object.values(bookieMap).forEach((outcomes) => {
      Object.keys(outcomes).forEach((outcome) => outcomeSet.add(outcome));
    });
    const outcomes = Array.from(outcomeSet);
    outcomes.sort();
    // Section
    const section = document.createElement('div');
    section.className = 'market-section';
    const titleEl = document.createElement('div');
    titleEl.className = 'market-title';
    let displayTitle = baseMarketCode;
    if (baseMarketCode.toLowerCase() === 'match_outcome') {
      displayTitle = 'Ottelutulos';
    }
    titleEl.textContent = displayTitle;
    section.appendChild(titleEl);
    // Grid
    const grid = document.createElement('div');
    grid.className = 'market-grid';
    grid.style.gridTemplateColumns = `150px repeat(${outcomes.length}, 1fr)`;
    // Header empty cell
    const emptyHeader = document.createElement('div');
    emptyHeader.className = 'header-cell';
    emptyHeader.textContent = '';
    grid.appendChild(emptyHeader);
    outcomes.forEach((outcome) => {
      const cell = document.createElement('div');
      cell.className = 'header-cell';
      let label = outcome;
      const lower = outcome.toLowerCase();
      if (lower === 'home') label = 'Home';
      else if (lower === 'draw') label = 'Draw';
      else if (lower === 'away') label = 'Away';
      else label = outcome.charAt(0).toUpperCase() + outcome.slice(1);
      cell.textContent = label;
      grid.appendChild(cell);
    });
    // No-vig row
    const nvLabel = document.createElement('div');
    nvLabel.className = 'label-cell';
    nvLabel.textContent = 'No-vig';
    grid.appendChild(nvLabel);
    outcomes.forEach((outcome) => {
      const cell = document.createElement('div');
      cell.className = 'no-vig-cell';
      const nvValue = noVigByMarket[baseMarketCode] && noVigByMarket[baseMarketCode][outcome];
      cell.textContent = nvValue ? Number(nvValue).toFixed(2) : '-';
      grid.appendChild(cell);
    });
    // Bookmaker rows
    const bookmakerNames = Object.keys(bookieMap).sort();
    bookmakerNames.forEach((bm) => {
      const labelCell = document.createElement('div');
      labelCell.className = 'bookmaker-cell';
      labelCell.textContent = bm;
      grid.appendChild(labelCell);
      outcomes.forEach((outcome) => {
        const cell = document.createElement('div');
        cell.className = 'odds-cell';
        const value = bookieMap[bm][outcome];
        cell.textContent = value !== undefined ? Number(value).toFixed(2) : '-';
        grid.appendChild(cell);
      });
    });
    section.appendChild(grid);
    marketsContainer.appendChild(section);
  }

  // Beautify market code for EV/Arbs lists
  function beautifyMarket(code) {
    const lower = (code || '').toLowerCase();
    if (lower === 'match_outcome') return 'Ottelutulos';
    return code;
  }

  // Load matches for a league
  function loadMatches(league) {
    currentLeague = league;
    clearRefresh();
    fetch(`/api/matches?league=${encodeURIComponent(league)}`, {
      headers: {
        'X-API-Key': API_KEY,
      },
    })
      .then((res) => {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then((data) => {
        renderMatches(data);
        showView('league');
      })
      .catch((err) => {
        console.error('Matches fetch failed:', err);
        matchesList.innerHTML = '<p>Tietojen lataaminen epäonnistui.</p>';
        showView('league');
      });
  }

  // Render matches list
  function renderMatches(matches) {
    matchesList.innerHTML = '';
    matches.sort((a, b) => new Date(a.start_time) - new Date(b.start_time));
    matches.forEach((match) => {
      const card = document.createElement('div');
      card.className = 'match-card';
      card.tabIndex = 0;
      card.addEventListener('click', () => {
        loadMatch(match);
      });
      card.addEventListener('keypress', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          loadMatch(match);
        }
      });
      const teamsDiv = document.createElement('div');
      teamsDiv.className = 'teams';
      teamsDiv.textContent = `${match.home_team} – ${match.away_team}`;
      card.appendChild(teamsDiv);
      const metaDiv = document.createElement('div');
      metaDiv.className = 'meta';
      const leagueName = match.league || match.sport || '';
      const start = formatDateTime(match.start_time);
      metaDiv.textContent = `${leagueName} · ${start}`;
      card.appendChild(metaDiv);
      matchesList.appendChild(card);
    });
  }

  // Load single match view
  function loadMatch(match) {
    currentMatch = match;
    clearRefresh();
    matchHeading.textContent = `${match.home_team} – ${match.away_team}`;
    const start = formatDateTime(match.start_time);
    const leagueName = match.league || match.sport || '';
    matchMeta.textContent = `${leagueName} · ${start}`;
    marketsContainer.innerHTML = '';
    showView('match');
    const fairPromise = fetch(`/api/fair/${match.match_id}`, {
      headers: { 'X-API-Key': API_KEY },
    }).then((res) => {
      if (!res.ok) throw new Error('Fair API: ' + res.status);
      return res.json();
    });
    const oddsPromise = fetch(`/api/odds/${match.match_id}`, {
      headers: { 'X-API-Key': API_KEY },
    }).then((res) => {
      if (!res.ok) throw new Error('Odds API: ' + res.status);
      return res.json();
    });
    Promise.all([fairPromise, oddsPromise])
      .then(([fairData, oddsData]) => {
        renderMarkets(fairData, oddsData);
        setRefresh(refreshMatch);
      })
      .catch((err) => {
        console.error('Failed to load match details:', err);
        marketsContainer.innerHTML = '<p>Kertoimien lataaminen epäonnistui.</p>';
      });
  }

  // Refresh the current match's odds and fair probabilities
  function refreshMatch() {
    if (!currentMatch) return;
    const fairPromise = fetch(`/api/fair/${currentMatch.match_id}`, {
      headers: { 'X-API-Key': API_KEY },
    }).then((res) => {
      if (!res.ok) throw new Error('Fair API: ' + res.status);
      return res.json();
    });
    const oddsPromise = fetch(`/api/odds/${currentMatch.match_id}`, {
      headers: { 'X-API-Key': API_KEY },
    }).then((res) => {
      if (!res.ok) throw new Error('Odds API: ' + res.status);
      return res.json();
    });
    Promise.all([fairPromise, oddsPromise])
      .then(([fairData, oddsData]) => {
        renderMarkets(fairData, oddsData);
      })
      .catch((err) => {
        console.error('Failed to refresh match details:', err);
      });
  }

  // Load EV list
  function loadEV() {
    clearRefresh();
    fetch('/api/ev/top?hours=24', {
      headers: { 'X-API-Key': API_KEY },
    })
      .then((res) => {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then((data) => {
        renderEV(data);
        showView('ev');
        setRefresh(loadEV);
      })
      .catch((err) => {
        console.error('EV fetch failed:', err);
        evList.innerHTML = '<p>Tietojen lataaminen epäonnistui.</p>';
        showView('ev');
      });
  }

  // Render EV list into a table
  function renderEV(items) {
    evList.innerHTML = '';
    if (!items || items.length === 0) {
      evList.innerHTML = '<p>Ei saatavilla olevia +EV-vedot.</p>';
      return;
    }
    const table = document.createElement('table');
    const thead = document.createElement('thead');
    thead.innerHTML = '<tr><th>Ottelu</th><th>Bookkeri</th><th>Markkina</th><th>Outcome</th><th>Kerroin</th><th>EV %</th></tr>';
    table.appendChild(thead);
    const tbody = document.createElement('tbody');
    items.forEach((item) => {
      const tr = document.createElement('tr');
      const matchCell = document.createElement('td');
      matchCell.textContent = `${item.home_team || ''} – ${item.away_team || ''}`;
      const bookCell = document.createElement('td');
      bookCell.textContent = item.bookmaker_name;
      const marketCell = document.createElement('td');
      marketCell.textContent = beautifyMarket(item.market_code);
      const outcomeCell = document.createElement('td');
      outcomeCell.textContent = item.outcome;
      const oddsCell = document.createElement('td');
      oddsCell.textContent = item.odds !== undefined ? Number(item.odds).toFixed(2) : '-';
      const evCell = document.createElement('td');
      evCell.textContent = item.ev_fraction !== undefined ? (item.ev_fraction * 100).toFixed(2) + '%' : '-';
      tr.appendChild(matchCell);
      tr.appendChild(bookCell);
      tr.appendChild(marketCell);
      tr.appendChild(outcomeCell);
      tr.appendChild(oddsCell);
      tr.appendChild(evCell);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    evList.appendChild(table);
  }

  // Load arbitrage list
  function loadArbs() {
    clearRefresh();
    fetch('/api/arbs/latest?hours=24', {
      headers: { 'X-API-Key': API_KEY },
    })
      .then((res) => {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then((data) => {
        renderArbs(data);
        showView('arbs');
        setRefresh(loadArbs);
      })
      .catch((err) => {
        console.error('Arbs fetch failed:', err);
        arbsList.innerHTML = '<p>Tietojen lataaminen epäonnistui.</p>';
        showView('arbs');
      });
  }

  // Render arbitrage list
  function renderArbs(items) {
    arbsList.innerHTML = '';
    if (!items || items.length === 0) {
      arbsList.innerHTML = '<p>Ei saatavilla olevia arbitraaseja.</p>';
      return;
    }
    const table = document.createElement('table');
    const thead = document.createElement('thead');
    thead.innerHTML = '<tr><th>Ottelu</th><th>Markkina</th><th>ROI %</th><th>Legit</th></tr>';
    table.appendChild(thead);
    const tbody = document.createElement('tbody');
    items.forEach((item) => {
      const tr = document.createElement('tr');
      const matchCell = document.createElement('td');
      matchCell.textContent = `${item.home_team || ''} – ${item.away_team || ''}`;
      const marketCell = document.createElement('td');
      marketCell.textContent = beautifyMarket(item.market_code);
      const roiCell = document.createElement('td');
      roiCell.textContent = item.roi_fraction !== undefined ? (item.roi_fraction * 100).toFixed(2) + '%' : '-';
      const legsCell = document.createElement('td');
      const legs = item.legs;
      const parts = [];
      if (legs) {
        Object.keys(legs).forEach((key) => {
          const leg = legs[key];
          if (leg && typeof leg === 'object') {
            const b = leg.book || key;
            const o = leg.odds;
            if (o !== undefined) {
              parts.push(`${b}: ${Number(o).toFixed(2)}`);
            } else {
              parts.push(`${b}: ${o}`);
            }
          }
        });
      }
      legsCell.textContent = parts.join(', ');
      tr.appendChild(matchCell);
      tr.appendChild(marketCell);
      tr.appendChild(roiCell);
      tr.appendChild(legsCell);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    arbsList.appendChild(table);
  }

  // Event handlers for top bar buttons
  if (topBar) {
    topBar.querySelectorAll('button').forEach((btn) => {
      btn.addEventListener('click', () => {
        const view = btn.getAttribute('data-view');
        const league = btn.getAttribute('data-league');
        if (view === 'league' && league) {
          loadMatches(league);
        } else if (view === 'ev') {
          loadEV();
        } else if (view === 'arbs') {
          loadArbs();
        }
      });
    });
  }

  // Back button: navigate back to league view
  backBtn.addEventListener('click', () => {
    if (currentLeague) {
      loadMatches(currentLeague);
    } else {
      showView('league');
    }
  });

  // Initialize default view on page load
  document.addEventListener('DOMContentLoaded', () => {
    loadMatches('soccer_epl');
  });
})();
