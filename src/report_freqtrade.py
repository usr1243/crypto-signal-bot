"""
PLAN wild-booping-firefly.md, Stufe C9: HTML-Bericht fuer das NEUE
freqtrade-System -- das Gegenstueck zu report.py fuer das bisherige
run_loop.py/telegram_listener.py-System, das UNVERAENDERT weiterlaeuft.

Eigene Datei statt Erweiterung von report.py: die beiden Systeme haben
unterschiedliche Datenquellen (freqtrade fuehrt seine EIGENE `trades`-Tabelle,
unsere Score-Aufschluesselung liegt getrennt in explainability.py's
score_breakdowns-Tabelle, verknuepft ueber `enter_tag`) -- eine Verzweigung in
derselben Datei haette report.py fuer das laufende Produktivsystem unnoetig
riskant gemacht.

Aufruf:
    ./.venv/bin/python -m src.report_freqtrade \\
        --freqtrade-db ../freqtrade/user_data/tradesv3.dryrun.sqlite \\
        --explain-db  ../freqtrade/user_data/score_breakdowns.sqlite3
"""

from __future__ import annotations

import argparse
import html
import sqlite3
from pathlib import Path

from . import explainability

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_FT_DB = BASE_DIR / "freqtrade" / "user_data" / "tradesv3.dryrun.sqlite"
DEFAULT_EXPLAIN_DB = BASE_DIR / "freqtrade" / "user_data" / "score_breakdowns.sqlite3"
OUTPUT_PATH = BASE_DIR / "freqtrade" / "user_data" / "report_freqtrade.html"


def _fetch_trades(freqtrade_db: str | Path) -> list[dict]:
    conn = sqlite3.connect(freqtrade_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT pair, is_open, open_rate, close_rate, close_profit, close_profit_abs,
                  stake_amount, amount, open_date, close_date, stop_loss, initial_stop_loss,
                  exit_reason, enter_tag, is_short
           FROM trades ORDER BY open_date DESC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _fmt_num(x: float | None) -> str:
    return "–" if x is None else f"{x:,.2f}"


def _fmt_pct(x: float | None) -> str:
    return "–" if x is None else f"{x:+.2f}%"


def _breakdown_html(breakdown: list[dict]) -> str:
    if not breakdown:
        return ("<p class='muted'>Keine Aufschlüsselung gefunden — entweder ein Trade von vor "
                "C9, oder die Referenz-ID wurde nicht gefunden.</p>")
    rows = []
    for c in sorted(breakdown, key=lambda c: -abs(c["points"])):
        cls = "pos" if c["points"] > 0 else "neg"
        rows.append(
            f"<tr><td class='{cls}'>{c['points']:+d}</td>"
            f"<td>{html.escape(c['rule'])}</td>"
            f"<td>{html.escape(c['detail'])}</td></tr>"
        )
    total = sum(c["points"] for c in breakdown)
    return (
        "<table class='breakdown'><thead><tr><th>Punkte</th><th>Regel</th><th>Begründung</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        f"<tfoot><tr><td colspan='3'>Summe: {total}</td></tr></tfoot></table>"
    )


def _trade_row_html(t: dict, explain_conn: sqlite3.Connection) -> str:
    entry = explainability.get_breakdown(explain_conn, t["enter_tag"]) if t["enter_tag"] else None
    score = entry["score"] if entry else None
    breakdown = entry["breakdown"] if entry else []

    is_open = bool(t["is_open"])
    profit_pct = (t["close_profit"] * 100) if t["close_profit"] is not None else None
    pnl_cls = "pos" if profit_pct and profit_pct > 0 else ("neg" if profit_pct and profit_pct < 0 else "")
    status_str = "offen" if is_open else (t["exit_reason"] or "geschlossen")

    direction = "SHORT" if t["is_short"] else "LONG"
    badge_cls = "short" if t["is_short"] else "long"
    score_str = f"{score:+d}/100" if score is not None else "?"

    return f"""
    <details class="signal-card {badge_cls}">
      <summary>
        <span class="badge {badge_cls}">{direction}</span>
        <span class="symbol">{html.escape(t['pair'])}</span>
        <span class="tf num">{html.escape(status_str)}</span>
        <span class="price num">{_fmt_num(t['open_rate'])} → {_fmt_num(t['close_rate'])}</span>
        <span class="score num">{score_str}</span>
        <span class="pnl num {pnl_cls}">{_fmt_pct(profit_pct) if profit_pct is not None else "–"}</span>
        <span class="ts">{html.escape(str(t['open_date'])[:19])}</span>
      </summary>
      <div class="detail">
        {_breakdown_html(breakdown)}
      </div>
    </details>"""


def generate(freqtrade_db: str | Path = DEFAULT_FT_DB, explain_db: str | Path = DEFAULT_EXPLAIN_DB) -> Path:
    if not Path(freqtrade_db).exists():
        raise FileNotFoundError(
            f"freqtrade-Datenbank nicht gefunden: {freqtrade_db} — existiert erst, "
            f"sobald `freqtrade trade` mindestens einmal gelaufen ist."
        )
    trades = _fetch_trades(freqtrade_db)
    explain_conn = explainability.connect(explain_db)

    open_trades = [t for t in trades if t["is_open"]]
    closed_trades = [t for t in trades if not t["is_open"]]
    wins = sum(1 for t in closed_trades if (t.get("close_profit") or 0) > 0)
    winrate = f"{wins}/{len(closed_trades)} ({wins/len(closed_trades)*100:.0f}%)" if closed_trades else "noch keine"

    honesty_note = ""
    if not trades:
        honesty_note = (
            "<div class='callout'>⚠️ Noch keine Trades in der freqtrade-Datenbank — "
            "dry_run läuft entweder noch nicht oder hat noch kein Signal umgesetzt.</div>"
        )

    open_rows = "\n".join(_trade_row_html(t, explain_conn) for t in open_trades) \
        or "<p class='muted'>Keine offenen Trades.</p>"
    closed_rows = "\n".join(_trade_row_html(t, explain_conn) for t in closed_trades) \
        or "<p class='muted'>Keine geschlossenen Trades.</p>"

    html_doc = f"""<title>Signalprotokoll (freqtrade)</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
  :root {{
    --bg:#eef0ee; --card:#ffffff; --ink:#161b19; --muted:#66716c;
    --line:rgba(22,27,25,0.12); --line-strong:rgba(22,27,25,0.22);
    --accent:#2f6f5e; --accent-soft:#dfeae6;
    --pos:#1f7a52; --pos-soft:#e1f0e8; --neg:#b5432f; --neg-soft:#f6e2dd;
    --long:#1f7a52; --short:#b5432f;
    --shadow:0 1px 2px rgba(22,27,25,0.05), 0 6px 20px -14px rgba(22,27,25,0.25);
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --bg:#121615; --card:#1a201e; --ink:#e6ece9; --muted:#96a39d;
      --line:rgba(230,236,233,0.13); --line-strong:rgba(230,236,233,0.24);
      --accent:#57b79e; --accent-soft:#193029;
      --pos:#3fbf82; --pos-soft:#173226; --neg:#e2705a; --neg-soft:#33201b;
      --long:#3fbf82; --short:#e2705a;
      --shadow:0 1px 2px rgba(0,0,0,0.3), 0 6px 20px -14px rgba(0,0,0,0.5);
    }}
  }}
  :root[data-theme="dark"] {{
    --bg:#121615; --card:#1a201e; --ink:#e6ece9; --muted:#96a39d;
    --line:rgba(230,236,233,0.13); --line-strong:rgba(230,236,233,0.24);
    --accent:#57b79e; --accent-soft:#193029;
    --pos:#3fbf82; --pos-soft:#173226; --neg:#e2705a; --neg-soft:#33201b;
    --long:#3fbf82; --short:#e2705a;
    --shadow:0 1px 2px rgba(0,0,0,0.3), 0 6px 20px -14px rgba(0,0,0,0.5);
  }}
  * {{ box-sizing:border-box; }}
  body {{ background:var(--bg); color:var(--ink); font-family:"IBM Plex Sans",-apple-system,BlinkMacSystemFont,sans-serif;
          margin:0; padding:28px 20px 60px; font-size:15px; line-height:1.55; -webkit-font-smoothing:antialiased; }}
  .num {{ font-family:"IBM Plex Mono",ui-monospace,monospace; font-variant-numeric:tabular-nums; }}
  .wrap {{ max-width:900px; margin:0 auto; }}
  h1 {{ font-size:21px; font-weight:700; letter-spacing:-0.01em; margin:0 0 4px; text-wrap:balance; }}
  .sub {{ color:var(--muted); font-size:12.5px; margin-bottom:22px; font-family:"IBM Plex Mono",monospace; }}
  .status-bar {{ display:grid; grid-template-columns:repeat(3,1fr); gap:1px; background:var(--line);
                 border:1px solid var(--line); border-radius:10px; overflow:hidden; margin-bottom:22px; }}
  .stat {{ background:var(--card); padding:12px 14px; }}
  .stat .label {{ font-size:10.5px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted);
                  font-family:"IBM Plex Mono",monospace; display:block; margin-bottom:6px; }}
  .stat .value {{ font-size:17px; font-weight:600; }}
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
  .badge.long {{ background:var(--long); }} .badge.short {{ background:var(--short); }}
  .symbol {{ font-weight:600; }}
  .tf, .ts {{ color:var(--muted); }}
  .ts {{ font-family:"IBM Plex Mono",monospace; font-size:12px; }}
  .pnl {{ margin-left:auto; font-weight:600; }}
  .detail {{ padding:2px 14px 14px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  table.breakdown th {{ text-align:left; padding:4px 8px; border-bottom:1px solid var(--line-strong);
                        font-size:10.5px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted);
                        font-family:"IBM Plex Mono",monospace; font-weight:500; }}
  table.breakdown td {{ text-align:left; padding:5px 8px; border-bottom:1px solid var(--line); }}
  table.breakdown td:first-child {{ font-family:"IBM Plex Mono",monospace; font-variant-numeric:tabular-nums; width:56px; font-weight:600; }}
  table.breakdown tfoot td {{ font-family:"IBM Plex Mono",monospace; color:var(--muted); padding-top:8px; border-bottom:none; }}
  .pos {{ color:var(--pos); }} .neg {{ color:var(--neg); }} .muted {{ color:var(--muted); }}
  footer {{ margin-top:30px; color:var(--muted); font-size:12px; }}
  footer code {{ font-family:"IBM Plex Mono",monospace; background:var(--line); padding:1px 5px; border-radius:4px; }}
</style>
<div class="wrap">
  <h1>Signalprotokoll (freqtrade)</h1>
  <div class="sub">{Path(freqtrade_db).name} · {len(trades)} Trade(s) gesamt</div>

  <div class="status-bar">
    <div class="stat"><span class="label">Offene Trades</span><div class="value num">{len(open_trades)}</div></div>
    <div class="stat"><span class="label">Geschlossene Trades</span><div class="value num">{len(closed_trades)}</div></div>
    <div class="stat"><span class="label">Winrate</span><div class="value num">{winrate}</div></div>
  </div>

  {honesty_note}

  <h2>Offene Trades</h2>
  {open_rows}

  <h2>Geschlossene Trades</h2>
  {closed_rows}

  <footer>Kein Finanzrat. dry_run-System (siehe PLAN wild-booping-firefly.md Stufe C) —
  solange <code>dry_run: true</code> in config.json steht, ist kein echtes Geld im Spiel.
  Neu erzeugen: <code>python -m src.report_freqtrade</code></footer>
</div>"""

    explain_conn.close()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(html_doc, encoding="utf-8")
    return OUTPUT_PATH


def main() -> None:
    parser = argparse.ArgumentParser(description="Erzeugt einen HTML-Bericht aus der freqtrade-Datenbank + Score-Aufschlüsselung")
    parser.add_argument("--freqtrade-db", default=str(DEFAULT_FT_DB))
    parser.add_argument("--explain-db", default=str(DEFAULT_EXPLAIN_DB))
    args = parser.parse_args()

    path = generate(freqtrade_db=args.freqtrade_db, explain_db=args.explain_db)
    print(f"Bericht erzeugt: {path}")


if __name__ == "__main__":
    main()
