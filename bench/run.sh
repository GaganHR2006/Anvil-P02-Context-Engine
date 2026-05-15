#!/usr/bin/env bash
# bench/run.sh — Canonical benchmark runner for Anvil P-02
# Ingests the published sample, runs the canonical scenario, emits JSON report.
#
# Usage:
#   ./bench/run.sh                    # Default: 5-seed fast mode
#   ./bench/run.sh --mode deep        # Deep mode evaluation
#   ./bench/run.sh --seeds 42 101     # Custom seeds
#   ./bench/run.sh --quick            # Quick 2-seed check

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

ADAPTER="adapters.engine:Engine"
MODE="fast"
SEEDS=""
QUICK=""
OUTPUT="report.json"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --mode)
            MODE="$2"
            shift 2
            ;;
        --seeds)
            shift
            while [[ $# -gt 0 && ! "$1" =~ ^-- ]]; do
                SEEDS="$SEEDS $1"
                shift
            done
            ;;
        --quick)
            QUICK="--quick"
            shift
            ;;
        --output)
            OUTPUT="$2"
            shift 2
            ;;
        --adapter)
            ADAPTER="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

echo "============================================================"
echo "  ANVIL P-02 | Persistent Context Engine | Benchmark Runner"
echo "============================================================"
echo ""
echo "  Adapter:  $ADAPTER"
echo "  Mode:     $MODE"
echo "  Output:   $OUTPUT"
echo ""

# Build the command
CMD="python run.py --adapter $ADAPTER --mode $MODE"

if [[ -n "$SEEDS" ]]; then
    CMD="$CMD --seeds $SEEDS"
fi

if [[ -n "$QUICK" ]]; then
    # Quick mode: use self_check with --quick flag
    CMD="python self_check.py --adapter $ADAPTER --quick"
fi

echo "  Running:  $CMD"
echo ""
echo "------------------------------------------------------------"

# Execute and capture output
$CMD 2>&1 | tee /dev/stderr

# Also run with JSON output for report
python -c "
import json
import sys
sys.path.insert(0, '.')
from harness import run, compute_score

# Import adapter
spec = '$ADAPTER'
module_path, class_name = spec.rsplit(':', 1)
import importlib
mod = importlib.import_module(module_path)
factory = getattr(mod, class_name)

# Run benchmark
seeds = [int(s) for s in '$SEEDS'.split()] if '$SEEDS'.strip() else None
summary = run(factory, seeds=seeds, mode='$MODE')
score = compute_score(summary, '$MODE')

# Combine into report
report = {
    'adapter': '$ADAPTER',
    'mode': '$MODE',
    'seeds': summary.get('seeds', []),
    'metrics': {
        'recall_at_5': score.get('recall@5', 0),
        'precision_at_5_mean': score.get('precision@5_mean', 0),
        'remediation_acc': score.get('remediation_acc', 0),
        'latency_p95_ms': score.get('latency_p95_ms', 0),
        'latency_mean_ms': summary.get('latency_mean_ms', 0),
    },
    'weighted_automated': score.get('weighted', 0),
    'max_possible': 0.80,
    'pass': score.get('recall@5', 0) >= 0.8 and score.get('remediation_acc', 0) >= 0.8,
}

with open('$OUTPUT', 'w') as f:
    json.dump(report, f, indent=2)

print()
print('============================================================')
print(f'  Report written to: $OUTPUT')
print(f'  Weighted Score:    {report[\"weighted_automated\"]:.3f} / {report[\"max_possible\"]}')
print(f'  PASS:              {report[\"pass\"]}')
print('============================================================')
"

echo ""
echo "Done."
