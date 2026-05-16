"""Verify L3 benchmark report thoroughly."""
import json
import sys

sys.stdout.reconfigure(encoding='utf-8')

r = json.load(open('l3_report.json'))

print("=" * 60)
print("L3 BENCHMARK VERIFICATION REPORT")
print("=" * 60)

print(f"\nL3 Version: {r.get('l3_version')}")
print(f"Timestamp: {r.get('timestamp')}")
print(f"Adapter: {r.get('adapter')}")
print(f"Adapter SHA-256: {r.get('adapter_sha256', 'N/A')[:16]}...")
print(f"Seeds: {r.get('seeds')}")
print(f"Mode: {r.get('mode')}")

print("\n" + "=" * 60)
print("AGGREGATED METRICS (across all seeds)")
print("=" * 60)
agg = r['aggregated']
print(f"  recall@5:          {agg['recall@5']:.4f}")
print(f"  precision@5_mean:  {agg['precision@5_mean']:.4f}")
print(f"  remediation_acc:   {agg['remediation_acc']:.4f}")
print(f"  latency_p95_ms:    {agg['latency_p95_ms']:.2f}")
print(f"  latency_mean_ms:   {agg['latency_mean_ms']:.2f}")
print(f"  n_seeds:           {agg['n_seeds']}")
print(f"  n_signals_total:   {agg['n_signals_total']}")

print("\n" + "=" * 60)
print("WEIGHTED SCORE BREAKDOWN")
print("=" * 60)
score = r['score']
print(f"  recall@5 (w=0.30):         {score['axes']['recall@5']:.4f} x 0.30 = {score['axes']['recall@5']*0.30:.4f}")
print(f"  precision@5 (w=0.15):      {score['axes']['precision@5_mean']:.4f} x 0.15 = {score['axes']['precision@5_mean']*0.15:.4f}")
print(f"  remediation_acc (w=0.20):  {score['axes']['remediation_acc']:.4f} x 0.20 = {score['axes']['remediation_acc']*0.20:.4f}")
print(f"  latency_p95 (w=0.15):      {score['axes']['latency_p95_ms']:.4f} x 0.15 = {score['axes']['latency_p95_ms']*0.15:.4f}")
print(f"  manual_context (w=0.10):   (panel-graded)")
print(f"  manual_explain (w=0.10):   (panel-graded)")
print(f"  -------------------------------------------")
print(f"  WEIGHTED AUTOMATED:        {score['weighted_score']:.4f} / {score['max_automated']:.4f}")
print(f"  PERCENTAGE:                {score['weighted_score']/score['max_automated']*100:.1f}%")

print("\n" + "=" * 60)
print("PER-SEED BREAKDOWN")
print("=" * 60)
for ps in r['per_seed']:
    s = ps['summary']
    print(f"\nSeed {ps['seed']}:")
    print(f"  n_train={ps['n_train']}, n_eval={ps['n_eval']}, n_signals={ps['n_signals']}")
    print(f"  recall@5={s['recall@5']:.3f}, precision@5={s['precision@5_mean']:.3f}")
    print(f"  remediation_acc={s['remediation_acc']:.3f}, latency_p95={s['latency_p95_ms']:.2f}ms")
    
    # Count decoys vs real
    incidents = ps['per_incident']
    decoys = [i for i in incidents if 'DEC-' in i['incident_id']]
    real = [i for i in incidents if 'DEC-' not in i['incident_id']]
    
    decoy_recall = sum(1 for d in decoys if d['correct_family_in_top_k'])
    decoy_rem = sum(1 for d in decoys if d['remediation_matches'])
    real_recall = sum(1 for r in real if r['correct_family_in_top_k'])
    real_rem = sum(1 for r in real if r['remediation_matches'])
    
    print(f"  Decoys: {len(decoys)} total, {decoy_recall}/{len(decoys)} recall, {decoy_rem}/{len(decoys)} remediation")
    print(f"  Real:   {len(real)} total, {real_recall}/{len(real)} recall, {real_rem}/{len(real)} remediation")

print("\n" + "=" * 60)
print("VERIFICATION SUMMARY")
print("=" * 60)
print(f"  L3 Version matches expected: {'anvil-2026-p02-L3-final' in r.get('l3_version', '')}")
print(f"  All 5 seeds processed: {len(r['per_seed']) == 5}")
print(f"  Total signals: {agg['n_signals_total']} (expected: 125)")
print(f"  Remediation perfect: {agg['remediation_acc'] == 1.0}")
print(f"  Latency under budget: {agg['latency_p95_ms'] < 2000}")
print(f"\n  FINAL SCORE: {score['weighted_score']:.4f} / {score['max_automated']:.4f} ({score['weighted_score']/score['max_automated']*100:.1f}%)")
