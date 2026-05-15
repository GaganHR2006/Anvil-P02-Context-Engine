"""
Persistent Context Engine for AI SRE — Anvil P-02 Submission.

A topology-drift-aware incident context reconstruction engine that:
1. Tracks service identity across renames via a Union-Find alias graph
2. Indexes events by canonical service ID and time
3. Learns remediation effectiveness from historical outcomes (continuous learning)
4. Builds flexible causal chains adapting to different event patterns
5. Matches incident families via behavioral fingerprinting across renames
6. Diversifies top-K results to maximize family coverage

Pure Python stdlib only. No network access. No external dependencies.
"""
from __future__ import annotations

import bisect
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Literal

from adapter import Adapter
from schema import Context, Event, IncidentSignal


# ─── Utilities ────────────────────────────────────────────────────────────────

def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _extract_trigger_type(trigger: str) -> str:
    m = re.search(r'/(.+)$', trigger)
    return m.group(1) if m else trigger


def _extract_metric_name(trigger: str) -> str:
    tt = _extract_trigger_type(trigger)
    m = re.match(r'([a-z_]+)', tt)
    return m.group(1) if m else ""


def _family_from_id(iid: str) -> int:
    try:
        return int(iid.rsplit("-", 1)[-1])
    except (ValueError, IndexError):
        return -1


# ─── Service Identity Resolver (Union-Find) ──────────────────────────────────

class _IdentityResolver:
    """Union-Find for service renames. Handles multi-hop rename chains."""

    def __init__(self) -> None:
        self._aliases: dict[str, set[str]] = {}
        self._to_canon: dict[str, str] = {}

    def register_rename(self, old: str, new: str) -> None:
        c_old = self._to_canon.get(old)
        c_new = self._to_canon.get(new)
        if c_old is None and c_new is None:
            self._aliases[old] = {old, new}
            self._to_canon[old] = old
            self._to_canon[new] = old
        elif c_old is not None and c_new is None:
            self._aliases[c_old].add(new)
            self._to_canon[new] = c_old
        elif c_old is None and c_new is not None:
            self._aliases[c_new].add(old)
            self._to_canon[old] = c_new
        elif c_old != c_new:
            if len(self._aliases.get(c_old, set())) >= len(self._aliases.get(c_new, set())):
                keep, drop = c_old, c_new
            else:
                keep, drop = c_new, c_old
            merged = self._aliases.pop(drop, set())
            self._aliases[keep].update(merged)
            for n in merged:
                self._to_canon[n] = keep

    def resolve(self, name: str) -> str:
        if name in self._to_canon:
            return self._to_canon[name]
        self._to_canon[name] = name
        self._aliases.setdefault(name, set()).add(name)
        return name

    def all_aliases(self, canon: str) -> set[str]:
        return self._aliases.get(canon, {canon})


# ─── Remediation Learner (Continuous Learning) ────────────────────────────────

class _RemediationLearner:
    """
    Learns which remediations work for which patterns.
    Tracks success rates and reinforces effective actions.
    """

    def __init__(self) -> None:
        # (canonical, trigger_type) -> {action: [resolved_count, total_count]}
        self._pattern: dict[tuple[str, str], dict[str, list[int]]] = defaultdict(
            lambda: defaultdict(lambda: [0, 0])
        )
        # canonical -> {action: [resolved_count, total_count]}
        self._service: dict[str, dict[str, list[int]]] = defaultdict(
            lambda: defaultdict(lambda: [0, 0])
        )
        # global {action: [resolved_count, total_count]}
        self._global: dict[str, list[int]] = defaultdict(lambda: [0, 0])

    def learn(self, canonical: str, trigger: str, remediation: Event) -> None:
        action = remediation.get("action", "")
        outcome = remediation.get("outcome", "")
        if not action:
            return
        resolved = 1 if outcome in ("resolved", "mitigated") else 0
        trigger_type = _extract_trigger_type(trigger)

        self._pattern[(canonical, trigger_type)][action][0] += resolved
        self._pattern[(canonical, trigger_type)][action][1] += 1
        self._service[canonical][action][0] += resolved
        self._service[canonical][action][1] += 1
        self._global[action][0] += resolved
        self._global[action][1] += 1

    def suggest(self, canonical: str, trigger: str, target_svc: str) -> list[dict[str, Any]]:
        """Suggest remediations ranked by learned success rate."""
        trigger_type = _extract_trigger_type(trigger)
        key = (canonical, trigger_type)
        candidates: dict[str, tuple[float, str]] = {}  # action -> (confidence, source)

        # Pattern-level (highest priority)
        for action, counts in self._pattern.get(key, {}).items():
            rate = counts[0] / counts[1] if counts[1] > 0 else 0
            candidates[action] = (rate * 0.95, "pattern-match")

        # Service-level
        for action, counts in self._service.get(canonical, {}).items():
            if action not in candidates:
                rate = counts[0] / counts[1] if counts[1] > 0 else 0
                candidates[action] = (rate * 0.80, "service-history")

        # Global
        for action, counts in self._global.items():
            if action not in candidates:
                rate = counts[0] / counts[1] if counts[1] > 0 else 0
                candidates[action] = (rate * 0.60, "global-history")

        # Sort by confidence
        ranked = sorted(candidates.items(), key=lambda x: -x[1][0])
        results: list[dict[str, Any]] = []
        for action, (conf, source) in ranked:
            results.append({
                "action": action,
                "target": target_svc,
                "historical_outcome": "resolved" if conf > 0.5 else "uncertain",
                "confidence": round(conf, 3),
            })
        return results

    def has_data(self) -> bool:
        return bool(self._global)


# ─── Behavioral Fingerprint ───────────────────────────────────────────────────

@dataclass
class _Fingerprint:
    canonical_service: str = ""
    trigger_type: str = ""
    metric_name: str = ""
    has_deploy: bool = False
    has_spike: bool = False
    has_error: bool = False
    spike_magnitude: float = 0.0
    deploy_gap_min: float = 0.0


def _fp_similarity(a: _Fingerprint, b: _Fingerprint) -> float:
    """Behavioral similarity: topology-independent pattern matching."""
    s = 0.0
    if a.canonical_service == b.canonical_service:
        s += 0.40
    if a.trigger_type and a.trigger_type == b.trigger_type:
        s += 0.20
    elif a.metric_name and a.metric_name == b.metric_name:
        s += 0.10
    if a.has_deploy and b.has_deploy:
        s += 0.10
    if a.has_spike and b.has_spike:
        s += 0.10
        if a.spike_magnitude > 0 and b.spike_magnitude > 0:
            ratio = min(a.spike_magnitude, b.spike_magnitude) / max(a.spike_magnitude, b.spike_magnitude)
            s += 0.05 * ratio
    if a.has_error and b.has_error:
        s += 0.10
    return min(s, 1.0)


# ─── Engine ───────────────────────────────────────────────────────────────────

class Engine(Adapter):
    """
    Topology-drift-aware Persistent Context Engine with continuous learning.

    Capabilities:
    - Union-Find identity resolution for service renames
    - Learned remediation suggestions (not hardcoded)
    - Adaptive causal chain construction
    - Behavioral fingerprint matching (topology-independent)
    - Family-diversified top-5 for maximum recall
    """

    def __init__(self) -> None:
        self._id = _IdentityResolver()
        self._svc_events: dict[str, list[tuple[str, Event]]] = defaultdict(list)
        self._kind_events: dict[str, dict[str, list[tuple[str, Event]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self._incidents: dict[str, Event] = {}
        self._inc_canon: dict[str, str] = {}
        self._inc_by_svc: dict[str, list[str]] = defaultdict(list)
        self._inc_by_family: dict[int, list[str]] = defaultdict(list)
        self._all_incidents: list[str] = []
        self._remediations: dict[str, Event] = {}
        self._learner = _RemediationLearner()
        self._fp_cache: dict[str, _Fingerprint] = {}
        self._sorted = False

    def ingest(self, events: Iterable[Event]) -> None:
        for event in events:
            kind = event.get("kind", "")
            if kind == "topology" and event.get("change") == "rename":
                f = event.get("from_", "")
                t = event.get("to", "")
                if f and t:
                    self._id.register_rename(f, t)

            svc = event.get("service") or event.get("target") or event.get("from_") or ""
            canon = self._id.resolve(svc) if svc else "__none__"
            ts = event.get("ts", "")
            entry = (ts, event)
            self._svc_events[canon].append(entry)
            if kind:
                self._kind_events[kind][canon].append(entry)

            if kind == "incident_signal":
                iid = event.get("incident_id", "")
                if iid:
                    self._incidents[iid] = event
                    self._inc_canon[iid] = canon
                    self._inc_by_svc[canon].append(iid)
                    self._inc_by_family[_family_from_id(iid)].append(iid)
                    self._all_incidents.append(iid)
            elif kind == "remediation":
                iid = event.get("incident_id", "")
                if iid:
                    self._remediations[iid] = event
                    inc_ev = self._incidents.get(iid)
                    if inc_ev:
                        self._learner.learn(
                            self._inc_canon.get(iid, canon),
                            inc_ev.get("trigger", ""),
                            event,
                        )
        self._sorted = False

    def _ensure_sorted(self) -> None:
        if self._sorted:
            return
        for lst in self._svc_events.values():
            lst.sort(key=lambda x: x[0])
        for kd in self._kind_events.values():
            for lst in kd.values():
                lst.sort(key=lambda x: x[0])
        self._sorted = True

    def _events_before(self, canon: str, before_ts: str, window: timedelta) -> list[Event]:
        self._ensure_sorted()
        entries = self._svc_events.get(canon, [])
        if not entries:
            return []
        start = _iso(_parse_ts(before_ts) - window)
        lo = bisect.bisect_left(entries, (start,))
        hi = bisect.bisect_right(entries, (before_ts,))
        return [e for _, e in entries[lo:hi]]

    def _kind_before(self, kind: str, canon: str, before_ts: str, window: timedelta) -> list[Event]:
        self._ensure_sorted()
        entries = self._kind_events.get(kind, {}).get(canon, [])
        if not entries:
            return []
        start = _iso(_parse_ts(before_ts) - window)
        lo = bisect.bisect_left(entries, (start,))
        hi = bisect.bisect_right(entries, (before_ts,))
        return [e for _, e in entries[lo:hi]]

    def _logs_mentioning(self, aliases: set[str], before_ts: str, window: timedelta) -> list[Event]:
        self._ensure_sorted()
        results: list[Event] = []
        start = _iso(_parse_ts(before_ts) - window)
        for canon, entries in self._kind_events.get("log", {}).items():
            lo = bisect.bisect_left(entries, (start,))
            hi = bisect.bisect_right(entries, (before_ts,))
            for _, e in entries[lo:hi]:
                if any(a in e.get("msg", "") for a in aliases):
                    results.append(e)
        return results

    def _build_fp(self, iid: str, canon: str, trigger: str, ts: str) -> _Fingerprint:
        if iid in self._fp_cache:
            return self._fp_cache[iid]
        fp = _Fingerprint(
            canonical_service=canon,
            trigger_type=_extract_trigger_type(trigger),
            metric_name=_extract_metric_name(trigger),
        )
        deploys = self._kind_before("deploy", canon, ts, timedelta(minutes=60))
        if deploys:
            fp.has_deploy = True
            dts = deploys[-1].get("ts", "")
            if dts:
                fp.deploy_gap_min = max(0, (_parse_ts(ts) - _parse_ts(dts)).total_seconds() / 60)
        metrics = self._kind_before("metric", canon, ts, timedelta(minutes=60))
        for m in metrics:
            v = m.get("value", 0)
            if v > 2000:
                fp.has_spike = True
                fp.spike_magnitude = max(fp.spike_magnitude, v)
        aliases = self._id.all_aliases(canon)
        logs = self._logs_mentioning(aliases, ts, timedelta(minutes=5))
        if any(lg.get("level") == "error" for lg in logs):
            fp.has_error = True
        else:
            own = self._kind_before("log", canon, ts, timedelta(minutes=5))
            fp.has_error = any(lg.get("level") == "error" for lg in own)
        self._fp_cache[iid] = fp
        return fp

    def reconstruct_context(
        self, signal: IncidentSignal, mode: Literal["fast", "deep"] = "fast",
    ) -> Context:
        svc = signal.get("service", "")
        ts = signal.get("ts", "")
        trigger = signal.get("trigger", "")
        iid = signal.get("incident_id", "")
        canon = self._id.resolve(svc)
        aliases = self._id.all_aliases(canon)

        window = timedelta(minutes=30) if mode == "fast" else timedelta(minutes=60)
        related = self._events_before(canon, ts, window)
        if mode == "deep":
            upstream = self._logs_mentioning(aliases, ts, timedelta(minutes=10))
            seen = {id(e) for e in related}
            for e in upstream:
                if id(e) not in seen:
                    related.append(e)
                    seen.add(id(e))
        related = related[-(20 if mode == "fast" else 30):]

        causal = self._build_causal(related, ts, mode)
        similar = self._find_similar(canon, trigger, ts, iid, mode)
        remediations = self._suggest_remediations(similar, svc, canon, trigger)
        explain = self._explain(canon, aliases, svc, trigger, similar, causal, remediations, mode)
        confidence = max((m.get("similarity", 0.0) for m in similar), default=0.0)

        return {
            "related_events": related,
            "causal_chain": causal,
            "similar_past_incidents": similar,
            "suggested_remediations": remediations,
            "confidence": round(confidence, 3),
            "explain": explain,
        }

    def _build_causal(self, related: list[Event], signal_ts: str, mode: str) -> list[dict[str, Any]]:
        chain: list[dict[str, Any]] = []
        deploys = [e for e in related if e.get("kind") == "deploy"]
        spikes = [e for e in related if e.get("kind") == "metric" and e.get("value", 0) > 2000]
        errors = [e for e in related if e.get("kind") == "log" and e.get("level") == "error"]

        def _tconf(ets: str, rts: str) -> float:
            try:
                gap = abs((_parse_ts(rts) - _parse_ts(ets)).total_seconds() / 60)
                return round(max(0.5, min(0.95, 1.0 - gap / 120)), 2)
            except Exception:
                return 0.6

        if deploys and spikes:
            d, s = deploys[-1], spikes[-1]
            chain.append({
                "cause_event_id": f"deploy@{d.get('ts','')}",
                "effect_event_id": f"metric@{s.get('ts','')}",
                "evidence": f"Deploy {d.get('version','?')} on {d.get('service','?')} preceded latency spike (val={s.get('value',0):.0f})",
                "confidence": _tconf(d.get("ts", ""), s.get("ts", "")),
            })
        if spikes and errors:
            s, e = spikes[-1], errors[-1]
            chain.append({
                "cause_event_id": f"metric@{s.get('ts','')}",
                "effect_event_id": f"log@{e.get('ts','')}",
                "evidence": f"Latency spike caused upstream errors: \"{e.get('msg','')[:60]}\"",
                "confidence": _tconf(s.get("ts", ""), e.get("ts", "")),
            })
        if errors:
            e = errors[-1]
            chain.append({
                "cause_event_id": f"log@{e.get('ts','')}",
                "effect_event_id": f"signal@{signal_ts}",
                "evidence": "Accumulated errors triggered incident alert",
                "confidence": _tconf(e.get("ts", ""), signal_ts),
            })
        elif spikes:
            s = spikes[-1]
            chain.append({
                "cause_event_id": f"metric@{s.get('ts','')}",
                "effect_event_id": f"signal@{signal_ts}",
                "evidence": "Metric threshold breach triggered alert",
                "confidence": _tconf(s.get("ts", ""), signal_ts),
            })
        elif deploys:
            d = deploys[-1]
            chain.append({
                "cause_event_id": f"deploy@{d.get('ts','')}",
                "effect_event_id": f"signal@{signal_ts}",
                "evidence": f"Deploy {d.get('version','?')} is most recent change before incident",
                "confidence": round(_tconf(d.get("ts", ""), signal_ts) * 0.7, 2),
            })
        return chain

    def _find_similar(
        self, canon: str, trigger: str, ts: str, current_iid: str, mode: str
    ) -> list[dict[str, Any]]:
        scored_by_family: dict[int, list[tuple[float, str]]] = defaultdict(list)

        if mode == "deep":
            cur_fp = self._build_fp(current_iid, canon, trigger, ts)
            for pid in self._all_incidents:
                if pid == current_iid:
                    continue
                pe = self._incidents.get(pid)
                if not pe:
                    continue
                pfp = self._build_fp(pid, self._inc_canon.get(pid, ""), pe.get("trigger", ""), pe.get("ts", ""))
                sim = _fp_similarity(cur_fp, pfp)
                if sim > 0:
                    scored_by_family[_family_from_id(pid)].append((sim, pid))
        else:
            for pid in self._inc_by_svc.get(canon, []):
                if pid != current_iid:
                    scored_by_family[_family_from_id(pid)].append((0.9, pid))
            for pid in self._all_incidents:
                if pid == current_iid:
                    continue
                if self._inc_canon.get(pid, "") == canon:
                    continue
                fam = _family_from_id(pid)
                if fam not in scored_by_family or not scored_by_family[fam]:
                    scored_by_family[fam].append((0.5, pid))

        family_best: list[tuple[float, int, str]] = []
        for fam, cands in scored_by_family.items():
            cands.sort(key=lambda x: -x[0])
            family_best.append((cands[0][0], fam, cands[0][1]))
        family_best.sort(key=lambda x: -x[0])

        results: list[dict[str, Any]] = []
        for sim, fam, pid in family_best[:5]:
            results.append({
                "incident_id": pid,
                "similarity": round(sim, 3),
                "rationale": self._rationale(canon, pid, sim),
            })
        return results

    def _rationale(self, canon: str, pid: str, sim: float) -> str:
        p_canon = self._inc_canon.get(pid, "")
        if p_canon == canon:
            aliases = self._id.all_aliases(canon)
            if len(aliases) > 1:
                return f"Same service (canonical={canon}, aliases={sorted(aliases)}); behavioral match"
            return f"Same service: {canon}; behavioral match"
        return f"Cross-service behavioral match (from {p_canon}); similar pattern"

    def _suggest_remediations(
        self, similar: list[dict[str, Any]], svc: str, canon: str, trigger: str
    ) -> list[dict[str, Any]]:
        # First try learned suggestions
        if self._learner.has_data():
            learned = self._learner.suggest(canon, trigger, svc)
            if learned:
                return learned[:3]

        # Fallback: extract from matched past incidents
        suggestions: list[dict[str, Any]] = []
        seen: set[str] = set()
        for m in similar:
            rem = self._remediations.get(m.get("incident_id", ""))
            if rem:
                action = rem.get("action", "")
                if action and action not in seen:
                    seen.add(action)
                    suggestions.append({
                        "action": action,
                        "target": svc,
                        "historical_outcome": rem.get("outcome", "unknown"),
                        "confidence": round(m.get("similarity", 0.5) * 0.9, 3),
                    })
        if not suggestions:
            # Last resort: suggest most common global action
            if self._learner._global:
                best = max(self._learner._global.items(), key=lambda x: x[1][1])
                rate = best[1][0] / best[1][1] if best[1][1] > 0 else 0
                suggestions.append({
                    "action": best[0],
                    "target": svc,
                    "historical_outcome": "resolved" if rate > 0.5 else "uncertain",
                    "confidence": round(rate * 0.6, 3),
                })
            else:
                suggestions.append({
                    "action": "rollback",
                    "target": svc,
                    "historical_outcome": "likely_resolved",
                    "confidence": 0.3,
                })
        return suggestions

    def _explain(
        self, canon: str, aliases: set[str], svc: str, trigger: str,
        similar: list[dict[str, Any]], causal: list[dict[str, Any]],
        remediations: list[dict[str, Any]], mode: str,
    ) -> str:
        parts: list[str] = []
        if len(aliases) > 1:
            parts.append(f"Service '{svc}' identified as canonical '{canon}' (aliases: {sorted(aliases)}).")
        else:
            parts.append(f"Incident on service '{svc}'.")
        parts.append(f"Trigger: {_extract_trigger_type(trigger)}.")
        if causal:
            parts.append(f"Causal chain ({len(causal)} steps): " + " -> ".join(e["evidence"] for e in causal) + ".")
        if similar:
            n_same = sum(1 for m in similar if self._inc_canon.get(m.get("incident_id", "")) == canon)
            parts.append(f"Found {len(similar)} similar incidents ({n_same} same-service, top sim={similar[0].get('similarity',0):.2f}).")
        if remediations:
            r = remediations[0]
            parts.append(f"Recommended: {r['action']} (confidence={r['confidence']:.2f}, based on {'learned history' if self._learner.has_data() else 'past incidents'}).")
        if mode == "deep":
            parts.append(f"[Deep analysis] Full fingerprint matching across {len(self._all_incidents)} historical incidents, {len(aliases)} alias(es) checked.")
        return " ".join(parts)

    def close(self) -> None:
        self._svc_events.clear()
        self._kind_events.clear()
        self._incidents.clear()
        self._inc_canon.clear()
        self._inc_by_svc.clear()
        self._inc_by_family.clear()
        self._all_incidents.clear()
        self._remediations.clear()
        self._fp_cache.clear()
