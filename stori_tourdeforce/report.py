"""ReportBuilder — generates report.html, report.md, and visualizations.

Uses matplotlib for static plots and inline SVG/PNG in the HTML report.
Falls back gracefully if matplotlib is not available.
"""

from __future__ import annotations

import base64
import io
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from stori_tourdeforce import __version__
from stori_tourdeforce.analyzers.graph import GraphAnalyzer
from stori_tourdeforce.analyzers.run import RunAnalyzer
from stori_tourdeforce.models import RunResult, RunStatus

logger = logging.getLogger(__name__)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAS_MPL = True
except ImportError:
    HAS_MPL = False
    logger.info("matplotlib not installed — plots will be skipped")


# ── Formatting helpers ────────────────────────────────────────────────────


def _fmt_ms(ms: float) -> str:
    """Format milliseconds into a human-readable duration string."""
    if ms <= 0:
        return "—"
    if ms < 1_000:
        return f"{ms:.0f}ms"
    secs = ms / 1_000
    if secs < 60:
        return f"{secs:.1f}s"
    mins = int(secs // 60)
    remaining = secs % 60
    return f"{mins}m {remaining:.0f}s"


def _fmt_num(n: int | float) -> str:
    """Format a number with locale-style thousands separators."""
    if isinstance(n, float):
        if n == int(n):
            return f"{int(n):,}"
        return f"{n:,.1f}"
    return f"{n:,}"


def _fmt_bytes(b: int) -> str:
    """Format bytes into KB/MB."""
    if b < 1_024:
        return f"{b} B"
    if b < 1_048_576:
        return f"{b / 1_024:.1f} KB"
    return f"{b / 1_048_576:.1f} MB"


def _fmt_pct(v: float) -> str:
    return f"{v:.1f}%"


class ReportBuilder:
    """Generates the final Tour de Force report."""

    def __init__(self, results: list[RunResult], output_dir: Path) -> None:
        self._results = results
        self._output = output_dir
        self._report_dir = output_dir / "report"
        self._plots_dir = self._report_dir / "plots"
        self._report_dir.mkdir(parents=True, exist_ok=True)
        self._plots_dir.mkdir(parents=True, exist_ok=True)
        self._analyzer = RunAnalyzer(results, output_dir)

    def build(self) -> Path:
        """Generate all reports and return the HTML report path."""
        kpis = self._analyzer.compute_kpis()
        db_path = self._analyzer.build_sqlite()

        # Generate plots
        plot_data: dict[str, str] = {}
        if HAS_MPL:
            plot_data = self._generate_plots()

        # Load graph if available
        graph_ascii = ""
        graph_mermaid = ""
        graph_metrics: dict[str, Any] = {}
        graph_data = self._load_graph()
        if graph_data:
            ga = GraphAnalyzer(graph_data)
            graph_ascii = ga.render_ascii()
            graph_mermaid = ga.render_mermaid()
            graph_metrics = ga.to_metrics()

        # Build reports
        html_path = self._build_html(kpis, plot_data, graph_ascii, graph_mermaid, graph_metrics)
        md_path = self._build_markdown(kpis, graph_ascii)

        logger.info("Reports generated: %s, %s", html_path, md_path)
        return html_path

    def _generate_plots(self) -> dict[str, str]:
        """Generate plot images and return as base64 data URIs."""
        plots: dict[str, str] = {}

        bg_color = "#0a0a0f"
        surface_color = "#16161f"
        text_color = "#a0a0b0"
        accent = "#6366f1"
        accent_light = "#818cf8"
        success_color = "#22c55e"
        danger_color = "#f85149"

        plt.rcParams.update({
            "figure.facecolor": bg_color,
            "axes.facecolor": surface_color,
            "axes.edgecolor": "#30363d",
            "text.color": text_color,
            "axes.labelcolor": text_color,
            "xtick.color": text_color,
            "ytick.color": text_color,
            "grid.color": "#1a1a25",
        })

        # Quality score distribution
        scores = [
            r.midi_metrics.get("quality_score", 0)
            for r in self._results if r.midi_metrics
        ]
        if scores:
            plots["quality_distribution"] = self._plot_histogram(
                scores, "Quality Score Distribution", "Quality Score", "Count",
                color=accent_light,
            )

        # Latency waterfall
        durations = [r.duration_ms for r in self._results if r.duration_ms > 0]
        if durations:
            plots["latency_distribution"] = self._plot_histogram(
                durations, "Run Duration Distribution", "Duration (ms)", "Count",
                color="#22d3ee",
            )

        # Status breakdown
        status_counts: dict[str, int] = {}
        for r in self._results:
            status_counts[r.status.value] = status_counts.get(r.status.value, 0) + 1
        if status_counts:
            plots["status_breakdown"] = self._plot_bar(
                status_counts, "Run Status Breakdown", "Status", "Count",
            )

        # Note count distribution
        note_counts = [float(r.orpheus_note_count) for r in self._results if r.orpheus_note_count > 0]
        if note_counts:
            plots["note_count_distribution"] = self._plot_histogram(
                note_counts, "Note Count Distribution", "Notes", "Count",
                color="#a855f7",
            )

        # Quality over time
        run_quality = [
            (i, r.midi_metrics.get("quality_score", 0))
            for i, r in enumerate(self._results)
            if r.midi_metrics
        ]
        if run_quality:
            plots["quality_timeline"] = self._plot_line(
                [x[0] for x in run_quality],
                [x[1] for x in run_quality],
                "Quality Score Over Runs", "Run Index", "Quality Score",
            )

        # Pitch class entropy
        entropies = [
            r.midi_metrics.get("pitch_class_entropy", 0)
            for r in self._results if r.midi_metrics
        ]
        if entropies:
            plots["entropy_distribution"] = self._plot_histogram(
                entropies, "Pitch Class Entropy", "Entropy (bits)", "Count",
                color="#22d3ee",
            )

        # MUSE operations per run
        muse_ops = [
            len(r.muse_commit_ids) + len(r.muse_merge_ids) + r.muse_checkout_count
            for r in self._results
        ]
        if any(v > 0 for v in muse_ops):
            plots["muse_ops_per_run"] = self._plot_bar(
                {r.run_id: ops for r, ops in zip(self._results, muse_ops) if ops > 0},
                "MUSE Operations per Run", "Run", "Operations",
            )

        return plots

    def _plot_histogram(self, values: list[float], title: str, xlabel: str, ylabel: str, color: str = "#6366f1") -> str:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.hist(values, bins=min(30, max(5, len(values) // 3)), color=color, edgecolor="#0a0a0f", alpha=0.85)
        ax.set_title(title, fontsize=14, fontweight="bold", color="white")
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(axis="y", alpha=0.15)
        plt.tight_layout()
        return self._fig_to_base64(fig)

    def _plot_bar(self, data: dict[str, int], title: str, xlabel: str, ylabel: str) -> str:
        fig, ax = plt.subplots(figsize=(8, 4))
        colors_map = {
            "success": "#22c55e", "maestro_error": "#f85149", "orpheus_error": "#f59e0b",
            "timeout": "#6a6a7a", "muse_error": "#a855f7", "prompt_error": "#f59e0b",
        }
        keys = list(data.keys())
        vals = list(data.values())
        bar_colors = [colors_map.get(k, "#6366f1") for k in keys]
        ax.bar(keys, vals, color=bar_colors, edgecolor="#0a0a0f")
        ax.set_title(title, fontsize=14, fontweight="bold", color="white")
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(axis="y", alpha=0.15)
        plt.xticks(rotation=30, ha="right")
        plt.tight_layout()
        return self._fig_to_base64(fig)

    def _plot_line(self, x: list, y: list, title: str, xlabel: str, ylabel: str) -> str:
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(x, y, color="#818cf8", linewidth=2, alpha=0.9)
        ax.scatter(x, y, color="#a855f7", s=20, alpha=0.8, zorder=5)
        ax.fill_between(x, y, alpha=0.08, color="#6366f1")
        ax.set_title(title, fontsize=14, fontweight="bold", color="white")
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(axis="y", alpha=0.15)
        plt.tight_layout()
        return self._fig_to_base64(fig)

    def _fig_to_base64(self, fig: Any) -> str:
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode()
        return f"data:image/png;base64,{b64}"

    def _load_graph(self) -> dict[str, Any] | None:
        graph_file = self._output / "muse" / "graph.json"
        if graph_file.exists():
            data: dict[str, Any] = json.loads(graph_file.read_text())
            return data
        return None

    def _build_html(
        self,
        kpis: dict,
        plots: dict[str, str],
        graph_ascii: str,
        graph_mermaid: str = "",
        graph_metrics: dict[str, Any] | None = None,
    ) -> Path:
        """Build the hero HTML report with Stori design system."""
        best = self._analyzer.find_best_run()
        worst = self._analyzer.find_worst_run()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        dur_stats = kpis.get("duration_stats", {})
        quality_stats = kpis.get("quality_score_stats", {})
        orpheus_stats = kpis.get("orpheus_latency_stats", {})
        note_stats = kpis.get("note_count_stats", {})

        # Inline plots
        plot_html = ""
        for name, data_uri in plots.items():
            nice_name = name.replace("_", " ").title()
            plot_html += f"""
            <div class="plot-card">
                <h3>{nice_name}</h3>
                <img src="{data_uri}" alt="{nice_name}" />
            </div>
            """

        # Graph metrics section
        graph_metrics_html = ""
        if graph_metrics:
            graph_metrics_html = f"""
            <div class="kpi-grid" style="margin-bottom:1.5rem">
              <div class="kpi-card"><div class="kpi-value">{graph_metrics.get('node_count', 0)}</div><div class="kpi-label">DAG Nodes</div></div>
              <div class="kpi-card"><div class="kpi-value">{graph_metrics.get('merge_count', 0)}</div><div class="kpi-label">Merge Commits</div></div>
              <div class="kpi-card"><div class="kpi-value">{graph_metrics.get('max_depth', 0)}</div><div class="kpi-label">Max DAG Depth</div></div>
              <div class="kpi-card"><div class="kpi-value">{graph_metrics.get('branch_head_count', 0)}</div><div class="kpi-label">Leaf Nodes</div></div>
            </div>
            """

        # Scenario info
        scenarios_used = set(r.scenario for r in self._results if r.scenario)
        scenario_html = ", ".join(f'<span class="badge info">{s}</span>' for s in scenarios_used) if scenarios_used else "N/A"

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Stori Tour de Force Report</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #0a0a0f; --bg-secondary: #12121a; --bg-tertiary: #1a1a25;
    --surface: #16161f; --border: rgba(255,255,255,0.08); --border-light: rgba(255,255,255,0.12);
    --text: #ffffff; --text-secondary: #a0a0b0; --text-muted: #6a6a7a;
    --primary: #6366f1; --primary-light: #818cf8; --primary-dark: #4f46e5;
    --accent: #22d3ee; --accent-purple: #a855f7;
    --success: #22c55e; --danger: #f85149; --warning: #f59e0b;
    --gradient-primary: linear-gradient(135deg, #6366f1 0%, #a855f7 50%, #22d3ee 100%);
    --gradient-card: linear-gradient(135deg, rgba(99,102,241,0.1) 0%, rgba(168,85,247,0.05) 100%);
    --shadow-glow: 0 0 40px rgba(99,102,241,0.2);
    --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    --font-mono: 'JetBrains Mono', 'SF Mono', Consolas, monospace;
    --radius-sm: 6px; --radius-md: 12px; --radius-lg: 20px;
  }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family: var(--font-sans); background: var(--bg); color: var(--text-secondary); line-height:1.6; }}
  .container {{ max-width:1280px; margin:0 auto; padding:0 24px; }}

  /* Hero header */
  .hero {{ padding: 60px 0 40px; text-align:center; background: radial-gradient(ellipse at 50% 0%, rgba(99,102,241,0.15) 0%, transparent 60%); }}
  .hero h1 {{ font-size:clamp(2rem,5vw,3.5rem); font-weight:800; color:var(--text); margin-bottom:8px; letter-spacing:-0.03em; }}
  .hero .gradient-text {{ background: var(--gradient-primary); -webkit-background-clip:text; -webkit-text-fill-color:transparent; background-clip:text; }}
  .hero .subtitle {{ font-size:1.1rem; color:var(--text-muted); margin-bottom:8px; }}
  .hero .timestamp {{ font-family:var(--font-mono); font-size:0.8rem; color:var(--text-muted); }}
  .hero .scenario-tags {{ margin-top:16px; display:flex; gap:8px; justify-content:center; flex-wrap:wrap; }}

  /* Section headers */
  .section-label {{ display:inline-block; font-size:0.75rem; font-weight:600; letter-spacing:0.1em; text-transform:uppercase; color:var(--primary-light); margin-bottom:12px; }}
  .section-title {{ font-size:clamp(1.3rem,3vw,1.8rem); font-weight:700; color:var(--text); margin-bottom:24px; letter-spacing:-0.02em; }}
  section {{ padding:40px 0; }}
  section + section {{ border-top:1px solid var(--border); }}

  /* KPI cards */
  .kpi-grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(180px,1fr)); gap:16px; }}
  .kpi-card {{ background: var(--surface); border:1px solid var(--border); border-radius:var(--radius-md); padding:20px; text-align:center; transition:all 0.25s ease; }}
  .kpi-card:hover {{ border-color:var(--primary); transform:translateY(-2px); box-shadow: var(--shadow-glow); }}
  .kpi-value {{ font-size:1.8rem; font-weight:700; color:var(--primary-light); font-family:var(--font-mono); line-height:1.2; }}
  .kpi-value.success {{ color:var(--success); }}
  .kpi-value.danger {{ color:var(--danger); }}
  .kpi-value.accent {{ color:var(--accent); }}
  .kpi-value.purple {{ color:var(--accent-purple); }}
  .kpi-value.warning {{ color:var(--warning); }}
  .kpi-label {{ color:var(--text-muted); font-size:0.8rem; font-weight:500; margin-top:6px; text-transform:uppercase; letter-spacing:0.05em; }}

  /* Plots */
  .plots-grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(380px,1fr)); gap:20px; }}
  .plot-card {{ background:var(--surface); border:1px solid var(--border); border-radius:var(--radius-md); padding:16px; }}
  .plot-card h3 {{ font-size:0.9rem; color:var(--text-muted); margin-bottom:12px; font-weight:500; }}
  .plot-card img {{ width:100%; border-radius:var(--radius-sm); }}

  /* Run cards */
  .run-card {{ background:var(--surface); border:1px solid var(--border); border-radius:var(--radius-md); padding:24px; margin-bottom:16px; }}
  .run-card.best {{ border-color:var(--success); background: linear-gradient(135deg, rgba(34,197,94,0.05), transparent); }}
  .run-card.worst {{ border-color:var(--danger); background: linear-gradient(135deg, rgba(248,81,73,0.05), transparent); }}
  .run-card h3 {{ font-size:1.1rem; color:var(--text); margin-bottom:8px; }}
  .run-card p {{ font-size:0.9rem; color:var(--text-secondary); margin-bottom:4px; }}
  .run-card .mono {{ font-family:var(--font-mono); font-size:0.85rem; }}

  /* Tables */
  table {{ width:100%; border-collapse:collapse; margin:16px 0; font-size:0.88rem; }}
  thead {{ background:var(--bg-tertiary); }}
  th {{ padding:10px 14px; text-align:left; color:var(--text-muted); font-weight:600; font-size:0.75rem; text-transform:uppercase; letter-spacing:0.05em; border-bottom:1px solid var(--border-light); }}
  td {{ padding:10px 14px; border-bottom:1px solid var(--border); color:var(--text-secondary); }}
  tr:hover td {{ background: rgba(99,102,241,0.03); }}

  /* Badges */
  .badge {{ display:inline-block; padding:3px 10px; border-radius:20px; font-size:0.7rem; font-weight:600; letter-spacing:0.02em; }}
  .badge.success {{ background:rgba(34,197,94,0.15); color:var(--success); }}
  .badge.danger {{ background:rgba(248,81,73,0.15); color:var(--danger); }}
  .badge.info {{ background:rgba(99,102,241,0.15); color:var(--primary-light); }}
  .badge.warning {{ background:rgba(245,158,11,0.15); color:var(--warning); }}
  .badge.purple {{ background:rgba(168,85,247,0.15); color:var(--accent-purple); }}

  /* Pre / Mermaid */
  pre {{ background:var(--bg-tertiary); border:1px solid var(--border); border-radius:var(--radius-md); padding:16px; overflow-x:auto; font-size:0.82rem; font-family:var(--font-mono); color:var(--text-muted); }}
  pre.mermaid {{ background:var(--surface); border:1px solid var(--border); border-radius:var(--radius-lg); padding:32px; text-align:center; }}

  /* Stats grid */
  .stats-grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(280px,1fr)); gap:20px; }}
  .stats-card {{ background:var(--surface); border:1px solid var(--border); border-radius:var(--radius-md); padding:20px; }}
  .stats-card h3 {{ font-size:0.9rem; color:var(--text); margin-bottom:12px; font-weight:600; }}
  .stats-card table {{ margin:0; font-size:0.85rem; }}
  .stats-card td:last-child {{ font-family:var(--font-mono); text-align:right; color:var(--primary-light); }}

  /* Details / summary */
  details {{ margin:8px 0; }}
  summary {{ cursor:pointer; font-size:0.85rem; color:var(--text-muted); padding:8px 0; font-weight:500; }}
  summary:hover {{ color:var(--primary-light); }}

  /* Two-col layout */
  .two-col {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; }}
  @media(max-width:768px) {{ .two-col {{ grid-template-columns:1fr; }} }}

  /* Footer */
  .footer {{ padding:40px 0 60px; text-align:center; }}
  .footer p {{ font-size:0.8rem; color:var(--text-muted); }}
  .footer .logo {{ font-weight:700; color:var(--text); }}
</style>
</head>
<body>

<div class="hero">
  <div class="container">
    <h1><span class="gradient-text">Tour de Force</span></h1>
    <p class="subtitle">Maestro &times; Orpheus &times; MUSE &mdash; End-to-End Integration Report</p>
    <p class="timestamp">{now}</p>
    <div class="scenario-tags">
      {scenario_html}
    </div>
  </div>
</div>

<div class="container">

<!-- ── KPIs ─────────────────────────────────────────────────────── -->
<section>
  <div class="section-label">Performance</div>
  <div class="section-title">Key Metrics</div>
  <div class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-value">{_fmt_num(kpis['total_runs'])}</div>
      <div class="kpi-label">Total Runs</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value success">{_fmt_pct(kpis['success_rate'])}</div>
      <div class="kpi-label">Success Rate</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value accent">{_fmt_ms(dur_stats.get('median', 0))}</div>
      <div class="kpi-label">Median Duration</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value purple">{quality_stats.get('mean', 0):.1f}</div>
      <div class="kpi-label">Mean Quality</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value">{_fmt_num(kpis['total_midi_notes'])}</div>
      <div class="kpi-label">Total MIDI Notes</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value">{_fmt_num(kpis['total_tool_calls'])}</div>
      <div class="kpi-label">Tool Calls</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value">{_fmt_ms(dur_stats.get('p95', 0))}</div>
      <div class="kpi-label">P95 Duration</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value accent">{_fmt_ms(orpheus_stats.get('median', 0))}</div>
      <div class="kpi-label">Orpheus Median</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value">{_fmt_ms(kpis.get('total_duration_ms', 0))}</div>
      <div class="kpi-label">Total Wall Time</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value purple">{_fmt_num(kpis.get('total_artifacts', 0))}</div>
      <div class="kpi-label">Artifacts</div>
    </div>
  </div>
</section>

<!-- ── MUSE VCS ─────────────────────────────────────────────────── -->
<section>
  <div class="section-label">Version Control</div>
  <div class="section-title">MUSE Permutation Coverage</div>
  <div class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-value">{_fmt_num(kpis['total_muse_commits'])}</div>
      <div class="kpi-label">Commits</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value purple">{_fmt_num(kpis.get('total_muse_branches', 0))}</div>
      <div class="kpi-label">Branches</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value accent">{_fmt_num(kpis['total_muse_merges'])}</div>
      <div class="kpi-label">Merges</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value warning">{_fmt_num(kpis.get('total_muse_conflicts', 0))}</div>
      <div class="kpi-label">Conflicts</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value">{_fmt_num(kpis.get('total_muse_checkouts', 0))}</div>
      <div class="kpi-label">Checkouts</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value danger">{_fmt_num(kpis.get('total_muse_checkout_blocked', 0))}</div>
      <div class="kpi-label">Blocked (drift)</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value">{_fmt_num(kpis.get('total_muse_drift_detected', 0))}</div>
      <div class="kpi-label">Drift Detected</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value success">{_fmt_num(kpis.get('total_muse_force_recoveries', 0))}</div>
      <div class="kpi-label">Force Recoveries</div>
    </div>
  </div>
</section>

<!-- ── MUSE Commit Graph ───────────────────────────────────────── -->
<section>
  <div class="section-label">DAG</div>
  <div class="section-title">MUSE Commit Graph</div>
  {graph_metrics_html}
  {f'<pre class="mermaid">{graph_mermaid}</pre>' if graph_mermaid else ''}
  <details>
    <summary>ASCII fallback</summary>
    <pre>{graph_ascii if graph_ascii else '(no graph data available)'}</pre>
  </details>
</section>

{self._render_failure_table(kpis)}

<!-- ── Visualizations ──────────────────────────────────────────── -->
<section>
  <div class="section-label">Analytics</div>
  <div class="section-title">Visualizations</div>
  <div class="plots-grid">
    {plot_html}
  </div>
</section>

<!-- ── Best / Worst ────────────────────────────────────────────── -->
<section>
  <div class="section-label">Highlights</div>
  <div class="section-title">Run Spotlight</div>
  <div class="two-col">
    {self._render_best_card(best)}
    {self._render_worst_card(worst)}
  </div>
</section>

<!-- ── Detailed Stats ──────────────────────────────────────────── -->
<section>
  <div class="section-label">Deep Dive</div>
  <div class="section-title">Detailed Statistics</div>
  <div class="stats-grid">
    {self._render_stats_card("Duration", dur_stats, fmt_fn=_fmt_ms)}
    {self._render_stats_card("Orpheus Latency", orpheus_stats, fmt_fn=_fmt_ms)}
    {self._render_stats_card("Quality Score", quality_stats)}
    {self._render_stats_card("Note Count", note_stats, fmt_fn=_fmt_num)}
  </div>
</section>

<!-- ── Artifacts ────────────────────────────────────────────────── -->
{self._render_artifact_section(kpis)}

<!-- ── All Runs ────────────────────────────────────────────────── -->
<section>
  <div class="section-label">Runs</div>
  <div class="section-title">All Runs</div>
  {self._render_runs_table()}
</section>

</div><!-- /container -->

<div class="footer">
  <div class="container">
    <p><span class="logo">stori_tourdeforce</span> v{__version__}</p>
    <p style="margin-top:4px">Artifacts: <code style="font-family:var(--font-mono);font-size:0.75rem;color:var(--primary-light)">{self._output}</code></p>
  </div>
</div>

<script type="module">
  import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
  mermaid.initialize({{ startOnLoad: true, theme: 'dark', themeVariables: {{
    primaryColor: '#6366f1', primaryTextColor: '#ffffff',
    primaryBorderColor: 'rgba(255,255,255,0.12)', lineColor: '#818cf8',
    secondaryColor: '#16161f', tertiaryColor: '#0a0a0f',
    nodeTextColor: '#ffffff', edgeLabelBackground: '#16161f',
    clusterBkg: '#1a1a25', clusterBorder: 'rgba(255,255,255,0.08)'
  }} }});
</script>
</body>
</html>"""

        path = self._report_dir / "report.html"
        path.write_text(html)
        return path

    def _render_failure_table(self, kpis: dict) -> str:
        breakdown = kpis.get("failure_breakdown", {})
        if not breakdown:
            return ""
        rows = "".join(
            f'<tr><td><span class="badge danger">{k}</span></td><td style="font-family:var(--font-mono)">{v}</td></tr>'
            for k, v in sorted(breakdown.items())
        )
        return f"""
        <section>
          <div class="section-label">Errors</div>
          <div class="section-title">Failure Breakdown</div>
          <table><thead><tr><th>Type</th><th>Count</th></tr></thead>
          <tbody>{rows}</tbody></table>
        </section>
        """

    def _render_best_card(self, best: RunResult | None) -> str:
        if not best:
            return '<div class="run-card"><h3>No runs completed</h3></div>'
        quality = best.midi_metrics.get("quality_score", 0) if best.midi_metrics else 0
        notes = _fmt_num(best.orpheus_note_count)
        duration = _fmt_ms(best.duration_ms)
        prompt_preview = (best.prompt.text[:150] + "...") if best.prompt and len(best.prompt.text) > 150 else (best.prompt.text if best.prompt else "N/A")
        return f"""
        <div class="run-card best">
          <h3><span class="badge success">Best</span> {best.run_id}</h3>
          <p class="mono">Quality: {quality:.1f} &bull; Notes: {notes} &bull; Duration: {duration}</p>
          <p>Intent: <span class="badge info">{best.intent}</span></p>
          <details><summary>Prompt</summary><p style="font-size:0.85rem;margin-top:8px;color:var(--text-muted)">{prompt_preview}</p></details>
        </div>
        """

    def _render_worst_card(self, worst: RunResult | None) -> str:
        if not worst:
            return '<div class="run-card"><h3>No runs</h3></div>'
        badge = "danger" if worst.status != RunStatus.SUCCESS else "warning"
        duration = _fmt_ms(worst.duration_ms)
        error = worst.error_message[:200] if worst.error_message else "Low quality score"
        return f"""
        <div class="run-card worst">
          <h3><span class="badge {badge}">Worst</span> {worst.run_id}</h3>
          <p class="mono">Status: {worst.status.value} &bull; Duration: {duration}</p>
          <p style="font-size:0.85rem;color:var(--text-muted)">Error: {error}</p>
        </div>
        """

    def _render_stats_card(self, title: str, stats: dict, fmt_fn: Any = None) -> str:
        if not stats:
            return f'<div class="stats-card"><h3>{title}</h3><p style="color:var(--text-muted)">No data</p></div>'

        def _fmt(k: str, v: Any) -> str:
            if fmt_fn and k != "count":
                return fmt_fn(v) if callable(fmt_fn) else str(v)
            if isinstance(v, float):
                return f"{v:,.2f}"
            return _fmt_num(v)

        rows = "".join(f"<tr><td>{k}</td><td>{_fmt(k, v)}</td></tr>" for k, v in stats.items())
        return f"""
        <div class="stats-card">
          <h3>{title}</h3>
          <table><tbody>{rows}</tbody></table>
        </div>
        """

    def _render_artifact_section(self, kpis: dict) -> str:
        total = kpis.get("total_artifacts", 0)
        breakdown = kpis.get("artifact_breakdown", {})
        if total == 0 and not breakdown:
            return ""

        cards = ""
        icon_map = {"mid": "MIDI", "wav": "WAV", "mp3": "MP3", "png": "Plot", "webp": "Plot"}
        for ext, count in sorted(breakdown.items()):
            label = icon_map.get(ext, ext.upper())
            cards += f'<div class="kpi-card"><div class="kpi-value purple">{_fmt_num(count)}</div><div class="kpi-label">{label} Files</div></div>\n'

        return f"""
        <section>
          <div class="section-label">Assets</div>
          <div class="section-title">Artifact Inventory</div>
          <div class="kpi-grid">
            <div class="kpi-card"><div class="kpi-value accent">{_fmt_num(total)}</div><div class="kpi-label">Total Files</div></div>
            {cards}
          </div>
        </section>
        """

    def _render_runs_table(self) -> str:
        rows = ""
        for r in self._results:
            badge = "success" if r.status == RunStatus.SUCCESS else "danger"
            quality = r.midi_metrics.get("quality_score", 0) if r.midi_metrics else 0
            rows += f"""<tr>
                <td class="mono">{r.run_id}</td>
                <td><span class="badge {badge}">{r.status.value}</span></td>
                <td class="mono">{_fmt_num(r.orpheus_note_count)}</td>
                <td class="mono">{quality:.1f}</td>
                <td class="mono">{_fmt_ms(r.duration_ms)}</td>
                <td><span class="badge info">{r.intent or '—'}</span></td>
                <td class="mono">{len(r.muse_commit_ids)}</td>
                <td class="mono">{len(r.muse_merge_ids)}</td>
                <td class="mono">{r.muse_conflict_count}</td>
                <td class="mono">{r.muse_checkout_count}</td>
            </tr>"""
        return f"""
        <div style="overflow-x:auto">
        <table>
        <thead><tr>
            <th>Run</th><th>Status</th><th>Notes</th><th>Quality</th><th>Duration</th>
            <th>Intent</th><th>Commits</th><th>Merges</th><th>Conflicts</th><th>Checkouts</th>
        </tr></thead>
        <tbody>{rows}</tbody>
        </table>
        </div>
        """

    def _build_markdown(self, kpis: dict, graph_ascii: str) -> Path:
        """Build a Markdown report."""
        best = self._analyzer.find_best_run()
        worst = self._analyzer.find_worst_run()
        dur_stats = kpis.get("duration_stats", {})

        md = f"""# Stori Tour de Force Report

## KPIs

| Metric | Value |
|--------|-------|
| Total Runs | {_fmt_num(kpis['total_runs'])} |
| Success Rate | {_fmt_pct(kpis['success_rate'])} |
| Median Duration | {_fmt_ms(dur_stats.get('median', 0))} |
| Mean Quality | {kpis.get('quality_score_stats', {}).get('mean', 0):.1f} |
| Total Notes | {_fmt_num(kpis['total_midi_notes'])} |
| Tool Calls | {_fmt_num(kpis['total_tool_calls'])} |
| MUSE Commits | {_fmt_num(kpis['total_muse_commits'])} |
| MUSE Merges | {_fmt_num(kpis['total_muse_merges'])} |
| MUSE Branches | {_fmt_num(kpis.get('total_muse_branches', 0))} |
| MUSE Conflicts | {_fmt_num(kpis.get('total_muse_conflicts', 0))} |
| MUSE Checkouts | {_fmt_num(kpis.get('total_muse_checkouts', 0))} |
| Checkouts Blocked | {_fmt_num(kpis.get('total_muse_checkout_blocked', 0))} |
| Drift Detections | {_fmt_num(kpis.get('total_muse_drift_detected', 0))} |
| Force Recoveries | {_fmt_num(kpis.get('total_muse_force_recoveries', 0))} |

## Best Run

- **ID:** {best.run_id if best else 'N/A'}
- **Quality:** {best.midi_metrics.get('quality_score', 0) if best and best.midi_metrics else 0:.1f}
- **Notes:** {_fmt_num(best.orpheus_note_count) if best else '0'}
- **Duration:** {_fmt_ms(best.duration_ms) if best else '—'}

## Worst Run

- **ID:** {worst.run_id if worst else 'N/A'}
- **Status:** {worst.status.value if worst else 'N/A'}
- **Error:** {worst.error_message[:200] if worst and worst.error_message else 'N/A'}

## MUSE Commit Graph

```
{graph_ascii or '(no graph data)'}
```

## Failure Breakdown

| Type | Count |
|------|-------|
"""
        for k, v in sorted(kpis.get("failure_breakdown", {}).items()):
            md += f"| {k} | {v} |\n"

        md += f"\n---\n*Generated by stori_tourdeforce v{__version__}*\n"

        path = self._report_dir / "report.md"
        path.write_text(md)
        return path
