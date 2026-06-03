"""
dashboard.py — Live terminal dashboard showing real-time store metrics
Uses 'rich' for a polished terminal UI that updates as events are ingested.

Run: python dashboard.py
"""

import time
import json
import requests
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.layout import Layout
from rich.panel import Panel
from rich.live import Live
from rich.text import Text
from rich import box

API_BASE = "http://localhost:8000"
STORE_ID = "ST1008"
REFRESH_SECONDS = 3

console = Console()


def fetch_json(endpoint: str) -> dict:
    try:
        r = requests.get(f"{API_BASE}{endpoint}", timeout=5)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def make_metrics_panel(metrics: dict) -> Panel:
    if "error" in metrics:
        return Panel(f"[red]Error: {metrics['error']}[/]", title="📊 Store Metrics")

    g = Table.grid(padding=(0, 2))
    g.add_column(style="bold cyan", justify="right")
    g.add_column()
    g.add_row("Unique Visitors", f"[green]{metrics.get('unique_visitors', 0)}[/]")
    g.add_row("Conversion Rate", f"[yellow]{metrics.get('conversion_rate', 0):.1%}[/]")
    g.add_row("Queue Depth Now", f"[{'red' if (metrics.get('queue_depth_current',0) > 3) else 'white'}]{metrics.get('queue_depth_current', 0)}[/]")
    g.add_row("Abandonment Rate", f"{metrics.get('abandonment_rate', 0):.1%}")
    g.add_row("Total Transactions", f"{metrics.get('total_transactions', 0)}")
    return Panel(g, title="[bold]📊 Store Metrics — ST1008 Brigade Bangalore[/]",
                 border_style="green")


def make_funnel_panel(funnel: dict) -> Panel:
    if "error" in funnel:
        return Panel(f"[red]{funnel['error']}[/]", title="🔽 Conversion Funnel")

    t = Table(box=box.SIMPLE, show_header=True, header_style="bold magenta")
    t.add_column("Stage")
    t.add_column("Count", justify="right")
    t.add_column("Drop-off %", justify="right")

    for step in funnel.get("funnel", []):
        bar = "█" * max(1, int(step["count"] / 10))
        drop_color = "red" if step["dropoff_pct"] > 40 else "yellow" if step["dropoff_pct"] > 20 else "green"
        t.add_row(step["stage"], str(step["count"]),
                  f"[{drop_color}]{step['dropoff_pct']:.1f}%[/]")

    return Panel(t, title="[bold]🔽 Conversion Funnel[/]", border_style="magenta")


def make_heatmap_panel(heatmap: dict) -> Panel:
    if "error" in heatmap:
        return Panel(f"[red]{heatmap['error']}[/]", title="🔥 Zone Heatmap")

    t = Table(box=box.SIMPLE, show_header=True, header_style="bold yellow")
    t.add_column("Zone")
    t.add_column("Visits", justify="right")
    t.add_column("Avg Dwell", justify="right")
    t.add_column("Heat", justify="left")

    for zone in heatmap.get("zones", []):
        score = zone.get("normalised_score", 0)
        heat_bar = "█" * max(0, int(score / 10))
        color = "red" if score > 70 else "yellow" if score > 40 else "cyan"
        dwell_s = zone.get("avg_dwell_ms", 0) / 1000
        t.add_row(
            zone["zone_id"],
            str(zone["visit_count"]),
            f"{dwell_s:.0f}s",
            f"[{color}]{heat_bar}[/] {score:.0f}",
        )

    return Panel(t, title="[bold]🔥 Zone Heatmap[/]", border_style="yellow")


def make_anomalies_panel(anomalies: dict) -> Panel:
    if "error" in anomalies:
        return Panel(f"[red]{anomalies['error']}[/]", title="⚠️  Anomalies")

    items = anomalies.get("anomalies", [])
    if not items:
        return Panel("[green]✓ No active anomalies[/]", title="[bold]⚠️  Anomalies[/]",
                     border_style="green")

    lines = []
    for a in items:
        sev = a["severity"]
        color = {"CRITICAL": "red", "WARN": "yellow", "INFO": "blue"}.get(sev, "white")
        lines.append(f"[{color}][{sev}][/] {a['type']}: {a['description']}")
        lines.append(f"  [dim]→ {a['suggested_action']}[/]")

    return Panel("\n".join(lines), title="[bold]⚠️  Anomalies[/]", border_style="red")


def make_health_panel(health: dict) -> Panel:
    if "error" in health:
        return Panel(f"[red]{health['error']}[/]", title="💚 Health")
    feed = health.get("feed_status", "UNKNOWN")
    color = "green" if feed == "OK" else "red"
    lag = health.get("feed_lag_minutes", 0)
    cams = ", ".join(health.get("cameras_active", []))
    txt = (f"[{color}]Feed: {feed}[/]  |  Lag: {lag:.1f}min  |  "
           f"Events: {health.get('event_count_today', 0)}  |  Cams: {cams}")
    return Panel(txt, title="[bold]💚 Health[/]", border_style=color)


def build_layout(ts: str) -> Layout:
    metrics   = fetch_json(f"/stores/{STORE_ID}/metrics")
    funnel    = fetch_json(f"/stores/{STORE_ID}/funnel")
    heatmap   = fetch_json(f"/stores/{STORE_ID}/heatmap")
    anomalies = fetch_json(f"/stores/{STORE_ID}/anomalies")
    health    = fetch_json("/health")

    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="top", size=12),
        Layout(name="mid", size=15),
        Layout(name="bottom", size=6),
    )
    layout["header"].update(Panel(
        f"[bold white]🏪 Purplle Store Intelligence — Brigade Road Bangalore[/]  "
        f"[dim]{ts}[/]",
        border_style="blue",
    ))
    layout["top"].split_row(
        Layout(make_metrics_panel(metrics), name="metrics"),
        Layout(make_funnel_panel(funnel), name="funnel"),
    )
    layout["mid"].split_row(
        Layout(make_heatmap_panel(heatmap), name="heatmap"),
        Layout(make_anomalies_panel(anomalies), name="anomalies"),
    )
    layout["bottom"].update(make_health_panel(health))
    return layout


def run():
    console.print("[bold green]Starting Store Intelligence Dashboard...[/]")
    console.print(f"[dim]Connecting to {API_BASE}[/]\n")

    with Live(console=console, refresh_per_second=1, screen=True) as live:
        while True:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                layout = build_layout(ts)
                live.update(layout)
            except Exception as e:
                live.update(Panel(f"[red]Dashboard error: {e}[/]"))
            time.sleep(REFRESH_SECONDS)


if __name__ == "__main__":
    run()
