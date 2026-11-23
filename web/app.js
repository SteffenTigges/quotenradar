/* Quotenradar Web-App. Liest data.json (stündlich von GitHub Actions erzeugt) und zeigt sie an. */
(function () {
  "use strict";

  var D = null;
  var state = { sport: "all", q: "", bank: 1000, frac: 0.25 };
  try {
    var saved = JSON.parse(localStorage.getItem("edgelab-settings") || "{}");
    if (saved.bank) state.bank = saved.bank;
    if (saved.frac) state.frac = saved.frac;
  } catch (e) { /* ohne Speicher weiter */ }

  var NAV = [
    { id: "spiele", label: "Spiele", icon: '<path d="M4 6h16M4 12h16M4 18h10"/>' },
    { id: "value", label: "Value", icon: '<path d="M4 17l5-5 4 4 7-8"/><path d="M15 8h5v5"/>' },
    { id: "grosswetten", label: "Großwetten", icon: '<circle cx="12" cy="12" r="8"/><path d="M12 8v8M9.5 10.5c0-1.4 1.1-2 2.5-2s2.5.6 2.5 2-1.1 1.8-2.5 2-2.5.7-2.5 2 1.1 2 2.5 2 2.5-.6 2.5-2"/>' },
    { id: "wetten", label: "Meine Wetten", icon: '<rect x="5" y="4" width="14" height="16" rx="2"/><path d="M9 9h6M9 13h6M9 17h3"/>' },
    { id: "info", label: "Info", icon: '<circle cx="12" cy="12" r="8"/><path d="M12 11v5M12 8h.01"/>' }
  ];
  var BOOK_URL = {
    Tipico: "https://sports.tipico.de/de", Betano: "https://www.betano.de", Winamax: "https://www.winamax.de",
    bet365: "https://www.bet365.de", Interwetten: "https://www.interwetten.de", "bet-at-home": "https://www.bet-at-home.de"
  };

  // ---------------------------------------------------------------- Formate (deutsch)
  var nf0 = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 0 });
  var nf1 = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  var nf2 = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  var eurF = new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
  var eurF2 = new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR", minimumFractionDigits: 2 });
  var WD = ["Sonntag", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"];
  var MO = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"];

  function pct(p, d) { return p == null ? "–" : (d ? nf1 : nf0).format(p * 100) + " %"; }
  function signed(x) { return x == null ? "–" : (x >= 0 ? "+" : "−") + nf1.format(Math.abs(x * 100)) + " %"; }
  function odds(x) { return x ? nf2.format(x) : "–"; }
  function fair(p) { return p ? nf2.format(1 / p) : "–"; }
  function eur(x, cents) { return x == null ? "–" : (cents ? eurF2 : eurF).format(x); }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function date(s) { return new Date(s); }
  function time(s) { var d = date(s); return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0"); }
  function dayKey(s) { var d = date(s); return d.getFullYear() + "-" + d.getMonth() + "-" + d.getDate(); }
  function dayLabel(s) {
    var d = date(s), now = new Date(), t = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    var diff = Math.round((new Date(d.getFullYear(), d.getMonth(), d.getDate()) - t) / 86400000);
    if (diff === 0) return "Heute";
    if (diff === 1) return "Morgen";
    return WD[d.getDay()] + ", " + d.getDate() + ". " + MO[d.getMonth()];
  }
  function longDate(s) { var d = date(s); return dayLabel(s) + ", " + time(s) + " Uhr"; }
  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  var CHEV = '<svg class="chev" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>';
  var BACK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg>';

  function save() { try { localStorage.setItem("edgelab-settings", JSON.stringify({ bank: state.bank, frac: state.frac })); } catch (e) {} }
  function loadBets() { try { return JSON.parse(localStorage.getItem("edgelab-bets") || "[]"); } catch (e) { return []; } }
  function saveBets(b) { try { localStorage.setItem("edgelab-bets", JSON.stringify(b)); } catch (e) {} }

  // ---------------------------------------------------------------- Daten-Helfer
  function probs(g) { return g.model || g.books || null; }
  function bestValue(g) {
    var best = null;
    (g.odds || []).forEach(function (r) { if (r.value != null && !r.unsure && r.value > 0.02 && (!best || r.value > best.value)) best = r; });
    return best;
  }
  function whalesFor(g) { return D.whales.filter(function (w) { return w.key === g.key && !w.live; }); }
  function kellyStake(p, o) {
    var b = o - 1; if (b <= 0) return 0;
    var k = Math.max(0, (p * b - (1 - p)) / b);
    return Math.min(state.bank * k * state.frac, state.bank * 0.03);
  }

  function scale(pr, books, big) {
    if (!pr) return "";
    var a = pr.a || 0, x = pr.draw || 0, b = pr.b || 0, t = a + x + b || 1;
    var notch = books && books.a != null ? '<span class="notch" style="left:' + (books.a / ((books.a || 0) + (books.draw || 0) + (books.b || 0) || 1) * 100) + '%" title="Buchmacher"></span>' : "";
    return '<div class="scale' + (big ? " big" : "") + '" aria-hidden="true"><i class="sa" style="flex:' + a / t + '"></i>' +
      (x ? '<i class="sd" style="flex:' + x / t + '"></i>' : "") + '<i class="sb" style="flex:' + b / t + '"></i>' + notch + "</div>";
  }

  // ---------------------------------------------------------------- Ansicht: Spiele
  function viewGames() {
    var sports = {};
    D.games.forEach(function (g) { sports[g.sport] = g.sportName; });
    var keys = Object.keys(sports).sort(function (a, b) { return count(b) - count(a); });
    function count(s) { return D.games.filter(function (g) { return g.sport === s; }).length; }
    var q = state.q.trim().toLowerCase();
    var list = D.games.filter(function (g) {
      return (state.sport === "all" || g.sport === state.sport) && (!q || (g.a + " " + g.b + " " + g.league).toLowerCase().indexOf(q) >= 0);
    });

    var html = '<h1>Spiele</h1><p class="lead">Wer gewinnt mit welcher Wahrscheinlichkeit. Die Linie unter jedem Spiel teilt sich nach der Siegchance, die grüne Kerbe zeigt, wo die Buchmacher stehen.</p>' +
      '<div class="filters"><div class="sports" role="group" aria-label="Sportart">' +
      '<button class="chip" data-sport="all" aria-pressed="' + (state.sport === "all") + '">Alle</button>' +
      keys.map(function (k) { return '<button class="chip" data-sport="' + esc(k) + '" aria-pressed="' + (state.sport === k) + '">' + esc(sports[k]) + "</button>"; }).join("") +
      '</div><input class="search" id="q" type="search" placeholder="Team suchen" aria-label="Team suchen" value="' + esc(state.q) + '"></div>';

    if (!list.length) {
      html += '<div class="empty"><p>Keine Spiele gefunden.</p><button class="btn" id="reset">Filter zurücksetzen</button></div>';
    } else {
      var groups = [], cur = null;
      list.forEach(function (g) {
        var k = dayKey(g.start);
        if (!cur || cur.k !== k) { cur = { k: k, label: dayLabel(g.start), items: [] }; groups.push(cur); }
        cur.items.push(g);
      });
      groups.forEach(function (grp) {
        html += '<h2 class="day">' + esc(grp.label) + '</h2><div class="list">' + grp.items.map(gameRow).join("") + "</div>";
      });
    }
    $("#view").innerHTML = html;

    $$("[data-sport]").forEach(function (b) { b.addEventListener("click", function () { state.sport = b.getAttribute("data-sport"); viewGames(); }); });
    var inp = $("#q");
    inp.addEventListener("input", function () {
      state.q = inp.value; var pos = inp.selectionStart; viewGames();
      var n = $("#q"); n.focus(); try { n.setSelectionRange(pos, pos); } catch (e) {}
    });
    var r = $("#reset"); if (r) r.addEventListener("click", function () { state.sport = "all"; state.q = ""; viewGames(); });
  }

  function gameRow(g) {
    var pr = probs(g) || {};
    var favA = (pr.a || 0) >= (pr.b || 0);
    var v = bestValue(g), w = whalesFor(g).length;
    var marks = [];
    if (v) marks.push('<span class="mark-value">Value bei ' + esc(v.best) + "</span>");
    if (g.injuries) marks.push('<span class="mark-warn">Ausfälle</span>');
    if (w) marks.push('<span class="mark-quiet">' + w + (w === 1 ? " Großwette" : " Großwetten") + "</span>");
    if (!g.model) marks.push('<span class="mark-quiet">nur Buchmacher</span>');
    return '<a class="game" href="#/spiel/' + encodeURIComponent(g.id) + '">' +
      '<div class="when"><div class="time">' + time(g.start) + '</div><div class="league">' + esc(g.league) + "</div></div>" +
      '<div class="teams">' +
      '<div class="team' + (favA ? " fav" : "") + '"><span>' + esc(g.a) + '</span><span class="pct">' + pct(pr.a) + "</span></div>" +
      '<div class="team' + (!favA ? " fav" : "") + '"><span>' + esc(g.b) + '</span><span class="pct">' + pct(pr.b) + "</span></div>" +
      (pr.draw != null ? '<div class="team minor"><span>Unentschieden</span><span class="pct">' + pct(pr.draw) + "</span></div>" : "") +
      scale(pr, g.model ? g.books : null, false) +
      (marks.length ? '<div class="marks">' + marks.join("") + "</div>" : "") +
      "</div>" + CHEV + "</a>";
  }

  // ---------------------------------------------------------------- Ansicht: Spiel
  function viewGame(id) {
    var g = D.games.filter(function (x) { return x.id === id; })[0];
    if (!g) { $("#view").innerHTML = '<a class="back" href="#/spiele">' + BACK + 'Alle Spiele</a><div class="empty"><p>Dieses Spiel ist nicht mehr in der Liste. Es hat vermutlich schon begonnen.</p></div>'; return; }
    var pr = probs(g) || {}, bk = g.books;
    var favA = (pr.a || 0) >= (pr.b || 0), fav = favA ? g.a : g.b, fp = favA ? pr.a : pr.b;
    var verdict = '<p class="verdict">' + (g.model ? "Laut Modell" : "Laut Buchmachern") + " gewinnt <strong>" + esc(fav) + "</strong> mit " + pct(fp) + ".";
    if (g.model && bk) { var bf = favA ? bk.a : bk.b; verdict += " Die Buchmacher sehen " + pct(bf) + (Math.abs(bf - fp) < 0.03 ? ", also fast dasselbe." : "."); }
    if (g.expected) verdict += " Erwartet: " + esc(g.expected) + ".";
    verdict += "</p>";

    var h = '<a class="back" href="#/spiele">' + BACK + "Alle Spiele</a>" +
      '<section class="hero"><div class="meta">' + esc(g.sportName) + ", " + esc(g.league) + ", " + longDate(g.start) + "</div>" +
      '<div class="duel"><div class="side' + (favA ? "" : " dim") + '"><div class="name">' + esc(g.a) + '</div><div class="big">' + pct(pr.a) + "</div></div>" +
      '<div class="mid">' + (pr.draw != null ? "Remis<b>" + pct(pr.draw) + "</b>" : "") + "</div>" +
      '<div class="side right' + (favA ? " dim" : "") + '"><div class="name">' + esc(g.b) + '</div><div class="big">' + pct(pr.b) + "</div></div></div>" +
      scale(pr, g.model ? bk : null, true) +
      (g.model && bk ? '<div class="legend"><span><span class="k" style="background:var(--a)"></span>Modell</span><span><span class="k" style="background:var(--value)"></span>Buchmacher</span></div>' : "") +
      verdict + (g.injuries ? '<div class="note"><b>Ausfälle eingerechnet.</b> ' + esc(g.injuries) + "</div>" : "") + "</section>";

    // Quoten
    h += '<h2 class="section">Quoten</h2>';
    if (g.odds && g.odds.length) {
      var books = g.bookList || [];
      h += '<div class="panel table-wrap"><table><thead><tr><th>Tipp</th><th>Fair</th>' + books.map(function (b) { return "<th>" + esc(b) + "</th>"; }).join("") + "<th>Value</th></tr></thead><tbody>" +
        g.odds.map(function (r) {
          var mx = Math.max.apply(null, books.map(function (b) { return r[b] || 0; }));
          return "<tr><td>" + esc(r.tipp) + "</td><td>" + fair(r.p) + "</td>" +
            books.map(function (b) { return r[b] ? "<td" + (r[b] === mx ? ' class="best"' : "") + ">" + odds(r[b]) + "</td>" : '<td class="na">–</td>'; }).join("") +
            (r.unsure ? '<td class="na">unsicher</td>' : r.value != null && r.value > 0 ? '<td class="pos">' + signed(r.value) + "</td>" : '<td class="na">' + (r.value != null ? signed(r.value) : "–") + "</td>") + "</tr>";
        }).join("") + "</tbody></table></div>" +
        '<p class="foot-note">Fair ist die Quote, die das Modell für richtig hält. Liegt ein Anbieter darüber, steht der Vorteil rechts. „Unsicher“ heißt: Modell und Anbieter liegen so weit auseinander, dass dem Modell vermutlich eine Information fehlt.</p>' +
        '<div class="links">' + books.filter(function (b) { return BOOK_URL[b]; }).map(function (b) { return '<a class="btn" href="' + BOOK_URL[b] + '" target="_blank" rel="noopener">' + esc(b) + " öffnen</a>"; }).join("") + "</div>";
    } else {
      h += '<p class="prose">Für dieses Spiel liegen noch keine Quoten von Tipico, Betano oder Winamax vor.</p>' +
        '<div class="links"><a class="btn" href="' + esc(g.link) + '" target="_blank" rel="noopener">Quoten bei OddsPortal vergleichen</a></div>';
    }

    // Darts-Statistik
    if (g.darts) {
      var da = g.darts.a, db = g.darts.b;
      var rows = [["3-Dart-Average", da.avg, db.avg, nf2], ["First 9 Average", da.first9, db.first9, nf2], ["180er", da.s180, db.s180, nf0],
        ["Checkout-Quote", da.checkout, db.checkout, "pct"], ["Gewonnene Legs", da.legs_won, db.legs_won, "pct"], ["Höchstes Finish", da.high_co, db.high_co, nf0]];
      h += '<h2 class="section">Darts-Statistik</h2><div class="panel table-wrap"><table><thead><tr><th>Letzte 12 Monate</th><th>' + esc(g.a) + "</th><th>" + esc(g.b) + "</th></tr></thead><tbody>" +
        rows.map(function (r) {
          var f = function (x) { return x == null ? "–" : r[3] === "pct" ? nf1.format(x) + " %" : r[3].format(x); };
          return "<tr><td>" + r[0] + "</td><td" + (r[1] > r[2] ? ' class="best"' : "") + ">" + f(r[1]) + "</td><td" + (r[2] > r[1] ? ' class="best"' : "") + ">" + f(r[2]) + "</td></tr>";
        }).join("") + "</tbody></table></div>" +
        '<p class="foot-note">Quelle: Darts Orakel.' + (g.darts.format ? " Gespielt wird " + esc(g.darts.format) + "." : "") + " Aus dem Average-Unterschied ergibt sich die Chance pro Leg und daraus alle Wetten unten.</p>";
    }

    // Großwetten auf dieses Spiel
    var ws = whalesFor(g);
    if (ws.length) h += '<h2 class="section">Großwetten</h2>' + whaleGroup(ws, null);

    // Alle Wetten
    if (g.groups && g.groups.length) {
      h += '<h2 class="section">Alle Wetten mit fairer Quote</h2><div class="panel">' + g.groups.map(function (gr, i) {
        var top = gr.items.reduce(function (m, x) { return x.p > m.p ? x : m; }, gr.items[0]);
        return '<details class="market"' + (i === 0 ? " open" : "") + "><summary><span>" + esc(gr.group) + '</span><span class="hint">' + esc(top.label) + " " + pct(top.p) + "</span></summary>" +
          '<div class="picks">' + gr.items.map(function (x) {
            return '<div class="pick"><span class="lbl">' + esc(x.label) + '</span><span class="num"><span class="p">' + pct(x.p) + '</span><span class="q">' + odds(x.odds) + "</span></span></div>";
          }).join("") + "</div></details>";
      }).join("") + "</div>";
    }

    // Spielerwetten: eine Zeile pro Spieler, Punkte / Rebounds / Assists nebeneinander
    if (g.props && g.props.length) {
      var cols = [["pts", "Punkte"], ["reb", "Rebounds"], ["ast", "Assists"]];
      var propRow = function (p) {
        var by = {}; p.stats.forEach(function (s) { by[s.stat] = s; });
        return "<tr><td>" + esc(p.player) + '<div class="muted small">' + esc(p.team) + (p.status ? ", " + esc(p.status) : "") + "</div></td>" +
          cols.map(function (c) {
            var s = by[c[0]];
            return s ? '<td><span class="prop-line">über ' + nf1.format(s.line) + '</span><span class="prop-p">' + pct(s.p_over) + '</span><span class="prop-q">' + fair(s.p_over) + "</span></td>" : '<td class="na">–</td>';
          }).join("") + "</tr>";
      };
      var head = "<thead><tr><th>Spieler</th>" + cols.map(function (c) { return "<th>" + c[1] + "</th>"; }).join("") + "</tr></thead>";
      var top = g.props.slice(0, 10), rest = g.props.slice(10);
      h += '<h2 class="section">Spielerwetten</h2><div class="panel table-wrap"><table class="props">' + head + "<tbody>" + top.map(propRow).join("") + "</tbody></table></div>";
      if (rest.length) {
        h += '<details class="more"><summary>' + rest.length + " weitere Spieler</summary>" +
          '<div class="panel table-wrap"><table class="props">' + head + "<tbody>" + rest.map(propRow).join("") + "</tbody></table></div></details>";
      }
      h += '<p class="foot-note">Linie ist der Saisonschnitt mit „,5“. Darunter die Chance auf „über“ und die faire Quote. Berechnet aus den letzten 20 Spielen mit der echten Schwankung des Spielers.</p>';
    }
    $("#view").innerHTML = h;
    window.scrollTo(0, 0);
  }

  // ---------------------------------------------------------------- Ansicht: Value
  function valueList() {
    var out = [];
    D.games.forEach(function (g) {
      (g.odds || []).forEach(function (r) {
        if (r.value != null && !r.unsure && r.value > 0.02 && r.p) out.push({ g: g, r: r, o: r[r.best] });
      });
    });
    return out.sort(function (a, b) { return b.r.value - a.r.value; });
  }
  function viewValue() {
    var list = valueList();
    var h = '<h1>Value</h1><p class="lead">Wetten, bei denen ein deutscher Anbieter eine höhere Quote zahlt, als das Modell für fair hält. Der Einsatz ist ein Vorschlag nach dem Kelly-Prinzip, höchstens 3 % deiner Bankroll.</p>' +
      '<div class="settings"><label class="field" for="bank">Bankroll<input id="bank" type="number" inputmode="numeric" min="0" step="50" value="' + state.bank + '"></label>' +
      '<label class="field" for="frac">Einsatz<select id="frac"><option value="0.25"' + (state.frac == 0.25 ? " selected" : "") + '>Vorsichtig (¼ Kelly)</option><option value="0.5"' + (state.frac == 0.5 ? " selected" : "") + ">Mittel (½ Kelly)</option></select></label></div>";
    if (!list.length) {
      h += '<div class="empty"><p>Gerade zahlt kein deutscher Anbieter mehr als fair.</p><p class="small">Die Quoten werden stündlich neu geladen.</p></div>';
    } else {
      h += '<div class="panel table-wrap"><table><thead><tr><th>Spiel</th><th>Tipp</th><th>Fair</th><th>Beste Quote</th><th>Vorteil</th><th>Einsatz</th><th></th></tr></thead><tbody>' +
        list.map(function (x, i) {
          return '<tr><td><a href="#/spiel/' + encodeURIComponent(x.g.id) + '">' + esc(x.g.a) + " – " + esc(x.g.b) + '</a><div class="muted small">' + esc(dayLabel(x.g.start)) + ", " + time(x.g.start) + "</div></td>" +
            '<td style="text-align:left">' + esc(x.r.tipp) + "</td><td>" + fair(x.r.p) + "</td><td>" + odds(x.o) + ' <span class="muted small">' + esc(x.r.best) + '</span></td><td class="pos">' + signed(x.r.value) + "</td><td>" + eur(kellyStake(x.r.p, x.o), true) + "</td>" +
            '<td><button class="text-btn" data-keep="' + i + '">Merken</button></td></tr>';
        }).join("") + "</tbody></table></div>";
    }
    h += '<p class="foot-note">Ein Vorteil heißt nur, dass Modell und Anbieter sich nicht einig sind. In vielen Sportarten liegen die Buchmacher öfter richtig. Unter Info steht, wo das Modell mithalten kann.</p>';
    $("#view").innerHTML = h;
    $("#bank").addEventListener("change", function (e) { state.bank = Math.max(0, +e.target.value || 0); save(); viewValue(); });
    $("#frac").addEventListener("change", function (e) { state.frac = +e.target.value; save(); viewValue(); });
    $$("[data-keep]").forEach(function (b) {
      b.addEventListener("click", function () {
        var x = list[+b.getAttribute("data-keep")];
        var bets = loadBets();
        bets.unshift({ id: Date.now(), match: x.g.a + " – " + x.g.b, tip: x.r.tipp, book: x.r.best, odds: x.o, stake: Math.max(1, Math.round(kellyStake(x.r.p, x.o) * 100) / 100), start: x.g.start, status: "open" });
        saveBets(bets);
        b.textContent = "Gemerkt"; b.disabled = true;
      });
    });
  }

  // ---------------------------------------------------------------- Ansicht: Großwetten
  function whaleGroup(list, title) {
    // Geldfluss nur für Siegwetten (Tipp = einer der beiden Namen), damit nichts vermischt wird
    var sides = (list[0].match || "").split(" – ");
    var by = {}; list.forEach(function (w) { if (sides.indexOf(w.pick) >= 0) by[w.pick] = (by[w.pick] || 0) + w.eur; });
    var picks = Object.keys(by).sort(function (a, b) { return by[b] - by[a]; });
    var total = picks.reduce(function (s, k) { return s + by[k]; }, 0);
    var shades = ["var(--a)", "var(--b)"];
    var flow = picks.length ? '<div class="flow" aria-hidden="true">' + picks.map(function (k, i) { return '<i style="flex:' + by[k] / total + ";background:" + shades[Math.min(i, 1)] + '"></i>'; }).join("") + "</div>" +
      '<div class="flow-legend">' + picks.map(function (k) { return "<span>Sieg " + esc(k) + ": " + eur(by[k]) + "</span>"; }).join("") + "</div>" : "";
    return '<div class="whale-group">' + (title || "") + flow +
      '<div style="margin-top:10px">' + list.slice(0, 8).map(function (w) {
        var cls = w.wallet === "Profi" ? "pro" : w.wallet === "Verlierer" ? "lose" : "";
        var agree = w.model != null ? (w.model > w.price ? "Modell dafür" : "Modell dagegen") : "";
        return '<div class="bet"><span class="amt">' + eur(w.eur) + "</span><span>" + esc(w.pick) + ' <span class="muted small">Quote ' + odds(w.odds) + "</span>" + (agree ? '<div class="agree">' + agree + "</div>" : "") + "</span>" +
          '<span class="who ' + cls + '">' + esc(w.wallet) + "</span></div>";
      }).join("") + "</div>" + (list.length > 8 ? '<p class="foot-note">und ' + (list.length - 8) + " weitere</p>" : "") + "</div>";
  }
  function viewWhales() {
    var list = D.whales.filter(function (w) { return !w.live; });
    var groups = {}; list.forEach(function (w) { var k = w.key || w.match; (groups[k] = groups[k] || []).push(w); });
    var keys = Object.keys(groups).sort(function (a, b) { return sum(groups[b]) - sum(groups[a]); });
    function sum(l) { return l.reduce(function (s, w) { return s + w.eur; }, 0); }
    var gameBy = {}; D.games.forEach(function (g) { gameBy[g.key] = g; });
    var h = '<h1>Großwetten</h1><p class="lead">Einsätze ab rund 1.800 € auf dem Prognosemarkt Polymarket in den letzten 24 Stunden, vor Spielbeginn. „Profi“ heißt: Das Konto liegt in Sportwetten deutlich im Plus. Beträge in Euro umgerechnet.</p>';
    if (!keys.length) h += '<div class="empty"><p>In den letzten 24 Stunden gab es keine Großwetten vor Spielbeginn.</p></div>';
    keys.forEach(function (k) {
      var l = groups[k], g = gameBy[k], pros = l.filter(function (w) { return w.wallet === "Profi"; }).length;
      var title = "<h3>" + (g ? '<a href="#/spiel/' + encodeURIComponent(g.id) + '">' + esc(l[0].match) + "</a>" : esc(l[0].match)) + "</h3>" +
        '<div class="sub">' + esc(l[0].sportName) + ", " + eur(sum(l)) + " in " + l.length + (l.length === 1 ? " Wette" : " Wetten") + (pros ? ", davon " + pros + (pros === 1 ? " Profi" : " Profis") : "") + "</div>";
      h += whaleGroup(l, title);
    });
    $("#view").innerHTML = h;
  }

  // ---------------------------------------------------------------- Ansicht: Meine Wetten
  function viewBets() {
    var bets = loadBets(), done = bets.filter(function (b) { return b.status !== "open"; });
    var staked = done.reduce(function (s, b) { return s + b.stake; }, 0);
    var profit = done.reduce(function (s, b) { return s + (b.status === "won" ? b.stake * (b.odds - 1) : -b.stake); }, 0);
    var won = done.filter(function (b) { return b.status === "won"; }).length;
    var h = '<h1>Meine Wetten</h1><p class="lead">Tipps, die du unter Value gemerkt hast. Gespeichert nur auf diesem Gerät.</p>' +
      '<div class="stats"><div class="stat"><div class="v ' + (profit >= 0 ? "pos" : "neg") + '">' + eur(profit, true) + '</div><div class="l">Gewinn</div></div>' +
      '<div class="stat"><div class="v">' + (staked ? signed(profit / staked) : "–") + '</div><div class="l">Rendite</div></div>' +
      '<div class="stat"><div class="v">' + (done.length ? pct(won / done.length) : "–") + '</div><div class="l">Trefferquote</div></div>' +
      '<div class="stat"><div class="v">' + (bets.length - done.length) + '</div><div class="l">Offen</div></div></div>';
    if (!bets.length) {
      h += '<div class="empty"><p>Noch keine Wetten gemerkt.</p><a class="btn" href="#/value">Value ansehen</a></div>';
    } else {
      h += '<div class="panel table-wrap"><table><thead><tr><th>Wette</th><th>Quote</th><th>Einsatz</th><th>Status</th><th></th></tr></thead><tbody>' +
        bets.map(function (b) {
          var st = { open: "offen", won: "gewonnen", lost: "verloren" }[b.status];
          return "<tr><td>" + esc(b.match) + '<div class="muted small">' + esc(b.tip) + " bei " + esc(b.book) + ", " + esc(dayLabel(b.start)) + "</div></td><td>" + odds(b.odds) + "</td><td>" + eur(b.stake, true) + '</td><td><span class="status ' + b.status + '">' + st + "</span></td>" +
            '<td><div class="row-actions">' + (b.status === "open" ? '<button class="text-btn" data-set="won" data-id="' + b.id + '">Gewonnen</button><button class="text-btn" data-set="lost" data-id="' + b.id + '">Verloren</button>' : "") +
            '<button class="text-btn" data-set="del" data-id="' + b.id + '">Entfernen</button></div></td></tr>';
        }).join("") + "</tbody></table></div>";
    }
    $("#view").innerHTML = h;
    $$("[data-set]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var id = +btn.getAttribute("data-id"), what = btn.getAttribute("data-set"), list = loadBets();
        list = what === "del" ? list.filter(function (b) { return b.id !== id; }) : list.map(function (b) { if (b.id === id) b.status = what; return b; });
        saveBets(list); viewBets();
      });
    });
  }

  // ---------------------------------------------------------------- Ansicht: Info
  function viewInfo() {
    var h = '<h1>Info</h1><p class="lead">Woher die Zahlen kommen und wie verlässlich sie sind.</p>';
    if (D.backtest && D.backtest.length) {
      h += '<h2 class="section">Wie gut ist das Modell?</h2><p class="prose">Für vergangene Spiele wurde die Prognose ohne Kenntnis des Ergebnisses berechnet und mit dem Marktpreis beim Anpfiff verglichen. Kleinere Werte sind besser, 0,693 entspricht einem Münzwurf.</p>' +
        '<div class="panel table-wrap"><table><thead><tr><th>Sportart</th><th>Spiele</th><th>Modell</th><th>Markt</th><th>Ergebnis</th></tr></thead><tbody>' +
        D.backtest.map(function (b) {
          var res = b.market > 0.69 ? "Marktdaten unzuverlässig" : b.model < b.market ? "Modell besser" : b.model - b.market < 0.012 ? "etwa gleich" : "Markt besser";
          return "<tr><td>" + esc(b.sportName) + "</td><td>" + nf0.format(b.games) + "</td><td>" + nf2.format(b.model) + "</td><td>" + nf2.format(b.market) + "</td><td" + (res === "Modell besser" ? ' class="pos"' : "") + ">" + res + "</td></tr>";
        }).join("") + "</tbody></table></div>";
    }
    if (D.injuries && D.injuries.length) {
      h += '<h2 class="section">Ausfälle, die eingerechnet sind</h2><div class="panel table-wrap"><table><thead><tr><th>Spieler</th><th>Status</th><th>Punkte</th></tr></thead><tbody>' +
        D.injuries.slice(0, 25).map(function (i) {
          return "<tr><td>" + esc(i.player) + '<div class="muted small">' + esc(i.team) + ", " + esc(i.league) + "</div></td><td>" + esc(i.status) + "</td><td>−" + nf1.format(i.points) + "</td></tr>";
        }).join("") + "</tbody></table></div>";
    }
    h += '<h2 class="section">Quellen</h2><div class="prose">' +
      "<p>Ergebnisse, Spielpläne und Großwetten stammen vom Prognosemarkt Polymarket, Verletzungen und NBA-Spielerdaten von ESPN, Darts-Statistiken von Darts Orakel, Quoten deutscher Anbieter von The Odds API. Dollarbeträge sind mit dem Referenzkurs der Europäischen Zentralbank in Euro umgerechnet" + (D.eurUsd ? " (1 € = " + nf2.format(D.eurUsd) + " $)" : "") + ".</p>" +
      "<p>Die Daten werden jede Stunde automatisch neu geladen.</p></div>" +
      '<p class="fine">Nur zur Information, keine Wettberatung. Wetten kann süchtig machen. Hilfe gibt es kostenlos unter 0800 1 37 27 00 und auf check-dein-spiel.de.</p>';
    $("#view").innerHTML = h;
  }

  // ---------------------------------------------------------------- Navigation
  function route() {
    var hash = location.hash.replace(/^#\/?/, "") || "spiele";
    var parts = hash.split("/"), page = parts[0];
    var active = page === "spiel" ? "spiele" : page;
    var nav = NAV.map(function (n) {
      return '<a href="#/' + n.id + '"' + (n.id === active ? ' aria-current="page"' : "") + "><svg viewBox=\"0 0 24 24\" aria-hidden=\"true\">" + n.icon + "</svg><span>" + n.label + "</span></a>";
    }).join("");
    $(".nav-bottom").innerHTML = nav;
    $(".nav-top").innerHTML = NAV.map(function (n) { return '<a href="#/' + n.id + '"' + (n.id === active ? ' aria-current="page"' : "") + ">" + n.label + "</a>"; }).join("");
    if (page === "spiel") viewGame(decodeURIComponent(parts.slice(1).join("/")));
    else if (page === "value") viewValue();
    else if (page === "grosswetten") viewWhales();
    else if (page === "wetten") viewBets();
    else if (page === "info") viewInfo();
    else viewGames();
    if (page !== "spiel") window.scrollTo(0, 0);
  }

  function stamp() {
    if (!D || !D.updated) return;
    $("#stamp").textContent = "Stand " + time(D.updated) + " Uhr";
  }

  // Nur kommende Spiele anzeigen, auch wenn die Datei ein paar Stunden alt ist
  function dropPast() {
    var now = Date.now();
    D.games = D.games.filter(function (g) { return date(g.start).getTime() > now - 5 * 60000; });
  }

  fetch("data.json?t=" + Math.floor(Date.now() / 300000), { cache: "no-store" })
    .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
    .then(function (data) {
      D = data; D.games = D.games || []; D.whales = D.whales || [];
      dropPast(); stamp();
      window.addEventListener("hashchange", route);
      route();
    })
    .catch(function () {
      $("#view").innerHTML = '<div class="empty"><p>Die Daten konnten nicht geladen werden.</p><button class="btn" onclick="location.reload()">Neu laden</button></div>';
    });
})();
