"""v2 audio / latency summary (CMI-3). Pure copy of the v1 percentile math so v2
survives the M6 deletion of interview_engine/. Operates on the v2 event-log events
(EventLogEvent.model_dump(mode="json") shape: {"kind", "payload", ...}); the
AgentSession emits the same `audio.metrics.{eou,llm,tts}_metrics` payloads v1 used.
"""

from __future__ import annotations

from typing import Any


def percentile_stats(values: list[int]) -> dict[str, int]:
    """p50/p95/max/n for an int list (true median for even n) — matches v1."""
    if not values:
        return {"p50": 0, "p95": 0, "max": 0, "n": 0}
    s = sorted(values)
    n = len(s)
    p50 = (s[n // 2 - 1] + s[n // 2]) // 2 if n % 2 == 0 else s[n // 2]
    p95 = s[min(n - 1, int(n * 0.95))]
    return {"p50": p50, "p95": p95, "max": s[-1], "n": n}


def extract_ms(events: list[dict[str, Any]], field: str) -> list[int]:
    """Pull a positive float `field` (seconds) from each event payload -> ms."""
    out: list[int] = []
    for ev in events:
        payload = ev.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        val = payload.get(field)
        if isinstance(val, (int, float)) and val > 0:
            out.append(int(val * 1000))
    return out


def summarize_perceived_latency(events: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Aggregate per-turn ChatMessage.metrics (the working 1.5.9 latency signal — CMI-3).

    Reads `turn.latency.assistant` (llm_node_ttft / tts_node_ttfb / e2e_latency) and
    `turn.latency.user` (end_of_turn_delay) events recorded by the agent. The headline
    CMI-3 number is `perceived_response_ms` = llm_node_ttft + tts_node_ttfb per turn.
    """
    asst = [e for e in events if e.get("kind") == "turn.latency.assistant"]
    user = [e for e in events if e.get("kind") == "turn.latency.user"]

    perceived: list[int] = []
    for e in asst:
        p = e.get("payload") or {}
        ttft, ttfb = p.get("llm_node_ttft"), p.get("tts_node_ttfb")
        if isinstance(ttft, (int, float)) and isinstance(ttfb, (int, float)) and ttft > 0 and ttfb > 0:
            perceived.append(int((ttft + ttfb) * 1000))

    return {
        "perceived_response_ms": percentile_stats(perceived),
        "llm_ttft_ms": percentile_stats(extract_ms(asst, "llm_node_ttft")),
        "tts_ttfb_ms": percentile_stats(extract_ms(asst, "tts_node_ttfb")),
        "e2e_latency_ms": percentile_stats(extract_ms(asst, "e2e_latency")),
        "eou_delay_ms": percentile_stats(extract_ms(user, "end_of_turn_delay")),
    }


def compute_audio_summary(
    *, events: list[dict[str, Any]], config_snapshot: dict[str, object],
) -> dict[str, object]:
    """Aggregate latency percentiles from audio.metrics.* events (CMI-3 gate)."""
    eou = [e for e in events if e.get("kind") == "audio.metrics.eou_metrics"]
    llm = [e for e in events if e.get("kind") == "audio.metrics.llm_metrics"]
    tts = [e for e in events if e.get("kind") == "audio.metrics.tts_metrics"]
    return {
        "latency": {
            "end_of_utterance_delay_ms": percentile_stats(extract_ms(eou, "end_of_utterance_delay")),
            "transcription_delay_ms": percentile_stats(extract_ms(eou, "transcription_delay")),
            "llm_ttft_ms": percentile_stats(extract_ms(llm, "ttft")),
            "tts_ttfb_ms": percentile_stats(extract_ms(tts, "ttfb")),
        },
        "perceived": summarize_perceived_latency(events),   # CMI-3 mouth half (working signal)
        "config": dict(config_snapshot),
    }
