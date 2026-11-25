/* Quotenradar Web-App. Liest data.json (stündlich von GitHub Actions erzeugt) und zeigt sie an. */
(function () {
  "use strict";

  var D = null;
  var state = { day: null, sport: "all", q: "", bank: 1000, frac: 0.25 };
  try {
    var saved = JSON.parse(localStorage.getItem("edgelab-settings") || "{}");
    if (saved.bank) state.bank = saved.bank;
    if (saved.frac) state.frac = saved.frac;
  } catch (e) { /* ohne Speicher weiter */ }

  var NAV = [
    { id: "spiele", label: "Spiele", icon: '<path d="M4 6h16M4 12h16M4 18h10"/>' },
    { id: "value", label: "Value", icon: '<path d="M4 17l5-5 4 4 7-8"/><path d="M15 8h5v5"/>' },
    { id: "spieler", label: "Spieler", icon: '<circle cx="12" cy="8" r="3.5"/><path d="M5 20c.8-3.6 3.6-5.5 7-5.5s6.2 1.9 7 5.5"/>' },
    { id: "grosswetten", label: "Großwetten", icon: '<circle cx="12" cy="12" r="8"/><path d="M12 8v8M9.5 10.5c0-1.4 1.1-2 2.5-2s2.5.6 2.5 2-1.1 1.8-2.5 2-2.5.7-2.5 2 1.1 2 2.5 2 2.5-.6 2.5-2"/>' },
    { id: "wetten", label: "Wetten", icon: '<rect x="5" y="4" width="14" height="16" rx="2"/><path d="M9 9h6M9 13h6M9 17h3"/>' },
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

  function pct(p, d) { return p == null ? "–" : (d ? nf1 : nf0).format(p * 100) + "\u00a0%"; }
  function signed(x) { return x == null ? "–" : (x >= 0 ? "+" : "−") + nf1.format(Math.abs(x * 100)) + "\u00a0%"; }
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

  // ---------------------------------------------------------------- Wichtigkeit & Gruppierung
  function tierOf(g) { return g.tier || 4; }
  function byImportance(a, b) {
    return tierOf(a) - tierOf(b) || (a.rank || 99) - (b.rank || 99) || (b.odds && b.odds.length ? 1 : 0) - (a.odds && a.odds.length ? 1 : 0) || date(a.start) - date(b.start);
  }
  function days() {
    var seen = {}, out = [];
    D.games.forEach(function (g) {
      var k = dayKey(g.start);
      if (!seen[k]) { seen[k] = { k: k, label: dayLabel(g.start), d: date(g.start), n: 0 }; out.push(seen[k]); }
      seen[k].n++;
    });
    return out.sort(function (a, b) { return a.d - b.d; });
  }
  function favOdds(g) {
    // Beste Anbieterquote auf den Favoriten (für die Liste)
    var pr = probs(g) || {}, fav = (pr.a || 0) >= (pr.b || 0) ? g.a : g.b, best = null;
    (g.odds || []).forEach(function (r) {
      if (r.tipp === fav || r.tipp === "Sieg " + fav) {
        (g.bookList || []).forEach(function (b) { if (r[b] && (!best || r[b] > best.o)) best = { o: r[b], book: b, name: fav }; });
      }
    });
    return best;
  }

  // ---------------------------------------------------------------- Ansicht: Spiele
  function viewGames() {
    var ds = days();
    if (!state.day || !ds.some(function (d) { return d.k === state.day; })) state.day = ds.length ? ds[0].k : null;
    var dayGames = D.games.filter(function (g) { return dayKey(g.start) === state.day; });
    var sports = {};
    dayGames.forEach(function (g) { sports[g.sport] = sports[g.sport] || { name: g.sportName, n: 0, t: 9 }; sports[g.sport].n++; sports[g.sport].t = Math.min(sports[g.sport].t, tierOf(g)); });
    var keys = Object.keys(sports).sort(function (a, b) { return sports[a].t - sports[b].t || sports[b].n - sports[a].n; });
    if (state.sport !== "all" && !sports[state.sport]) state.sport = "all";
    var q = state.q.trim().toLowerCase();
    var list = (q ? D.games : dayGames).filter(function (g) {
      return (state.sport === "all" || g.sport === state.sport) && (!q || (g.a + " " + g.b + " " + g.league).toLowerCase().indexOf(q) >= 0);
    }).sort(byImportance);

    var h = '<div class="days" role="tablist" aria-label="Tag">' + ds.map(function (d) {
      return '<button class="day-tab" role="tab" data-day="' + d.k + '" aria-selected="' + (d.k === state.day && !q) + '"><b>' + esc(d.label.split(",")[0]) + "</b><span>" +
        (d.label.indexOf(",") > 0 ? esc(d.label.split(", ")[1]) + ", " : "") + d.n + " Spiele</span></button>";
    }).join("") + "</div>" +
      '<div class="filters"><div class="sports" role="group" aria-label="Sportart">' +
      '<button class="chip" data-sport="all" aria-pressed="' + (state.sport === "all") + '">Alle</button>' +
      keys.map(function (k) { return '<button class="chip" data-sport="' + esc(k) + '" aria-pressed="' + (state.sport === k) + '">' + esc(sports[k].name) + "</button>"; }).join("") +
      '</div><input class="search" id="q" type="search" placeholder="Team oder Liga suchen" aria-label="Team oder Liga suchen" value="' + esc(state.q) + '"></div>';

    if (!list.length) {
      h += '<div class="empty"><p>Keine Spiele gefunden.</p><button class="btn" id="reset">Filter zurücksetzen</button></div>';
    } else {
      // Topspiele: die wichtigsten Begegnungen des Tages als große Karten
      var top = q ? [] : list.filter(function (g) { return tierOf(g) === 1; }).slice(0, 6);
      if (!top.length && !q) top = list.filter(function (g) { return tierOf(g) === 2; }).slice(0, 4);
      if (top.length) h += '<h2 class="block-title">Topspiele</h2><div class="features">' + top.map(featureCard).join("") + "</div>";

      // Danach Liga für Liga, wichtigste zuerst
      var shown = {}; top.forEach(function (g) { shown[g.id] = 1; });
      var leagues = [], idx = {};
      list.filter(function (g) { return !shown[g.id]; }).forEach(function (g) {
        var k = g.sport + "|" + g.league;
        if (!idx[k]) { idx[k] = { name: g.league, sport: g.sportName, tier: tierOf(g), items: [] }; leagues.push(idx[k]); }
        idx[k].items.push(g);
      });
      leagues.forEach(function (l) { l.items.sort(function (a, b) { return date(a.start) - date(b.start); }); });
      var main = leagues.filter(function (l) { return l.tier < 4 || q; }), rest = leagues.filter(function (l) { return l.tier >= 4 && !q; });
      h += main.map(leagueBlock).join("");
      if (rest.length) {
        var n = rest.reduce(function (s, l) { return s + l.items.length; }, 0);
        h += '<details class="more-leagues"><summary><span>Weitere Ligen</span><span class="hint">' + rest.length + " Ligen, " + n + " Spiele</span></summary>" + rest.map(leagueBlock).join("") + "</details>";
      }
    }
    $("#view").innerHTML = h;

    $$("[data-day]").forEach(function (b) { b.addEventListener("click", function () { state.day = b.getAttribute("data-day"); state.q = ""; viewGames(); }); });
    $$("[data-sport]").forEach(function (b) { b.addEventListener("click", function () { state.sport = b.getAttribute("data-sport"); viewGames(); }); });
    var inp = $("#q");
    inp.addEventListener("input", function () {
      state.q = inp.value; var pos = inp.selectionStart; viewGames();
      var n = $("#q"); n.focus(); try { n.setSelectionRange(pos, pos); } catch (e) {}
    });
    var r = $("#reset"); if (r) r.addEventListener("click", function () { state.sport = "all"; state.q = ""; viewGames(); });
  }

  function leagueBlock(l) {
    return '<section class="league-block"><h2 class="league-head"><span>' + esc(l.name) + '</span><span class="hint">' + esc(l.sport) + ", " + l.items.length + (l.items.length === 1 ? " Spiel" : " Spiele") + "</span></h2>" +
      '<div class="list">' + l.items.map(gameRow).join("") + "</div></section>";
  }

  function featureCard(g) {
    var pr = probs(g) || {}, favA = (pr.a || 0) >= (pr.b || 0), v = bestValue(g), fo = favOdds(g);
    var foot = v ? '<span class="tag-value">Value ' + signed(v.value) + " bei " + esc(v.best) + "</span>"
      : fo ? '<span>Beste Quote ' + esc(fo.name) + " <b>" + odds(fo.o) + "</b> bei " + esc(fo.book) + "</span>"
      : '<span>Faire Quote ' + esc(favA ? g.a : g.b) + " <b>" + fair(favA ? pr.a : pr.b) + "</b></span>";
    return '<a class="feature" href="#/spiel/' + encodeURIComponent(g.id) + '">' +
      '<div class="f-meta"><span>' + esc(g.league) + "</span><span>" + time(g.start) + " Uhr</span></div>" +
      '<div class="f-team' + (favA ? " fav" : "") + '"><span class="n">' + esc(g.a) + '</span><span class="p">' + pct(pr.a) + "</span></div>" +
      '<div class="f-team' + (!favA ? " fav" : "") + '"><span class="n">' + esc(g.b) + '</span><span class="p">' + pct(pr.b) + "</span></div>" +
      (pr.draw != null ? '<div class="f-draw">Unentschieden ' + pct(pr.draw) + "</div>" : "") +
      scale(pr, g.model ? g.books : null, false) +
      '<div class="f-foot">' + foot + (g.injuries ? '<span class="tag-warn">Ausfälle</span>' : "") + "</div></a>";
  }

  function gameRow(g) {
    var pr = probs(g) || {};
    var favA = (pr.a || 0) >= (pr.b || 0);
    var v = bestValue(g), w = whalesFor(g).length, fo = favOdds(g);
    var marks = [];
    if (v) marks.push('<span class="tag-value">Value bei ' + esc(v.best) + "</span>");
    if (g.injuries) marks.push('<span class="tag-warn">Ausfälle</span>');
    if (w) marks.push('<span class="mark-quiet">' + w + (w === 1 ? " Großwette" : " Großwetten") + "</span>");
    if (!g.model) marks.push('<span class="mark-quiet">nur Buchmacher</span>');
    return '<a class="game" href="#/spiel/' + encodeURIComponent(g.id) + '">' +
      '<div class="when"><div class="time">' + time(g.start) + "</div>" + (state.q ? '<div class="league">' + esc(dayLabel(g.start)) + "</div>" : "") + "</div>" +
      '<div class="teams">' +
      '<div class="team' + (favA ? " fav" : "") + '"><span>' + esc(g.a) + '</span><span class="pct">' + pct(pr.a) + "</span></div>" +
      '<div class="team' + (!favA ? " fav" : "") + '"><span>' + esc(g.b) + '</span><span class="pct">' + pct(pr.b) + "</span></div>" +
      (pr.draw != null ? '<div class="team minor"><span>Unentschieden</span><span class="pct">' + pct(pr.draw) + "</span></div>" : "") +
      scale(pr, g.model ? g.books : null, false) +
      (marks.length ? '<div class="marks">' + marks.join("") + "</div>" : "") +
      "</div>" +
      '<div class="best-odds">' + (fo ? "<b>" + odds(fo.o) + "</b><span>" + esc(fo.book) + "</span>" : "<b>" + fair(favA ? pr.a : pr.b) + "</b><span>fair</span>") + "</div>" +
      CHEV + "</a>";
  }

  // ---------------------------------------------------------------- Die wichtigsten Wetten je Sportart
  var KEY_MARKETS = {
    soccer: [["Ergebnis (1X2)"], ["Über/Unter Tore", "2,5", "Über/Unter 2,5 Tore"], ["Beide treffen"], ["Doppelte Chance"]],
    basketball: [["Sieger"], ["Handicap (faire Linie)", null, "Handicap"], [/^Gesamtpunkte/, "mid", "Gesamtpunkte"]],
    "american-football": [["Sieger"], ["Handicap (faire Linie)", null, "Handicap"], [/^Gesamtpunkte/, "mid", "Gesamtpunkte"]],
    hockey: [["Sieger inkl. Verlängerung", null, "Sieger inkl. Verlängerung"], ["3-Weg nach 60 Minuten"], ["Über/Unter Tore", "5,5", "Über/Unter 5,5 Tore"], ["Puckline"], ["Sieger"]],
    tennis: [["Sieger"], ["Über/Unter Sätzen", null, "Über/Unter Sätze"], ["Ergebnis in Sätzen"]],
    darts: [["Sieger"], ["Satzergebnis"], [/^Gesamtzahl Sätze/, null, "Gesamtzahl Sätze"], [/^Gesamtzahl Legs/, "mid", "Gesamtzahl Legs"],
      ["Handicap Legs", "1,5", "Handicap 1,5 Legs"], ["Genaues Ergebnis (Top 6)", null, "Genaues Ergebnis"]]
  };
  function keyMarkets(g) {
    var G = g.groups || [], out = [];
    var spec = KEY_MARKETS[g.sport];
    if (!spec) return G.slice(0, 3).map(function (x) { return { title: x.group, items: x.items }; });
    spec.forEach(function (s) {
      if (out.length >= 4) return;
      var grp = G.filter(function (x) { return s[0] instanceof RegExp ? s[0].test(x.group) : x.group === s[0]; })[0];
      if (!grp) return;
      var items = grp.items;
      if (s[1] === "mid" || (s[1] && !items.some(function (i) { return i.label.slice(-s[1].length) === s[1]; }))) {
        var m = Math.floor(items.length / 4) * 2; items = items.slice(m, m + 2);
      } else if (s[1]) {
        items = items.filter(function (i) { return i.label.slice(-s[1].length) === s[1]; });
      }
      var title = s[2] || grp.group;
      if (s[1] === "mid" && items[0]) title = title + " " + items[0].label.replace(/^(Über|Unter) /, "").replace(/ (Legs|Sätze)$/, "");
      out.push({ title: title, items: items });
    });
    return out;
  }
  function bookFor(g, item) {
    var res = null;
    (g.odds || []).forEach(function (r) {
      if ((r.tipp === item.label || r.tipp === "Sieg " + item.label) && Math.abs((r.p || 0) - item.p) < 0.006) {
        (g.bookList || []).forEach(function (b) { if (r[b] && (!res || r[b] > res.o)) res = { o: r[b], book: b, value: r.unsure ? null : r.value }; });
      }
    });
    return res;
  }
  function marketCard(g, m) {
    var top = m.items.reduce(function (a, x) { return x.p > a.p ? x : a; }, m.items[0]);
    return '<div class="mcard"><h3>' + esc(m.title) + '</h3><div class="opts n' + Math.min(m.items.length, 4) + '">' + m.items.map(function (x) {
      var bk = bookFor(g, x), val = bk && bk.value != null && bk.value > 0.02;
      return '<div class="opt' + (x === top ? " lead-opt" : "") + (val ? " has-value" : "") + '"><div class="o-lbl">' + esc(x.label) + "</div>" +
        '<div class="o-p">' + pct(x.p) + "</div>" +
        '<div class="o-q"><span>fair <b>' + odds(x.odds) + "</b></span>" + (bk ? '<span class="o-bk">' + esc(bk.book) + " <b>" + odds(bk.o) + "</b></span>" : "") + "</div>" +
        (val ? '<div class="tag-value">' + signed(bk.value) + " Vorteil</div>" : "") + "</div>";
    }).join("") + "</div></div>";
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
      '<section class="hero"><div class="meta"><span>' + esc(g.league) + "</span><span>" + esc(longDate(g.start)) + "</span></div>" +
      '<div class="duel"><div class="side' + (favA ? "" : " dim") + '"><div class="name">' + esc(g.a) + '</div><div class="big">' + pct(pr.a) + "</div></div>" +
      '<div class="mid">' + (pr.draw != null ? "Remis<b>" + pct(pr.draw) + "</b>" : "") + "</div>" +
      '<div class="side right' + (favA ? " dim" : "") + '"><div class="name">' + esc(g.b) + '</div><div class="big">' + pct(pr.b) + "</div></div></div>" +
      scale(pr, g.model ? bk : null, true) +
      (g.model && bk ? '<div class="legend"><span><span class="k ka"></span>Modell</span><span><span class="k kb"></span>Buchmacher</span></div>' : "") +
      verdict + "</section>" +
      (g.injuries ? '<div class="note"><b>Ausfälle eingerechnet</b>' + esc(g.injuries) + "</div>" : "");

    // Die wichtigsten Wetten
    var km = keyMarkets(g);
    if (km.length) {
      h += '<h2 class="section">Die wichtigsten Wetten</h2><div class="mgrid">' + km.map(function (m) { return marketCard(g, m); }).join("") + "</div>" +
        '<p class="foot-note">Die große Zahl ist die Chance laut Modell, darunter die faire Quote' + (g.odds && g.odds.length ? " und die beste Quote der Anbieter. Gelb markiert: Der Anbieter zahlt mehr als fair." : ".") + "</p>";
    }

    // Quotenvergleich
    if (g.odds && g.odds.length) {
      var books = g.bookList || [];
      h += '<h2 class="section">Quotenvergleich</h2><div class="panel table-wrap"><table><thead><tr><th>Tipp</th><th>Fair</th>' + books.map(function (b) { return "<th>" + esc(b) + "</th>"; }).join("") + "<th>Vorteil</th></tr></thead><tbody>" +
        g.odds.map(function (r) {
          var mx = Math.max.apply(null, books.map(function (b) { return r[b] || 0; }));
          return "<tr><td>" + esc(r.tipp) + "</td><td>" + fair(r.p) + "</td>" +
            books.map(function (b) { return r[b] ? "<td" + (r[b] === mx && books.length > 1 ? ' class="best"' : "") + ">" + odds(r[b]) + "</td>" : '<td class="na">–</td>'; }).join("") +
            (r.unsure ? '<td class="na">unsicher</td>' : r.value != null && r.value > 0 ? '<td class="pos">' + signed(r.value) + "</td>" : '<td class="na">' + (r.value != null ? signed(r.value) : "–") + "</td>") + "</tr>";
        }).join("") + "</tbody></table></div>" +
        '<p class="foot-note">„Unsicher“ heißt: Modell und Anbieter liegen so weit auseinander, dass dem Modell vermutlich eine Information fehlt.</p>' +
        '<div class="links">' + books.filter(function (b) { return BOOK_URL[b]; }).map(function (b) { return '<a class="btn" href="' + BOOK_URL[b] + '" target="_blank" rel="noopener">' + esc(b) + " öffnen</a>"; }).join("") + "</div>";
    } else {
      h += '<h2 class="section">Quotenvergleich</h2><p class="prose">Für dieses Spiel liegen keine Quoten von Tipico, Betano oder Winamax vor. Die Anbieter werden einmal am Tag für die großen Ligen abgefragt.</p>' +
        '<div class="links"><a class="btn" href="' + esc(g.link) + '" target="_blank" rel="noopener">Quoten bei OddsPortal ansehen</a></div>';
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
        '<p class="foot-note">Quelle: Darts Orakel.' + (g.darts.format ? " Gespielt wird " + esc(g.darts.format) + "." : "") + "</p>";
    }

    // Spieler: nur Punkte, jede Zeile führt zur Punkte-Leiter
    var pl = allPlayers().filter(function (y) { return y.g.id === g.id; });
    if (pl.length) {
      var sideRows = function (side, name) {
        var r = pl.filter(function (y) { return y.p.side === side; }).sort(function (a, b) { return b.pts.exp - a.pts.exp; });
        return r.length ? '<h3 class="team-head">' + esc(name) + '</h3><div class="list">' + r.map(function (y) { return playerRow(y, false); }).join("") + "</div>" : "";
      };
      h += '<h2 class="section">Spieler und Punkte</h2><p class="prose small">Tipp auf einen Spieler für die Chance auf 20, 25, 30 oder mehr Punkte.</p>' +
        sideRows("a", g.a) + sideRows("b", g.b);
    }

    // Großwetten auf dieses Spiel
    var ws = whalesFor(g);
    if (ws.length) h += '<h2 class="section">Großwetten</h2>' + whaleGroup(ws, null);

    // Alle Wetten, eingeklappt
    if (g.groups && g.groups.length) {
      h += '<h2 class="section">Alle Wetten</h2><div class="panel">' + g.groups.map(function (gr) {
        var top = gr.items.reduce(function (m, x) { return x.p > m.p ? x : m; }, gr.items[0]);
        return '<details class="market"><summary><span>' + esc(gr.group) + '</span><span class="hint">' + esc(top.label) + " " + pct(top.p) + "</span></summary>" +
          '<div class="picks">' + gr.items.map(function (x) {
            return '<div class="pick"><span class="lbl">' + esc(x.label) + '</span><span class="num"><span class="p">' + pct(x.p) + '</span><span class="q">' + odds(x.odds) + "</span></span></div>";
          }).join("") + "</div></details>";
      }).join("") + "</div>";
    }
    $("#view").innerHTML = h;
    window.scrollTo(0, 0);
  }

  // ---------------------------------------------------------------- Spieler (NBA-Punkte)
  function allPlayers() {
    var out = [];
    D.games.forEach(function (g) {
      (g.props || []).forEach(function (p, i) {
        var pts = (p.stats || []).filter(function (s) { return s.stat === "pts"; })[0];
        if (!pts) return;
        out.push({ g: g, p: p, i: i, pts: pts, opp: p.side === "a" ? g.b : g.a, form: p.last5 != null && p.season != null ? p.last5 - p.season : 0 });
      });
    });
    return out;
  }
  function playerHref(x) { return "#/spieler/" + encodeURIComponent(x.g.id) + "/" + x.i; }
  function formTag(x) {
    if (x.form >= 3) return '<span class="tag-hot">+' + nf1.format(x.form) + " in Form</span>";
    if (x.form <= -3) return '<span class="tag-cold">−' + nf1.format(-x.form) + " schwach</span>";
    return "";
  }
  function playerRow(x, showGame) {
    var st = x.p.status ? '<span class="tag-warn">' + esc(x.p.status) + "</span>" : "";
    return '<a class="prow" href="' + playerHref(x) + '">' +
      '<div class="pr-name"><b>' + esc(x.p.player) + "</b><span>" + (showGame ? esc(x.p.team.split(" ").pop()) + " gegen " + esc(x.opp) + ", " + esc(dayLabel(x.g.start)) + " " + time(x.g.start) : esc(x.p.team)) + "</span>" +
      (st || formTag(x) ? '<div class="pr-tags">' + st + formTag(x) + "</div>" : "") + "</div>" +
      '<div class="pr-num"><span>Saison</span><b>' + nf1.format(x.p.season) + "</b></div>" +
      '<div class="pr-num"><span>Letzte 5</span><b>' + nf1.format(x.p.last5) + "</b></div>" +
      '<div class="pr-num strong"><span>Erwartet</span><b>' + nf1.format(x.pts.exp) + "</b></div>" + CHEV + "</a>";
  }

  function viewPlayers() {
    var list = allPlayers();
    var h = '<h1>Spieler</h1><p class="lead">NBA-Spieler der nächsten Spiele. Tipp auf einen Spieler, um zu sehen, mit welcher Wahrscheinlichkeit er wie viele Punkte macht.</p>';
    if (!list.length) {
      $("#view").innerHTML = h + '<div class="empty"><p>Gerade stehen keine NBA-Spiele mit Spielerdaten an.</p></div>'; return;
    }
    var hot = list.filter(function (x) { return x.p.season >= 15 && x.form > 0 && !/aus|out/i.test(x.p.status || ""); }).sort(function (a, b) { return b.form - a.form; }).slice(0, 10);
    var top = list.slice().sort(function (a, b) { return b.pts.exp - a.pts.exp; }).slice(0, 15);
    h += '<input class="search wide" id="pq" type="search" placeholder="Spieler suchen" aria-label="Spieler suchen">' +
      '<div id="presults"></div>' +
      '<h2 class="section">Heißeste Spieler</h2><p class="prose small">Spieler mit mindestens 15 Punkten im Schnitt, die in den letzten 5 Spielen am deutlichsten darüber lagen.</p><div class="list">' + hot.map(function (x) { return playerRow(x, true); }).join("") + "</div>" +
      '<h2 class="section">Meiste Punkte erwartet</h2><div class="list">' + top.map(function (x) { return playerRow(x, true); }).join("") + "</div>";
    $("#view").innerHTML = h;
    var inp = $("#pq");
    inp.addEventListener("input", function () {
      var q = inp.value.trim().toLowerCase();
      var hits = q.length < 2 ? [] : list.filter(function (x) { return x.p.player.toLowerCase().indexOf(q) >= 0; }).slice(0, 12);
      $("#presults").innerHTML = q.length < 2 ? "" : hits.length ? '<div class="list" style="margin-top:12px">' + hits.map(function (x) { return playerRow(x, true); }).join("") + "</div>" : '<p class="muted" style="margin:14px 2px">Kein Spieler gefunden.</p>';
    });
  }

  function viewPlayer(gid, idx) {
    var x = allPlayers().filter(function (y) { return y.g.id === gid && y.i === idx; })[0];
    if (!x) { $("#view").innerHTML = '<a class="back" href="#/spieler">' + BACK + 'Alle Spieler</a><div class="empty"><p>Dieser Spieler ist nicht mehr in der Liste. Das Spiel hat vermutlich schon begonnen.</p></div>'; return; }
    var p = x.p, g = x.g, L = p.ladder;
    var h = '<a class="back" href="#/spiel/' + encodeURIComponent(g.id) + '">' + BACK + esc(g.a) + " – " + esc(g.b) + "</a>" +
      '<section class="hero player-hero"><div class="meta"><span>' + esc(p.team) + "</span><span>gegen " + esc(x.opp) + ", " + esc(longDate(g.start)) + "</span></div>" +
      '<div class="ph-name">' + esc(p.player) + "</div>" +
      '<div class="ph-row"><div class="ph-big"><b>' + nf1.format(x.pts.exp) + "</b><span>Punkte erwartet</span></div>" +
      '<div class="ph-facts"><div><span>Saison</span><b>' + nf1.format(p.season) + '</b></div><div><span>Letzte 5</span><b>' + nf1.format(p.last5) + "</b></div>" +
      "<div><span>Schwankung</span><b>±" + nf1.format(p.sd || 0) + "</b></div><div><span>Minuten</span><b>" + nf0.format(p.min) + "</b></div></div></div>" +
      (p.status ? '<p class="verdict">Status: <strong>' + esc(p.status) + "</strong>. Falls er ausfällt, gilt nichts davon.</p>" : "") + "</section>";

    // Letzte 10 Spiele
    if (p.last && p.last.length) {
      var mx = Math.max.apply(null, p.last.concat([p.season || 0])) || 1;
      h += '<h2 class="section">Letzte ' + p.last.length + " Spiele</h2>" +
        '<div class="panel bars-panel"><div class="bars" role="img" aria-label="Punkte der letzten Spiele, ältestes links">' +
        '<i class="avg-line" style="bottom:' + Math.round(p.season / mx * 120) + 'px"><span>Ø ' + nf1.format(p.season) + "</span></i>" +
        p.last.map(function (v, k) {
          var cls = v >= p.season ? "up" : "down";
          return '<div class="bar ' + cls + (k >= p.last.length - 5 ? " recent" : "") + '"><b>' + v + '</b><i style="height:' + Math.max(3, Math.round(v / mx * 120)) + 'px"></i></div>';
        }).join("") + '</div><div class="bars-foot"><span>älter</span><span>letzte 5 hervorgehoben</span><span>neuestes</span></div></div>';
    }

    // Punkte-Leiter
    if (L) {
      var rows = L.atLeast.map(function (pa, k) { return { n: L.from + k, at: pa, ex: L.exact[k] }; });
      var main = rows.reduce(function (m, r) { return Math.abs(r.at - 0.5) < Math.abs(m.at - 0.5) ? r : m; }, rows[0]);
      h += '<h2 class="section">Wie viele Punkte?</h2>' +
        '<p class="prose small">„Mindestens 32“ entspricht bei den Anbietern „über 31,5“. Die faire Quote ist der Wert, ab dem sich die Wette laut Modell lohnt. Dunkel markiert: die Linie nahe 50 %, wie sie Anbieter meist anbieten.</p>' +
        '<div class="panel ladder"><div class="lrow lhead"><span>Punkte</span><span>Chance</span><span class="lq">Faire Quote</span><span class="lx">Genau</span></div>' +
        rows.filter(function (r) { return r.at <= 0.97 && r.at >= 0.03; }).map(function (r) {
          return '<div class="lrow' + (r === main ? " main" : "") + '"><span class="ln">mind. <b>' + r.n + "</b></span>" +
            '<span class="lp"><i style="width:' + (r.at * 100) + '%"></i><b>' + pct(r.at) + "</b></span>" +
            '<span class="lq">' + fair(r.at) + '</span><span class="lx">' + pct(r.ex, true) + "</span></div>";
        }).join("") + "</div>" +
        '<p class="foot-note">Berechnet aus den letzten 20 Spielen (neuere zählen mehr) mit der echten Schwankung des Spielers. Gegner, Verletzungen von Mitspielern und Pausen-Abende sind nicht eingerechnet.</p>';
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
    h += '<p class="foot-note">Ausgeblendet sind Tipps, bei denen das Modell mehr als 10 Prozentpunkte oder mehr als das 1,3-Fache von der Einschätzung aller Anbieter zusammen abweicht. So große Abstände sind fast immer ein Modellfehler, etwa wegen einer fehlenden Aufstellung. Auch die übrigen Vorteile heißen nur, dass Modell und Anbieter sich nicht einig sind. Unter Info steht, wo das Modell mithalten kann.</p>';
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
    else if (page === "spieler" && parts.length >= 3) viewPlayer(decodeURIComponent(parts[1]), +parts[2]);
    else if (page === "spieler") viewPlayers();
    else if (page === "value") viewValue();
    else if (page === "grosswetten") viewWhales();
    else if (page === "wetten") viewBets();
    else if (page === "info") viewInfo();
    else viewGames();
    if (page !== "spiel" && !(page === "spieler" && parts.length >= 3)) window.scrollTo(0, 0);
  }

  function stamp() {
    if (!D || !D.updated) return;
    var u = date(D.updated); $("#stamp").textContent = "Stand " + u.getDate() + "." + (u.getMonth() + 1) + "., " + time(D.updated) + " Uhr";
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
