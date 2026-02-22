#!/bin/bash
set -euo pipefail

BUCKET="stori-assets-992382692655-useast2"
REGION="us-east-2"
WORKDIR="/opt/midi-analysis"
RESULTS_DIR="$WORKDIR/results"
LOG="/var/log/midi-analysis.log"

exec > >(tee -a "$LOG") 2>&1
echo "=== MIDI Analysis Bootstrap — $(date) ==="
echo "Instance: $(curl -s http://169.254.169.254/latest/meta-data/instance-type)"
CPUS=$(nproc)
echo "CPUs: $CPUS"
echo "Memory: $(free -h | grep Mem | awk '{print $2}')"

yum install -y python3 python3-pip tar gzip tmux
pip3 install mido awscli

mkdir -p "$WORKDIR" "$RESULTS_DIR"
cd "$WORKDIR"

echo "=== Downloading from S3 — $(date) ==="
aws s3 cp "s3://$BUCKET/midi-analysis/midi_data.tar.gz" midi_data.tar.gz --region "$REGION"
aws s3 cp "s3://$BUCKET/midi-analysis/analyze_midi.py" analyze_midi.py --region "$REGION"

echo "=== Extracting — $(date) ==="
tar xzf midi_data.tar.gz
rm midi_data.tar.gz
TOTAL_FILES=$(find . -name '*.mid' -o -name '*.midi' | wc -l)
echo "MIDI files: $TOTAL_FILES"

# ── Main analysis script ────────────────────────────────────────────────
cat > run_analysis.py << 'PYEOF'
#!/usr/bin/env python3
"""Single-process, multi-worker analysis. Saturates all CPUs via ProcessPoolExecutor."""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

sys.path.insert(0, "/opt/midi-analysis")
from analyze_midi import analyze_midi, aggregate_reports, SCHEMA_VERSION

WORKDIR = Path("/opt/midi-analysis")
RESULTS = WORKDIR / "results"
BUCKET = "stori-assets-992382692655-useast2"
REGION = "us-east-2"


def process_one(midi_path: str) -> Optional[dict]:
    try:
        return analyze_midi(midi_path)
    except Exception as e:
        return {"error": str(e), "file": midi_path}


def main():
    sources = ["maestro", "symphonynet", "musicnet", "lakh"]
    all_files: list[str] = []
    for src in sources:
        src_dir = WORKDIR / src
        if not src_dir.is_dir():
            continue
        files = sorted(
            [str(p) for p in src_dir.rglob("*.mid")] +
            [str(p) for p in src_dir.rglob("*.midi")]
        )
        print(f"  {src}: {len(files):,} files")
        all_files.extend(files)

    n_total = len(all_files)
    cpus = os.cpu_count() or 8
    workers = cpus - 4  # leave a few for OS + I/O
    print(f"\nTotal: {n_total:,} files | CPUs: {cpus} | Workers: {workers}")
    print(f"Schema version: {SCHEMA_VERSION}")

    RESULTS.mkdir(parents=True, exist_ok=True)
    reports: list[dict] = []
    errors = 0
    t0 = time.time()

    BATCH = 5000
    for batch_start in range(0, n_total, BATCH):
        batch = all_files[batch_start:batch_start + BATCH]
        batch_num = batch_start // BATCH + 1
        n_batches = (n_total + BATCH - 1) // BATCH
        print(f"\n{'='*60}")
        print(f"  Batch {batch_num}/{n_batches} ({len(batch):,} files)")
        print(f"{'='*60}")

        batch_reports: list[dict] = []
        batch_errors = 0

        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(process_one, f): f for f in batch}
            for i, fut in enumerate(as_completed(futures), 1):
                result = fut.result()
                if result and "error" not in result:
                    batch_reports.append(result)
                else:
                    batch_errors += 1

                if i % 500 == 0 or i == len(batch):
                    elapsed = time.time() - t0
                    done = len(reports) + len(batch_reports) + errors + batch_errors
                    rate = done / elapsed if elapsed > 0 else 0
                    eta = (n_total - done) / rate / 60 if rate > 0 else 0
                    print(f"  {done:>7,}/{n_total:,}  "
                          f"{rate:>5.0f}/s  "
                          f"ETA {eta:>5.1f}m  "
                          f"err={errors + batch_errors}")

        reports.extend(batch_reports)
        errors += batch_errors

        # Checkpoint to disk
        cp = RESULTS / f"checkpoint_{batch_num:04d}.json"
        cp.write_text(json.dumps(batch_reports))
        mb = cp.stat().st_size / 1024 / 1024
        print(f"  Checkpoint saved: {cp.name} ({mb:.1f} MB, {len(batch_reports):,} reports)")

    elapsed = time.time() - t0
    rate = (len(reports) + errors) / elapsed if elapsed > 0 else 0
    print(f"\n{'='*60}")
    print(f"  ANALYSIS COMPLETE")
    print(f"  {len(reports):,} reports | {errors:,} errors | {elapsed:.0f}s ({elapsed/60:.1f}m)")
    print(f"  Average: {rate:.0f} files/sec")
    print(f"{'='*60}")

    # Save all reports
    print("\nSaving all_reports.json...")
    all_path = RESULTS / "all_reports.json"
    all_path.write_text(json.dumps(reports))
    print(f"  {all_path.stat().st_size / 1024 / 1024:.1f} MB")

    # Aggregate heuristics
    print("Aggregating heuristics...")
    t1 = time.time()
    agg = aggregate_reports(reports)
    print(f"  Aggregation: {time.time() - t1:.1f}s")
    agg_path = RESULTS / "heuristics_v2.json"
    agg_path.write_text(json.dumps(agg, indent=2))
    print(f"  {agg_path.stat().st_size / 1024:.1f} KB")

    # Upload to S3
    print("\nUploading results to S3...")
    os.system(f'aws s3 cp {agg_path} s3://{BUCKET}/midi-analysis/results/heuristics_v2.json --region {REGION}')
    os.system(f'aws s3 cp {all_path} s3://{BUCKET}/midi-analysis/results/all_reports.json --region {REGION}')
    os.system(f'aws s3 cp /var/log/midi-analysis.log s3://{BUCKET}/midi-analysis/results/bootstrap.log --region {REGION}')

    # Signal done
    done_file = RESULTS / "DONE"
    done_file.write_text(
        f"completed: {time.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        f"reports: {len(reports):,}\n"
        f"errors: {errors:,}\n"
        f"elapsed: {elapsed:.0f}s\n"
        f"rate: {rate:.0f} files/sec\n"
    )
    os.system(f'aws s3 cp {done_file} s3://{BUCKET}/midi-analysis/results/DONE --region {REGION}')
    print("\n=== ALL DONE — results in S3 ===")


if __name__ == "__main__":
    main()
PYEOF

# Launch inside tmux so SSH disconnect won't kill it
echo "=== Launching tmux session 'analysis' — $(date) ==="
tmux new-session -d -s analysis -x 220 -y 50 \
  "python3 /opt/midi-analysis/run_analysis.py 2>&1 | tee /opt/midi-analysis/results/analysis.log; echo 'FINISHED — press enter to exit'; read"

echo "tmux session 'analysis' is running."
echo "Reconnect with: tmux attach -t analysis"
echo "Monitor from outside: tail -f /opt/midi-analysis/results/analysis.log"
echo "=== Bootstrap complete — $(date) ==="
