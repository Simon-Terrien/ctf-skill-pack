"""CMS-CAG integration test against the REAL cms-runtime.

Skips cleanly if cms is not importable, so the core suite stays CMS-independent
(ctfrt must boot without CMS). When cms-runtime is on path, this exercises the
full record -> retrieve -> recommend cycle.

Run: CMS_SRC=/path/to/cms-runtime/src python tests/test_cms_cag.py
"""
from __future__ import annotations

import asyncio
import os
import sys

# allow pointing at a local cms-runtime checkout
_cms_src = os.getenv("CMS_SRC")
if _cms_src and _cms_src not in sys.path:
    sys.path.insert(0, _cms_src)

try:
    import cms  # noqa: F401
    _HAVE_CMS = True
except Exception:
    _HAVE_CMS = False

from ctfrt.memory_query import MemoryQuestion, NullMemoryQuery


async def test_null_memory_degrades_gracefully():
    """The default (no CMS) must answer safely, not crash."""
    ans = await NullMemoryQuery().ask(
        MemoryQuestion(mission_id="m", requester="reverse", question="anything"))
    assert ans.confidence == 0.0 and ans.answer == ""
    assert ans.recommended_next_action is None


async def test_cms_cag_surfaces_prior_technique():
    if not _HAVE_CMS:
        print("SKIP test_cms_cag_surfaces_prior_technique (cms not importable; set CMS_SRC)")
        return
    from ctfrt.cms_cag import CMSMemory

    mem = CMSMemory()  # in-memory sqlite
    mem.record_mission("rev-001", "reverse",
        "Mission rev-001 reverse: strings found nothing; ltrace revealed a strcmp "
        "comparison; dynamic tracing recovered the flag. SOLVED.")
    mem.record_mission("rev-002", "reverse",
        "Mission rev-002 reverse: rodata XOR loop; inverted the transform to "
        "recover the flag. SOLVED.")
    mem.record_mission("web-001", "web-exploit",
        "Mission web-001: SSTI in Jinja2 template led to RCE. SOLVED.")

    # ask the reverse lane about the strcmp situation
    ans = await mem.ask(MemoryQuestion(
        mission_id="rev-003", requester="reverse", category="reverse",
        question="binary with strcmp validation, strings found nothing — what worked?"))

    assert ans.confidence > 0.0
    assert any(e.source_type == "mission_observation" for e in ans.evidence)
    # the closest prior mission used dynamic tracing via ltrace/strcmp
    assert ("ltrace" in ans.related_patterns or "strcmp" in ans.related_patterns)
    assert ans.recommended_next_action is not None
    # cross-category isolation: the web SSTI mission must not be cited here
    assert all("ssti" not in e.summary.lower() for e in ans.evidence)


async def test_cms_cag_no_match_is_honest():
    if not _HAVE_CMS:
        print("SKIP test_cms_cag_no_match_is_honest (cms not importable)")
        return
    from ctfrt.cms_cag import CMSMemory
    mem = CMSMemory()
    ans = await mem.ask(MemoryQuestion(
        mission_id="x", requester="crypto-attack", category="crypto-attack",
        question="elliptic curve invalid point attack"))
    assert ans.confidence == 0.0 and ans.warnings  # honest about empty memory


async def test_full_loop_technique_flows_into_memory():
    """Solve -> enriched 'solved' trace -> MemoryConsumer -> CMS -> queryable.
    Proves the technique tag survives the whole path, not just record_mission."""
    if not _HAVE_CMS:
        print("SKIP test_full_loop_technique_flows_into_memory (cms not importable)")
        return
    from ctfrt.cms_cag import CMSMemory, MemoryConsumer
    from ctfrt.bus import InMemoryBus
    from ctfrt.contracts import TraceEvent

    bus = InMemoryBus()
    mem = CMSMemory()
    consumer = MemoryConsumer(bus, mem)

    # start the consumer reading traces
    task = asyncio.create_task(consumer.run())
    await asyncio.sleep(0)

    # emit an enriched 'solved' trace exactly as orchestrator.on_flag now does
    await bus.publish("ctf.traces", TraceEvent(
        challenge_id="rev-009", kind="solved",
        payload={"flag": "CTF{loop}", "category": "reverse",
                 "technique": ["ltrace", "strcmp"], "source": "reverse:BioBrainAdapter"}))
    await asyncio.sleep(0.05)  # let the consumer process
    task.cancel()

    ans = await mem.ask(MemoryQuestion(
        mission_id="rev-010", requester="reverse", category="reverse",
        question="strcmp validated binary, what technique solved it before?"))
    assert "ltrace" in ans.related_patterns or "strcmp" in ans.related_patterns
    assert ans.recommended_next_action is not None


TESTS = [
    test_null_memory_degrades_gracefully,
    test_cms_cag_surfaces_prior_technique,
    test_cms_cag_no_match_is_honest,
    test_full_loop_technique_flows_into_memory,
]

if __name__ == "__main__":
    for t in TESTS:
        asyncio.run(t())
        print(f"PASS {t.__name__}")
