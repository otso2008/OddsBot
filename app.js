/*
 * Oddsbank frontend logic.
 *
 * Tämä skripti hakee kaikki tulevat ottelut backendistä ja renderöi ne
 * listaan. Kun käyttäjä klikkaa ottelua, haetaan kyseisen ottelun
 * kertoimet ja no-vig-oddsit ja näytetään ne taulukkomuodossa. Toteutus
 * on responsiivinen ja käyttää puhtaasti vanilla JavaScriptiä ilman
 * riippuvuuksia.
 */

(function () {
  const API_KEY = 'Goala411';

  // DOM elements
  const matchesView = document.getElementById('matches-view');
  const matchesList = document.getElementById('matches-list');
  const matchView = document.getElementById('match-view');
  const backBtn = document.getElementById('back-btn');
  const matchHeading = document.getElementById('match-heading');
  const matchMeta = document.getElementById('match-meta');
  const marketsContainer = document.getElementById('markets-container');

  /**
   * Utility: format ISO date string to localised date/time string in Finnish.
   * @param {string} isoStr
   * @returns {string}
   */
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

  /**
   * Show the matches list and hide match detail view.
   */
  function showMatchesView() {
    matchView.classList.add('hidden');
    matchesView.classList.remove('hidden');
  }

  /**
   * Show match detail view and hide matches list.
   */
  function showMatchView() {
    matchesView.classList.add('hidden');
    matchView.classList.remove('hidden');
  }

  /**
   * Fetch the list of upcoming matches from backend.
   */
  function fetchMatches() {
    fetch("/api/matches/upcoming", {
  headers: {
    "X-API-Key": "Goala411"
  }
})



      .then((res) => {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then((data) => {
        renderMatches(data);
      })
      .catch((err) => {
        console.error('Matches fetch failed:', err);
        matchesList.innerHTML = '<p>Tietojen lataaminen epäonnistui.</p>';
      });
  }

  /**
   * Render the matches list.
   * @param {Array} matches
   */
  function renderMatches(matches) {
    matchesList.innerHTML = '';
    // Sort by start_time ascending
    matches.sort((a, b) => new Date(a.start_time) - new Date(b.start_time));
    matches.forEach((match) => {
      const card = document.createElement('div');
      card.className = 'match-card';
      card.tabIndex = 0;
      card.addEventListener('click', () => {
        loadMatch(match);
      });
      // For accessibility: allow Enter key to trigger click
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
      const league = match.league || match.sport || '';
      const start = formatDateTime(match.start_time);
      metaDiv.textContent = `${league} · ${start}`;
      card.appendChild(metaDiv);

      matchesList.appendChild(card);
    });
  }

  /**
   * Load details for a selected match.
   * @param {Object} match
   */
  function loadMatch(match) {
    // Update header/meta
    matchHeading.textContent = `${match.home_team} – ${match.away_team}`;
    const start = formatDateTime(match.start_time);
    const league = match.league || match.sport || '';
    matchMeta.textContent = `${league} · ${start}`;
    // Clear previous markets
    marketsContainer.innerHTML = '';
    showMatchView();
    // Fetch fair probs and odds concurrently
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
      })
      .catch((err) => {
        console.error('Failed to load match details:', err);
        marketsContainer.innerHTML = '<p>Kertoimien lataaminen epäonnistui.</p>';
      });
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
      // Build key: include line if present
      let key = item.market_code;
      if (item.line !== null && item.line !== undefined) {
        key = `${item.market_code} (line ${item.line})`;
      }
      oddsByMarket[key] = oddsByMarket[key] || {};
      const bm = item.bookmaker_name;
      oddsByMarket[key][bm] = oddsByMarket[key][bm] || {};
      oddsByMarket[key][bm][item.outcome] = item.price;
    });

    // Collect all market keys
    const marketKeys = Object.keys(oddsByMarket);
    marketKeys.sort();
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
    // Determine market_code (remove line part for fair lookup)
    const match = key.match(/^(.*?)(\s*\(line.*\))?$/);
    const baseMarketCode = match ? match[1] : key;
    // Collect outcomes from both fair data and odds
    const outcomeSet = new Set();
    if (noVigByMarket[baseMarketCode]) {
      Object.keys(noVigByMarket[baseMarketCode]).forEach((outcome) => outcomeSet.add(outcome));
    }
    Object.values(bookieMap).forEach((outcomes) => {
      Object.keys(outcomes).forEach((outcome) => outcomeSet.add(outcome));
    });
    const outcomes = Array.from(outcomeSet);
    outcomes.sort();
    // Start creating DOM
    const section = document.createElement('div');
    section.className = 'market-section';
    // Title
    const titleEl = document.createElement('div');
    titleEl.className = 'market-title';
    // Beautify title: if match_outcome, show "Ottelutulos" else show market code as is
    let displayTitle = baseMarketCode;
    if (baseMarketCode.toLowerCase() === 'match_outcome') {
      displayTitle = 'Ottelutulos';
    }
    titleEl.textContent = displayTitle;
    section.appendChild(titleEl);
    // Grid container
    const grid = document.createElement('div');
    grid.className = 'market-grid';
    // First column width fixed, others equal
    grid.style.gridTemplateColumns = `150px repeat(${outcomes.length}, 1fr)`;
    // Header row
    const emptyHeader = document.createElement('div');
    emptyHeader.className = 'header-cell';
    emptyHeader.textContent = '';
    grid.appendChild(emptyHeader);
    outcomes.forEach((outcome) => {
      const cell = document.createElement('div');
      cell.className = 'header-cell';
      // Capitalise and translate if needed
      let label = outcome;
      // Convert common codes to Finnish/English labels
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

  // Event listeners
  backBtn.addEventListener('click', () => {
    showMatchesView();
  });

  // Load matches on start
  fetchMatches();
})();
