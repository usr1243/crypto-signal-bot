"""
Stufe "Transparenz": erzeugt einen lesbaren HTML-Bericht aus der SQLite-DB.

Warum das existiert: der Bot war bisher eine Blackbox -- man sah eine Zahl
("Score 23/100") und Telegram-Nachrichten, aber nicht, WELCHE der ~20 Regeln
wie viele Punkte beigetragen hat, und keinen Gesamtüberblick über offene/
geschlossene Uebungstrades. Dieser Bericht macht jede einzelne Entscheidung
nachvollziehbar -- direkt aus den gespeicherten Daten, nicht durch Nachbauen
der Scoring-Funktion.

Bewusst eine erzeugte Datei, kein Web-Server: der Kollege sitzt nicht an
Lorenz' Mac. Ein `localhost`-Server nuetzt ihm nichts, ein weiterer
Dauerprozess ist eine weitere Fehlerquelle (siehe db.connect_with_retry(),
das eigens wegen beobachteter Fehler im Dauerbetrieb existiert). Eine HTML-
Datei laesst sich dagegen lokal oeffnen UND als Artifact teilen.

Aufruf:
    ./.venv/bin/python -m src.report
    ./.venv/bin/python -m src.report --limit 50
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

from . import db, paper_trading
from .main import DB_PATH

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_PATH = BASE_DIR / "data" / "report.html"


def _fetch_signals(conn, limit: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, ts, symbol, timeframe, price, candidate, score, technical_json, macro_json "
        "FROM signals ORDER BY id DESC LIMIT ?", (limit,),
    ).fetchall()
    out = []
    for row in rows:
        sid, ts, symbol, tf, price, candidate, score, tech_json, macro_json = row
        out.append({
            "id": sid, "ts": ts, "symbol": symbol, "timeframe": tf, "price": price,
            "candidate": candidate, "score": score,
            "technical": json.loads(tech_json) if tech_json else {},
            "macro": json.loads(macro_json) if macro_json else None,
        })
    return out


def _fetch_paper_trades(conn) -> tuple[list[dict], list[dict]]:
    rows = conn.execute(
        "SELECT id, signal_id, symbol, direction, entry, stop, target, size_pct, "
        "reward_risk_ratio, atr_multiple, status, closed_price, pnl_pct, opened_at, closed_at "
        "FROM paper_trades ORDER BY id DESC"
    ).fetchall()
    cols = ["id", "signal_id", "symbol", "direction", "entry", "stop", "target", "size_pct",
            "reward_risk_ratio", "atr_multiple", "status", "closed_price", "pnl_pct", "opened_at", "closed_at"]
    all_trades = [dict(zip(cols, row)) for row in rows]
    open_trades = [t for t in all_trades if t["status"] == "open"]
    closed_trades = [t for t in all_trades if t["status"] != "open"]
    return open_trades, closed_trades


def _fmt_pct(x: float | None) -> str:
    return "–" if x is None else f"{x:+.2f}%"


def _fmt_num(x: float | None) -> str:
    return "–" if x is None else f"{x:,.2f}"


def _breakdown_html(technical: dict) -> str:
    parts = technical.get("score_breakdown") or []
    if not parts:
        return "<p class='muted'>Keine Aufschlüsselung gespeichert (Signal vor der Transparenz-Erweiterung).</p>"
    rows = []
    for p in sorted(parts, key=lambda c: -abs(c["points"])):
        cls = "pos" if p["points"] > 0 else "neg"
        rows.append(
            f"<tr><td class='{cls}'>{p['points']:+d}</td>"
            f"<td>{html.escape(p['rule'])}</td>"
            f"<td>{html.escape(p['detail'])}</td></tr>"
        )
    total = sum(p["points"] for p in parts)
    return (
        "<table class='breakdown'><thead><tr><th>Punkte</th><th>Regel</th><th>Begründung</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        f"<tfoot><tr><td colspan='3'>Summe: {total}</td></tr></tfoot></table>"
    )


def _signal_row_html(sig: dict) -> str:
    cand = sig["candidate"]
    badge_cls = {"LONG": "long", "SHORT": "short", "NONE": "none"}[cand]
    macro = sig["macro"]
    macro_html = ""
    if macro:
        macro_html = (
            f"<div class='macro'><strong>Makro:</strong> {html.escape(macro.get('regime','?'))} "
            f"({macro.get('confidence', 0) * 100:.0f}%) — {html.escape(macro.get('reasoning', ''))}</div>"
        )
    return f"""
    <details class="signal-card {badge_cls}">
      <summary>
        <span class="badge {badge_cls}">{cand}</span>
        <span class="symbol">{html.escape(sig['symbol'])}</span>
        <span class="tf num">{html.escape(sig['timeframe'])}</span>
        <span class="price num">{_fmt_num(sig['price'])}</span>
        <span class="score num">{sig['score']:+d}/100</span>
        <span class="ts">{html.escape(sig['ts'][:19])}</span>
      </summary>
      <div class="detail">
        {_breakdown_html(sig['technical'])}
        {macro_html}
      </div>
    </details>"""


def _trade_row_html(t: dict) -> str:
    pnl = t.get("pnl_pct")
    pnl_cls = "pos" if pnl and pnl > 0 else ("neg" if pnl and pnl < 0 else "")
    return (
        f"<tr><td>{html.escape(t['symbol'])}</td><td>{html.escape(t['direction'])}</td>"
        f"<td class='num'>{_fmt_num(t['entry'])}</td><td class='num'>{_fmt_num(t['stop'])}</td>"
        f"<td class='num'>{_fmt_num(t['target'])}</td>"
        f"<td class='num'>{t.get('reward_risk_ratio') or '–'}</td>"
        f"<td>{html.escape(t['status'])}</td>"
        f"<td class='num {pnl_cls}'>{_fmt_pct(pnl)}</td>"
        f"<td class='num'>{html.escape((t['opened_at'] or '')[:19])}</td></tr>"
    )


def generate(limit: int = 100) -> Path:
    conn = db.connect(DB_PATH)
    signals = _fetch_signals(conn, limit)
    open_trades, closed_trades = _fetch_paper_trades(conn)
    paused = db.is_paused(conn)
    halted, halt_reason = paper_trading.check_circuit_breakers(conn)

    n_signals_total = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    n_paper_total = conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]

    wins = sum(1 for t in closed_trades if (t.get("pnl_pct") or 0) > 0)
    winrate = f"{wins}/{len(closed_trades)} ({wins/len(closed_trades)*100:.0f}%)" if closed_trades else "noch keine"

    honesty_note = ""
    if n_paper_total == 0:
        honesty_note = (
            "<div class='callout'>⚠️ Noch keine Übungstrades in der Datenbank — der Bot hat bislang "
            "kein Signal gefunden, das die Schwelle überschritten hat. Dieser Bericht zeigt die "
            "Werkzeuge, sobald es Trades gibt, füllt sich der Abschnitt unten automatisch.</div>"
        )

    signal_rows = "\n".join(_signal_row_html(s) for s in signals)
    open_rows = "\n".join(_trade_row_html(t) for t in open_trades) or "<tr><td colspan='9' class='muted'>Keine offenen Trades</td></tr>"
    closed_rows = "\n".join(_trade_row_html(t) for t in closed_trades) or "<tr><td colspan='9' class='muted'>Keine geschlossenen Trades</td></tr>"

    pause_state = "halt" if paused else "run"
    breaker_state = "halt" if halted else "run"

    html_doc = f"""<title>Signalprotokoll</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
  :root {{
    --bg:#eef0ee; --card:#ffffff; --ink:#161b19; --muted:#66716c;
    --line:rgba(22,27,25,0.12); --line-strong:rgba(22,27,25,0.22);
    --accent:#2f6f5e; --accent-soft:#dfeae6;
    --pos:#1f7a52; --pos-soft:#e1f0e8; --neg:#b5432f; --neg-soft:#f6e2dd;
    --long:#1f7a52; --short:#b5432f; --none:#7c8781;
    --shadow:0 1px 2px rgba(22,27,25,0.05), 0 6px 20px -14px rgba(22,27,25,0.25);
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --bg:#121615; --card:#1a201e; --ink:#e6ece9; --muted:#96a39d;
      --line:rgba(230,236,233,0.13); --line-strong:rgba(230,236,233,0.24);
      --accent:#57b79e; --accent-soft:#193029;
      --pos:#3fbf82; --pos-soft:#173226; --neg:#e2705a; --neg-soft:#33201b;
      --long:#3fbf82; --short:#e2705a; --none:#7c8983;
      --shadow:0 1px 2px rgba(0,0,0,0.3), 0 6px 20px -14px rgba(0,0,0,0.5);
    }}
  }}
  :root[data-theme="dark"] {{
    --bg:#121615; --card:#1a201e; --ink:#e6ece9; --muted:#96a39d;
    --line:rgba(230,236,233,0.13); --line-strong:rgba(230,236,233,0.24);
    --accent:#57b79e; --accent-soft:#193029;
    --pos:#3fbf82; --pos-soft:#173226; --neg:#e2705a; --neg-soft:#33201b;
    --long:#3fbf82; --short:#e2705a; --none:#7c8983;
    --shadow:0 1px 2px rgba(0,0,0,0.3), 0 6px 20px -14px rgba(0,0,0,0.5);
  }}
  * {{ box-sizing:border-box; }}
  body {{ background:var(--bg); color:var(--ink); font-family:"IBM Plex Sans",-apple-system,BlinkMacSystemFont,sans-serif;
          margin:0; padding:28px 20px 60px; font-size:15px; line-height:1.55; -webkit-font-smoothing:antialiased; }}
  .num {{ font-family:"IBM Plex Mono",ui-monospace,monospace; font-variant-numeric:tabular-nums; }}
  .wrap {{ max-width:900px; margin:0 auto; }}
  h1 {{ font-size:21px; font-weight:700; letter-spacing:-0.01em; margin:0 0 4px; text-wrap:balance; }}
  .sub {{ color:var(--muted); font-size:12.5px; margin-bottom:22px; font-family:"IBM Plex Mono",monospace; }}

  .status-bar {{ display:grid; grid-template-columns:repeat(4,1fr); gap:1px; background:var(--line);
                 border:1px solid var(--line); border-radius:10px; overflow:hidden; margin-bottom:22px; }}
  .stat {{ background:var(--card); padding:12px 14px; }}
  .stat .label {{ font-size:10.5px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted);
                  font-family:"IBM Plex Mono",monospace; display:block; margin-bottom:6px; }}
  .stat .value {{ font-size:17px; font-weight:600; display:flex; align-items:center; gap:6px; }}
  .dot {{ width:8px; height:8px; border-radius:50%; flex-shrink:0; }}
  .dot.run {{ background:var(--pos); box-shadow:0 0 0 3px var(--pos-soft); }}
  .dot.halt {{ background:var(--neg); box-shadow:0 0 0 3px var(--neg-soft); }}

  .callout {{ background:var(--accent-soft); border:1px solid var(--line); border-left:3px solid var(--accent);
              border-radius:8px; padding:13px 16px; margin-bottom:20px; font-size:13.5px; color:var(--ink); }}

  h2 {{ font-size:14px; font-weight:600; text-transform:uppercase; letter-spacing:.04em; color:var(--muted);
        margin:30px 0 10px; display:flex; align-items:center; gap:10px; }}
  h2::after {{ content:""; flex:1; height:1px; background:var(--line); }}

  .signal-card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; margin-bottom:7px; }}
  .signal-card summary {{ cursor:pointer; padding:11px 14px; display:flex; gap:10px; align-items:center;
                           flex-wrap:wrap; font-size:13.5px; list-style:none; }}
  .signal-card summary::-webkit-details-marker {{ display:none; }}
  .signal-card[open] {{ box-shadow:var(--shadow); }}
  .badge {{ font-family:"IBM Plex Mono",monospace; font-size:10.5px; font-weight:600; letter-spacing:.03em;
            padding:3px 9px; border-radius:100px; color:#fff; }}
  .badge.long {{ background:var(--long); }} .badge.short {{ background:var(--short); }} .badge.none {{ background:var(--none); }}
  .symbol {{ font-weight:600; }}
  .tf, .ts {{ color:var(--muted); }}
  .ts {{ font-family:"IBM Plex Mono",monospace; font-size:12px; }}
  .score {{ margin-left:auto; font-weight:600; }}
  .detail {{ padding:2px 14px 14px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  table.breakdown th {{ text-align:left; padding:4px 8px; border-bottom:1px solid var(--line-strong);
                        font-size:10.5px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted);
                        font-family:"IBM Plex Mono",monospace; font-weight:500; }}
  table.breakdown td {{ text-align:left; padding:5px 8px; border-bottom:1px solid var(--line); }}
  table.breakdown td:first-child {{ font-family:"IBM Plex Mono",monospace; font-variant-numeric:tabular-nums; width:56px; font-weight:600; }}
  table.breakdown tfoot td {{ font-family:"IBM Plex Mono",monospace; color:var(--muted); padding-top:8px; border-bottom:none; }}
  .pos {{ color:var(--pos); }} .neg {{ color:var(--neg); }} .muted {{ color:var(--muted); }}
  .macro {{ margin-top:10px; font-size:13px; color:var(--muted); border-top:1px solid var(--line); padding-top:10px; }}
  .table-wrap {{ overflow-x:auto; border-radius:10px; border:1px solid var(--line); }}
  table.trades {{ width:100%; border-collapse:collapse; background:var(--card); font-size:13px; min-width:640px; }}
  table.trades th {{ text-align:left; padding:9px 10px; background:var(--line); font-size:10.5px; text-transform:uppercase;
                     letter-spacing:.04em; font-family:"IBM Plex Mono",monospace; font-weight:500; color:var(--muted); }}
  table.trades td {{ padding:9px 10px; border-top:1px solid var(--line); }}
  table.trades td.num {{ font-family:"IBM Plex Mono",monospace; font-variant-numeric:tabular-nums; }}
  footer {{ margin-top:30px; color:var(--muted); font-size:12px; }}
  footer code {{ font-family:"IBM Plex Mono",monospace; background:var(--line); padding:1px 5px; border-radius:4px; }}
</style>
<div class="wrap">
  <h1>Signalprotokoll</h1>
  <div class="sub">{DB_PATH.name} · letzte {len(signals)} von {n_signals_total} Signalen</div>

  <div class="status-bar">
    <div class="stat"><span class="label">Betrieb</span>
      <div class="value"><span class="dot {pause_state}"></span>{"Pausiert" if paused else "Aktiv"}</div></div>
    <div class="stat"><span class="label">Circuit-Breaker</span>
      <div class="value"><span class="dot {breaker_state}"></span>{html.escape(halt_reason) if halted else "Unauffällig"}</div></div>
    <div class="stat"><span class="label">Offene Trades</span>
      <div class="value num">{len(open_trades)}</div></div>
    <div class="stat"><span class="label">Winrate</span>
      <div class="value num">{winrate}</div></div>
  </div>

  {honesty_note}

  <h2>Signale &amp; Entscheidungen</h2>
  {signal_rows}

  <h2>Offene Übungstrades</h2>
  <div class="table-wrap"><table class="trades">
    <thead><tr><th>Symbol</th><th>Richtung</th><th>Entry</th><th>Stop</th><th>Ziel</th><th>R:R</th><th>Status</th><th>PnL</th><th>Eröffnet</th></tr></thead>
    <tbody>{open_rows}</tbody>
  </table></div>

  <h2>Geschlossene Übungstrades</h2>
  <div class="table-wrap"><table class="trades">
    <thead><tr><th>Symbol</th><th>Richtung</th><th>Entry</th><th>Stop</th><th>Ziel</th><th>R:R</th><th>Status</th><th>PnL</th><th>Eröffnet</th></tr></thead>
    <tbody>{closed_rows}</tbody>
  </table></div>

  <footer>Kein Finanzrat. Alle Trades hier sind Übungstrades (Paper-Trading), kein echtes Geld.
  Neu erzeugen: <code>python -m src.report</code></footer>
</div>"""

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(html_doc, encoding="utf-8")
    return OUTPUT_PATH


def main() -> None:
    parser = argparse.ArgumentParser(description="Erzeugt einen HTML-Bericht aus der Bot-Datenbank")
    parser.add_argument("--limit", type=int, default=100, help="Wie viele Signale anzeigen (neueste zuerst)")
    args = parser.parse_args()

    path = generate(limit=args.limit)
    print(f"Bericht erzeugt: {path}")


if __name__ == "__main__":
    main()
