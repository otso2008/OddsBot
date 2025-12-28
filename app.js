
document.addEventListener('DOMContentLoaded', () => {
  // --- API KEY (X-API-Key header) ---
  const API_KEY = "Goala411";
  const API_HEADERS = { "X-API-Key": API_KEY };

  // --- Element references ---
  const matchesTitle = document.getElementById('matches-title');
  const matchesGrid = document.getElementById('matches-grid');
  const matchHeading = document.getElementById('match-heading');
  const matchMeta = document.getElementById('match-meta');
  const oddsContainer = document.getElementById('odds-container');
  const fairContainer = document.getElementById('fair-container');
  const evList = document.getElementById('ev-list');
  const arbsList = document.getElementById('arbs-list');

  // --- View switching ---
  function showView(name) {
    ['matches', 'match', 'ev', 'arbs'].forEach((v) => {
      const view = document.getElementById(`${v}-view`);
      if (view) view.classList.add('hidden');
    });
    const active = document.getElementById(`${name}-view`);
    if (active) active.classList.remove('hidden');
  }

  // --- Format ISO date to Finnish locale ---
  function formatDateTime(dateStr) {
    try {
      const date = new Date(dateStr);
      return date.toLocaleString('fi-FI', { dateStyle: 'short', timeStyle: 'short' });
    } catch {
      return dateStr;
    }
  }

  // --- Helper: fetch JSON with error handling ---
  async function fetchJson(url) {
    const res = await fetch(url, { headers: API_HEADERS });
    const contentType = res.headers.get('content-type') || '';

    if (!contentType.includes('application/json')) {
      const text = await res.text().catch(() => '');
      throw new Error(`Ei-JSON vastaus (HTTP ${res.status}). ${text.slice(0, 80)}`);
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err?.detail ? `${err.detail} (HTTP ${res.status})` : `HTTP ${res.status}`);
    }
    return res.json();
  }

  // --- Fetch and display matches for a given league ---
  async function loadMatches(league, title) {
    showView('matches');
    matchesTitle.textContent = title || 'Tulevat ottelut';
    matchesGrid.innerHTML = '<div class="card"><p>Ladataan otteluita...</p></div>';

    try {
      const data = await fetchJson(`/api/matches?league=${encodeURIComponent(league)}`);

      if (!data || data.length === 0) {
        matchesGrid.innerHTML = '<div class="card"><p>Ei otteluita löytynyt.</p></div>';
        return;
      }

      matchesGrid.innerHTML = '';
      data.forEach((match) => {
        const card = document.createElement('div');
        card.className = 'card match-card';
        card.innerHTML = `
          <h3>${match.home_team} vs ${match.away_team}</h3>
          <p class="muted">${formatDateTime(match.start_time)}</p>
        `;
        card.addEventListener('click', () => {
          loadMatchDetails(match.match_id, match.home_team, match.away_team, match.start_time);
        });
        matchesGrid.appendChild(card);
      });
    } catch (error) {
      matchesGrid.innerHTML = `<div class="card"><p>Virhe otteluiden haussa: ${error.message}</p></div>`;
    }
  }

  // --- Fetch and display details for a single match ---
  async function loadMatchDetails(matchId, homeTeam, awayTeam, startTime) {
    showView('match');
    matchHeading.textContent = `${homeTeam} vs ${awayTeam}`;
    matchMeta.textContent = `Alkamisaika: ${formatDateTime(startTime)}`;
    oddsContainer.innerHTML = '<div class="card"><p>Ladataan kertoimia...</p></div>';
    fairContainer.innerHTML = '';

    try {
      const [odds, fair] = await Promise.all([
        fetchJson(`/api/odds/${matchId}`),
        fetchJson(`/api/fair/${matchId}`),
      ]);

      // Group odds by market_code
      const markets = {};
      (odds || []).forEach((row) => {
        (markets[row.market_code] ||= []).push(row);
      });

      oddsContainer.innerHTML = '';
      const marketCodes = Object.keys(markets);

      if (marketCodes.length === 0) {
        oddsContainer.innerHTML = '<div class="card"><p>Ei kertoimia saatavilla.</p></div>';
      } else {
        marketCodes.forEach((marketCode) => {
          const card = document.createElement('div');
          card.className = 'card market-card';

          // Custom rendering for H2H market: bookmaker rows and outcomes columns with no-vig reference
          if (marketCode === 'h2h') {
            let html = `<h3>${marketCode}</h3>`;

            // Build no-vig lookup from fair data
            const noVig = {};
            (fair || []).forEach((item) => {
              if (item.market_code === 'h2h') {
                noVig[item.outcome] = item.no_vig_odds;
              }
            });

            // Determine unique outcomes and bookmaker mapping
            const outcomesSet = new Set();
            const bookmakerMap = {};
            markets[marketCode].forEach((row) => {
              outcomesSet.add(row.outcome);
              if (!bookmakerMap[row.bookmaker_name]) {
                bookmakerMap[row.bookmaker_name] = {};
              }
              bookmakerMap[row.bookmaker_name][row.outcome] = row.price;
            });

            // Define a preferred order for common outcomes
            const preferredOrder = ['home', 'draw', 'away'];
            const outcomes = preferredOrder.filter((o) => outcomesSet.has(o)).concat(
              Array.from(outcomesSet).filter((o) => !preferredOrder.includes(o))
            );

            // Build table header
            html += '<table class="table odds-h2h-table">';
            html += '<thead><tr><th>Bookkeri</th>';
            outcomes.forEach((outc) => {
              html += `<th>${outc}</th>`;
            });
            html += '</tr></thead><tbody>';

            // No-vig reference row
            if (Object.keys(noVig).length > 0) {
              html += '<tr class="no-vig-row"><th>No-vig</th>';
              outcomes.forEach((outc) => {
                const val = noVig[outc];
                const valStr = typeof val === 'number' ? val.toFixed(2) : (val || '');
                html += `<td class="no-vig-cell">${valStr}</td>`;
              });
              html += '</tr>';
            }

            // Bookmaker rows
            Object.keys(bookmakerMap).forEach((bookName) => {
              html += `<tr><th>${bookName}</th>`;
              outcomes.forEach((outc) => {
                const price = bookmakerMap[bookName][outc];
                if (price == null) {
                  html += '<td>-</td>';
                } else {
                  const numPrice = typeof price === 'number' ? price : parseFloat(price);
                  const refVal = noVig[outc];
                  let cls = '';
                  if (refVal != null) {
                    // Compare offered odds to no-vig; higher odds mean better value
                    cls = numPrice > refVal ? 'odds-better' : (numPrice < refVal ? 'odds-worse' : '');
                  }
                  html += `<td class="${cls}">${numPrice.toFixed(2)}</td>`;
                }
              });
              html += '</tr>';
            });

            html += '</tbody></table>';
            card.innerHTML = html;
            oddsContainer.appendChild(card);
            return; // skip default rendering for h2h
          }

          // Default rendering for other markets
          let html = `<h3>${marketCode}</h3>`;
          html += `
            <table class="table">
              <thead>
                <tr>
                  <th>Kohde</th>
                  <th>Bookkeri</th>
                  <th>Kerroin</th>
                  <th>Raja</th>
                </tr>
              </thead>
              <tbody>
          `;
          markets[marketCode].forEach((row) => {
            const price = typeof row.price === 'number' ? row.price.toFixed(2) : row.price;
            const line = row.line ?? '';
            html += `<tr><td>${row.outcome}</td><td>${row.bookmaker_name}</td><td>${price}</td><td>${line}</td></tr>`;
          });
          html += '</tbody></table>';
          card.innerHTML = html;
          oddsContainer.appendChild(card);
        });
      }

      // Render fair probabilities (no-vig and margin)
      if (fair && fair.length > 0) {
        const heading = document.createElement('div');
        heading.className = 'card';
        heading.innerHTML = `<h3>Fair probabilities</h3><p class="muted">No-vig + marginaali</p>`;
        fairContainer.appendChild(heading);
        fair.forEach((item) => {
          const card = document.createElement('div');
          card.className = 'card fair-card';
          card.innerHTML = `
            <h3>${item.market_code} – ${item.outcome}</h3>
            <div class="kv">
              <span class="pill">Fair: ${(item.fair_probability * 100).toFixed(2)}%</span>
              <span class="pill">No-vig: ${item.no_vig_odds.toFixed(2)}</span>
              <span class="pill">Margin: ${(item.margin * 100).toFixed(2)}%</span>
              <span class="pill">Ref: ${item.reference_bookmaker_name}</span>
            </div>
          `;
          fairContainer.appendChild(card);
        });
      }
    } catch (err) {
      oddsContainer.innerHTML = `<div class="card"><p>Virhe kertoimien haussa: ${err.message}</p></div>`;
    }
  }

  // --- Fetch and display +EV selections ---
  async function loadEV() {
    showView('ev');
    evList.innerHTML = '<div class="card"><p>Ladataan +EV-kohteita...</p></div>';
    try {
      const data = await fetchJson('/api/ev/top?limit=100');
      if (!data || data.length === 0) {
        evList.innerHTML = '<div class="card"><p>Ei +EV-kohteita.</p></div>';
        return;
      }
      evList.innerHTML = '';
      data.forEach((ev) => {
        const card = document.createElement('div');
        card.className = 'card ev-card';
        const evPercent = (ev.ev_fraction * 100).toFixed(2);
        card.innerHTML = `
          <h3>${ev.home_team} vs ${ev.away_team}</h3>
          <p class="muted">${ev.league || ''} • ${formatDateTime(ev.start_time)}</p>
          <div class="kv">
            <span class="pill">Market: ${ev.market_code}</span>
            <span class="pill">Kohde: ${ev.outcome}</span>
            <span class="pill">EV: ${evPercent}%</span>
          </div>
          <p class="muted">Bookkeri: ${ev.bookmaker_name} • Ref: ${ev.reference_bookmaker_name}</p>
          <p class="muted">Tarjottu kerroin: ${Number(ev.odds).toFixed(2)} • Fair: ${(ev.fair_probability * 100).toFixed(2)}%</p>
        `;
        evList.appendChild(card);
      });
    } catch (error) {
      evList.innerHTML = `<div class="card"><p>Virhe +EV-kohteiden haussa: ${error.message}</p></div>`;
    }
  }

  // --- Fetch and display arbitrage opportunities ---
  async function loadArbs() {
    showView('arbs');
    arbsList.innerHTML = '<div class="card"><p>Ladataan arbitraaseja...</p></div>';
    try {
      const data = await fetchJson('/api/arbs/latest?limit=100');
      if (!data || data.length === 0) {
        arbsList.innerHTML = '<div class="card"><p>Ei arbitraaseja.</p></div>';
        return;
      }
      arbsList.innerHTML = '';
      data.forEach((arb) => {
        const card = document.createElement('div');
        card.className = 'card arb-card';
        const roiPercent = (arb.roi_fraction * 100).toFixed(2);
        // Build legs list
        let legsHtml = '';
        if (arb.legs && typeof arb.legs === 'object') {
          Object.keys(arb.legs).forEach((outcome) => {
            const leg = arb.legs[outcome];
            legsHtml += `<li>${outcome}: ${leg.book} @ ${Number(leg.odds).toFixed(2)}</li>`;
          });
        }
        // Build stake split list
        let stakesHtml = '';
        if (arb.stake_split && typeof arb.stake_split === 'object') {
          Object.keys(arb.stake_split).forEach((outcome) => {
            const stake = arb.stake_split[outcome];
            stakesHtml += `<li>${outcome}: ${Number(stake).toFixed(2)} €</li>`;
          });
        }
        card.innerHTML = `
          <h3>${arb.home_team} vs ${arb.away_team}</h3>
          <p class="muted">${arb.league || ''} • ${formatDateTime(arb.start_time)}</p>
          <div class="kv">
            <span class="pill">Market: ${arb.market_code}</span>
            <span class="pill">ROI: ${roiPercent}%</span>
          </div>
          <div class="stack" style="margin-top:10px">
            <div class="card" style="box-shadow:none">
              <h3 style="margin:0 0 8px; font-size:14px;">Legit</h3>
              <ul style="margin:0; padding-left:18px; color:rgba(0,0,0,.75); font-size:14px;">
                ${legsHtml || '<li>-</li>'}
              </ul>
            </div>
            <div class="card" style="box-shadow:none">
              <h3 style="margin:0 0 8px; font-size:14px;">Panosjako</h3>
              <ul style="margin:0; padding-left:18px; color:rgba(0,0,0,.75); font-size:14px;">
                ${stakesHtml || '<li>-</li>'}
              </ul>
            </div>
          </div>
        `;
        arbsList.appendChild(card);
      });
    } catch (error) {
      arbsList.innerHTML = `<div class="card"><p>Virhe arbitraasien haussa: ${error.message}</p></div>`;
    }
  }

  // --- Activate nav button (highlight active) ---
  function activateNav(targetButton) {
    document.querySelectorAll('.nav-button').forEach((btn) => btn.classList.remove('active'));
    if (targetButton) targetButton.classList.add('active');
  }

  // --- Event listeners ---
  document.querySelectorAll('button[data-league]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const league = btn.getAttribute('data-league');
      const title = btn.textContent;
      activateNav(btn);
      loadMatches(league, title);
    });
  });

  document.getElementById('ev-button')?.addEventListener('click', (event) => {
    activateNav(event.currentTarget);
    loadEV();
  });

  document.getElementById('arbs-button')?.addEventListener('click', (event) => {
    activateNav(event.currentTarget);
    loadArbs();
  });

  document.getElementById('back-btn')?.addEventListener('click', () => {
    showView('matches');
  });
});
