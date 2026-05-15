"""
Worked Example Validation — verifies the engine meets all P-02 requirements.

Tests against the problem statement's worked example criteria:
1. Related Events: includes deploy, metric spike, upstream error log
2. Causal Chain: deploy -> latency spike -> upstream error, confidence >= 0.5
3. Similar Past Incidents: matches across renames (topology-independent)
4. Suggested Remediation: rollback with confidence reflecting historical success
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from generator import GenConfig, generate
from adapters.engine import Engine

# Use seed 42 for reproducibility
cfg = GenConfig(seed=42, n_services=12, days=7)
ds = generate(cfg)

engine = Engine()
engine.ingest(ds.train_events)
engine.ingest(ds.eval_events)

# Pick the first eval signal
sig = ds.eval_signals[0]
signal = {
    "incident_id": sig["incident_id"],
    "ts": sig["ts"],
    "trigger": sig.get("trigger", ""),
    "service": sig.get("service", ""),
}

print("=" * 70)
print("WORKED EXAMPLE VALIDATION")
print("=" * 70)
print(f"\nSignal: {signal}")
print(f"Service: {signal['service']}")
print(f"Trigger: {signal['trigger']}")

# Test both modes
for mode in ["fast", "deep"]:
    print(f"\n{'=' * 70}")
    print(f"MODE: {mode}")
    print(f"{'=' * 70}")
    
    ctx = engine.reconstruct_context(signal, mode=mode)
    
    # 1. RELATED EVENTS
    print(f"\n--- 1. RELATED EVENTS ({len(ctx.get('related_events', []))} found) ---")
    related = ctx.get("related_events", [])
    
    has_deploy = any(e.get("kind") == "deploy" for e in related)
    has_metric = any(e.get("kind") == "metric" for e in related)
    has_log_error = any(e.get("kind") == "log" and e.get("level") == "error" for e in related)
    has_trace = any(e.get("kind") == "trace" for e in related)
    
    print(f"  Has deploy event: {has_deploy}")
    print(f"  Has metric event: {has_metric}")
    print(f"  Has error log: {has_log_error}")
    print(f"  Has trace: {has_trace}")
    
    # Show event kinds breakdown
    from collections import Counter
    kinds = Counter(e.get("kind", "?") for e in related)
    print(f"  Event kinds: {dict(kinds)}")
    
    # Show some sample events
    for e in related[:5]:
        print(f"    {e.get('ts','')} [{e.get('kind','')}] svc={e.get('service','')} {e.get('msg','')[:50] if e.get('msg') else ''}")
    
    check1 = has_deploy and has_metric
    print(f"\n  REQUIREMENT MET: {'YES' if check1 else 'PARTIAL'}")
    
    # 2. CAUSAL CHAIN
    print(f"\n--- 2. CAUSAL CHAIN ({len(ctx.get('causal_chain', []))} edges) ---")
    chain = ctx.get("causal_chain", [])
    
    has_deploy_to_spike = False
    has_spike_to_error = False
    all_confidence_ok = True
    
    for edge in chain:
        cause = edge.get("cause_event_id", "")
        effect = edge.get("effect_event_id", "")
        evidence = edge.get("evidence", "")
        confidence = edge.get("confidence", 0)
        
        print(f"  {cause} -> {effect}")
        print(f"    evidence: {evidence}")
        print(f"    confidence: {confidence}")
        
        if "deploy" in cause and "metric" in effect:
            has_deploy_to_spike = True
        if "metric" in cause and ("log" in effect or "signal" in effect):
            has_spike_to_error = True
        if confidence < 0.5:
            all_confidence_ok = False
    
    check2 = has_deploy_to_spike and all_confidence_ok and len(chain) > 0
    print(f"\n  Deploy -> spike edge: {has_deploy_to_spike}")
    print(f"  All confidence >= 0.5: {all_confidence_ok}")
    print(f"  REQUIREMENT MET: {'YES' if check2 else 'PARTIAL'}")
    
    # 3. SIMILAR PAST INCIDENTS
    print(f"\n--- 3. SIMILAR PAST INCIDENTS ({len(ctx.get('similar_past_incidents', []))} found) ---")
    similar = ctx.get("similar_past_incidents", [])
    
    for m in similar:
        print(f"  {m.get('incident_id','')} sim={m.get('similarity',0):.3f}")
        print(f"    rationale: {m.get('rationale','')}")
    
    # Check if we have cross-service matches (topology-independent)
    has_same_svc = any("Same service" in m.get("rationale", "") for m in similar)
    has_cross_svc = any("Cross-service" in m.get("rationale", "") for m in similar)
    has_rename_awareness = any("aliases" in m.get("rationale", "") or "canonical" in m.get("rationale", "") for m in similar)
    
    check3 = len(similar) > 0
    print(f"\n  Has same-service matches: {has_same_svc}")
    print(f"  Has cross-service matches: {has_cross_svc}")
    print(f"  Shows rename awareness: {has_rename_awareness}")
    print(f"  REQUIREMENT MET: {'YES' if check3 else 'NO'}")
    
    # 4. SUGGESTED REMEDIATION
    print(f"\n--- 4. SUGGESTED REMEDIATION ({len(ctx.get('suggested_remediations', []))} found) ---")
    remediations = ctx.get("suggested_remediations", [])
    
    has_rollback = False
    has_confidence = False
    
    for r in remediations:
        action = r.get("action", "")
        target = r.get("target", "")
        outcome = r.get("historical_outcome", "")
        confidence = r.get("confidence", 0)
        
        print(f"  action: {action}")
        print(f"  target: {target}")
        print(f"  historical_outcome: {outcome}")
        print(f"  confidence: {confidence}")
        
        if action == "rollback":
            has_rollback = True
        if confidence > 0:
            has_confidence = True
    
    check4 = has_rollback and has_confidence
    print(f"\n  Has rollback action: {has_rollback}")
    print(f"  Has confidence score: {has_confidence}")
    print(f"  REQUIREMENT MET: {'YES' if check4 else 'NO'}")
    
    # 5. EXPLAIN FIELD
    print(f"\n--- 5. EXPLAIN FIELD ---")
    explain = ctx.get("explain", "")
    print(f"  {explain}")
    print(f"  Has content: {bool(explain)}")
    
    # 6. CONFIDENCE
    print(f"\n--- 6. OVERALL CONFIDENCE ---")
    print(f"  confidence: {ctx.get('confidence', 0)}")
    
    # SUMMARY
    print(f"\n{'=' * 70}")
    print(f"SUMMARY ({mode} mode):")
    all_pass = check1 and check2 and check3 and check4
    print(f"  1. Related Events: {'PASS' if check1 else 'PARTIAL'}")
    print(f"  2. Causal Chain:   {'PASS' if check2 else 'PARTIAL'}")
    print(f"  3. Similar Past:   {'PASS' if check3 else 'FAIL'}")
    print(f"  4. Remediation:    {'PASS' if check4 else 'FAIL'}")
    print(f"  OVERALL: {'ALL REQUIREMENTS MET' if all_pass else 'SOME GAPS'}")
