/*
  Oddsbank-frontendin JavaScript. Tämä skripti hallitsee näkymien
  näyttämistä, hakee dataa FastAPI-backendiltä ja renderöi tulokset
  käyttöliittymään. Nappien klikkaukset käynnistävät oikeat API-kutsut.
  API-kutsut tehdään suhteellisilla poluilla, mikä olettaa että tämä
  frontend on palveltuna samasta domainista ja portista kuin FastAPI.

  Funktiot:
    - showView: piilottaa kaikki näkymät ja näyttää halutun.
    - loadMatches: hakee ottelut tietylle liigalle ja renderöi listan.
    - loadMatchDetails: hakee yksittäisen ottelun kertoimet ja fair-
      probability-tiedot ja näyttää ne.
    - loadEV: hakee ja näyttää +EV-kohteet.
    - loadArbs: hakee ja näyttää arbitraasit.

  Huomioi: tämä skripti ei sisällä kovia linkkejä ulkoisiin palvelimiin.
  Jos backend on eri osoitteessa kuin frontend, muuta fetch()-kutsujen
  URL:it vastaamaan oikeaa hostia (esim. `fetch('http://localhost:8000/api/...')`).
*/

document.addEventListener('DOMContentLoaded', () => {
  // Elementtiviitteet
  const matchesTitle = document.getElementById('matches-title');
  const matchesList = document.getElementById('matches-list');
  const matchHeading = document.getElementById('match-heading');
  const matchMeta = document.getElementById('match-meta');
  const oddsContainer = document.getElementById('odds-container');
  const fairContainer = document.getElementById('fair-container');
  const evList = document.getElementById('ev-list');
  const arbsList = document.getElementById('arbs-list');

  // Näkymien hallinta
  function showView(name) {
    ['matches', 'match', 'ev', 'arbs'].forEach((v) => {
      const view = document.getElementById(`${v}-view`);
      if (view) {
        view.classList.add('hidden');
      }
    });
    const active = document.getElementById(`${name}-view`);
    if (active) {
      active.classList.remove('hidden');
    }
  }

  // Aikamuodon muuttaminen Suomen paikalliseen aikaan
  function formatDateTime(dateStr) {
    try {
      const date = new Date(dateStr);
      return date.toLocaleString('fi-FI', {
        dateStyle: 'short',
        timeStyle: 'short',
      });
    } catch (err) {
      return dateStr;
    }
  }

  // Otteluiden hakeminen valitulle liigalle
  async function loadMatches(league, title) {
    showView('matches');
    // Päivitä listan otsikko käyttäjän näkemällä tekstillä
    matchesTitle.textContent = title || 'Tulevat ottelut';
    matchesList.innerHTML = '<p>Ladataan otteluita...</p>';
    try {
      const response = await fetch(`/api/matches?league=${encodeURIComponent(league)}`);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const data = await response.json();
      if (!data || data.length === 0) {
        matchesList.innerHTML = '<p>Ei otteluita löytynyt.</p>';
        return;
      }
      matchesList.innerHTML = '';
      data.forEach((match) => {
        const card = document.createElement('div');
        card.className = 'card match-card';
        card.dataset.matchId = match.match_id;
        card.dataset.home = match.home_team;
        card.dataset.away = match.away_team;
        card.dataset.start = match.start_time;
        card.innerHTML = `
          <h3>${match.home_team} vs ${match.away_team}</h3>
          <p class="match-meta">${formatDateTime(match.start_time)}</p>
        `;
        // Klikattaessa ladataan ottelun kertoimet
        card.addEventListener('click', () => {
          loadMatchDetails(match.match_id, match.home_team, match.away_team, match.start_time);
        });
        matchesList.appendChild(card);
      });
    } catch (error) {
      matchesList.innerHTML = `<p>Virhe otteluiden haussa: ${error.message}</p>`;
    }
  }

  // Yksittäisen ottelun tietojen hakeminen
  async function loadMatchDetails(matchId, homeTeam, awayTeam, startTime) {
    showView('match');
    matchHeading.textContent = `${homeTeam} vs ${awayTeam}`;
    matchMeta.textContent = `Alkamisaika: ${formatDateTime(startTime)}`;
    oddsContainer.innerHTML = '<p>Ladataan kertoimia...</p>';
    fairContainer.innerHTML = '';
    try {
      const [oddsRes, fairRes] = await Promise.all([
        fetch(`/api/odds/${matchId}`),
        fetch(`/api/fair/${matchId}`),
      ]);
      const odds = oddsRes.ok ? await oddsRes.json() : [];
      const fair = fairRes.ok ? await fairRes.json() : [];

      // Ryhmitellään kertoimet markkinoittain
      const markets = {};
      odds.forEach((row) => {
        if (!markets[row.market_code]) {
          markets[row.market_code] = [];
        }
        markets[row.market_code].push(row);
      });
      oddsContainer.innerHTML = '';
      // Luo kortti jokaista markkinaa kohden
      Object.keys(markets).forEach((marketCode) => {
        const card = document.createElement('div');
        card.className = 'card market-card';
        let html = `<h3>${marketCode}</h3>`;
        html += '<table class="table"><thead><tr><th>Kohde</th><th>Bookkeri</th><th>Kerroin</th><th>Raja</th></tr></thead><tbody>';
        markets[marketCode].forEach((row) => {
          html += `<tr><td>${row.outcome}</td><td>${row.bookmaker_name}</td><td>${row.price.toFixed(2)}</td><td>${row.line ?? ''}</td></tr>`;
        });
        html += '</tbody></table>';
        card.innerHTML = html;
        oddsContainer.appendChild(card);
      });

      // Näytä fair probabilities jos saatavilla
      if (fair && fair.length > 0) {
        fairContainer.innerHTML = '<h2>Fair probabilities</h2>';
        fair.forEach((item) => {
          const card = document.createElement('div');
          card.className = 'card fair-card';
          card.innerHTML = `
            <p><strong>${item.market_code} – ${item.outcome}</strong></p>
            <p>Fair probability: ${(item.fair_probability * 100).toFixed(2)} %</p>
            <p>No‑vig odds: ${item.no_vig_odds.toFixed(2)}</p>
            <p>Margin: ${(item.margin * 100).toFixed(2)} %</p>
            <p>Ref: ${item.reference_bookmaker_name}</p>
          `;
          fairContainer.appendChild(card);
        });
      }
    } catch (err) {
      oddsContainer.innerHTML = `<p>Virhe kertoimien haussa: ${err.message}</p>`;
    }
  }

  // +EV-kohteiden hakeminen ja renderöinti
  async function loadEV() {
    showView('ev');
    evList.innerHTML = '<p>Ladataan +EV-kohteita...</p>';
    try {
      const response = await fetch('/api/ev/top?limit=100');
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const data = await response.json();
      if (!data || data.length === 0) {
        evList.innerHTML = '<p>Ei +EV-kohteita.</p>';
        return;
      }
      evList.innerHTML = '';
      data.forEach((ev) => {
        const card = document.createElement('div');
        card.className = 'card ev-card';
        const evPercent = (ev.ev_fraction * 100).toFixed(2);
        card.innerHTML = `
          <div class="ev-header">${ev.home_team} vs ${ev.away_team} (${ev.league})</div>
          <p><strong>Market:</strong> ${ev.market_code}</p>
          <p><strong>Bookkeri:</strong> ${ev.bookmaker_name}</p>
          <p><strong>Referenssi:</strong> ${ev.reference_bookmaker_name}</p>
          <p><strong>Tarjottu kerroin:</strong> ${ev.odds.toFixed(2)}</p>
          <p><strong>Todennäköisyys (fair):</strong> ${(ev.fair_probability * 100).toFixed(2)} %</p>
          <p><strong>EV:</strong> ${evPercent} %</p>
          <p><strong>Kohde:</strong> ${ev.outcome}</p>
          <p><strong>Alkamisaika:</strong> ${formatDateTime(ev.start_time)}</p>
        `;
        evList.appendChild(card);
      });
    } catch (error) {
      evList.innerHTML = `<p>Virhe +EV-kohteiden haussa: ${error.message}</p>`;
    }
  }

  // Arbitraasien hakeminen ja renderöinti
  async function loadArbs() {
    showView('arbs');
    arbsList.innerHTML = '<p>Ladataan arbitraaseja...</p>';
    try {
      const response = await fetch('/api/arbs/latest?limit=100');
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const data = await response.json();
      if (!data || data.length === 0) {
        arbsList.innerHTML = '<p>Ei arbitraaseja.</p>';
        return;
      }
      arbsList.innerHTML = '';
      data.forEach((arb) => {
        const card = document.createElement('div');
        card.className = 'card arb-card';
        const roiPercent = (arb.roi_fraction * 100).toFixed(2);
        // Generoi lista vedoista (legs)
        let legsHtml = '';
        if (arb.legs) {
          Object.keys(arb.legs).forEach((outcome) => {
            const leg = arb.legs[outcome];
            legsHtml += `<li>${outcome}: ${leg.book} @ ${leg.odds.toFixed(2)}</li>`;
          });
        }
        // Generoi panosjako
        let stakesHtml = '';
        if (arb.stake_split) {
          Object.keys(arb.stake_split).forEach((outcome) => {
            const stake = arb.stake_split[outcome];
            stakesHtml += `<li>${outcome}: ${stake.toFixed(2)} €</li>`;
          });
        }
        card.innerHTML = `
          <div class="arb-header">${arb.home_team} vs ${arb.away_team} (${arb.league})</div>
          <p><strong>Market:</strong> ${arb.market_code}</p>
          <p><strong>ROI:</strong> ${roiPercent} %</p>
          <p><strong>Alkamisaika:</strong> ${formatDateTime(arb.start_time)}</p>
          <p><strong>Legit:</strong></p>
          <ul class="legs-list">${legsHtml}</ul>
          <p><strong>Panosjako:</strong></p>
          <ul class="stakes-list">${stakesHtml}</ul>
        `;
        arbsList.appendChild(card);
      });
    } catch (error) {
      arbsList.innerHTML = `<p>Virhe arbitraasien haussa: ${error.message}</p>`;
    }
  }

  // Utility: aktivoi valittu nav-nappi ja poista muilta 'active'-luokka
  function activateNav(targetButton) {
    document.querySelectorAll('.nav-button').forEach((btn) => {
      btn.classList.remove('active');
    });
    if (targetButton) {
      targetButton.classList.add('active');
    }
  }

  // Nappien kuuntelijat
  document.querySelectorAll('button[data-league]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const league = btn.getAttribute('data-league');
      const title = btn.textContent;
      activateNav(btn);
      loadMatches(league, title);
    });
  });
  document.getElementById('ev-button').addEventListener('click', (event) => {
    activateNav(event.currentTarget);
    loadEV();
  });
  document.getElementById('arbs-button').addEventListener('click', (event) => {
    activateNav(event.currentTarget);
    loadArbs();
  });
  document.getElementById('back-btn').addEventListener('click', () => {
    // Paluu ottelulistaan: näytetään entinen lista ilman uutta hakua
    showView('matches');
  });
});
