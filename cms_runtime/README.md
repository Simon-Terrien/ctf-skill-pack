# CMS Runtime

Operationalization of the Complex Meaning Space framework, kept structurally separate from the research line per ADR.

## Status

**Blocks 1 + 2 + 3 + 4 + 5 + 6 delivered.** Per the ADR sequencing decision:

**Block 1 — L1 observation layer**
- packaging, layout, dependencies
- `L1Observation` canonical record
- `ObservationService` ingestion path with optional explicit `turn_index`
- SQLite storage with `ObservationStore`
- migration adapter from research-line `CMSPoint`
- equivalence tests proving bit-exact match against the research path

**Block 2 — L2 episode layer**
- `L2Episode` dataclass with invariants
- pluggable closure policies (`WindowedClosurePolicy`, `SurpriseClosurePolicy`, `CompositeClosurePolicy`)
- injectable surprise scorer (`EuclideanSurpriseScorer`)
- `EpisodeService` with per-session open-episode tracking, flush semantics
- `EpisodeStore` with CRUD + time-range queries
- schema v2 with idempotent migration
- `CMSEngine` orchestrating L1 → L2

**Block 3 — L3A memory evidence layer**
- `MemoryEvidence` dataclass with mandatory provenance
- 8 filing rules (5 observation-level, 3 episode-level) with dead zones + mutual exclusion
- `EvidenceService` with idempotency fast path + schema-level UNIQUE backstop
- soft-canonical scope policing
- bounded, deterministic `support_score` + default `relevance_score` = 1.0
- `EvidenceStore` with CRUD + provenance queries
- schema v3 with UNIQUE (user_id, source_kind, source_id, rule_id)
- contradiction fields persisted but inert until Block 5

**Block 4 — Retrieval and canonical state assembly**
- `RuntimeStateView` — single canonical, consumer-neutral view of persisted state
- `RetrievalPolicy` — tunable limits (default: 5 obs, 3 eps, 5 evidence)
- `RetrievalService` — composes store queries with the locked deterministic ordering
- `StateAssembler` — builds `RuntimeStateView` with signals/counts/freshness flags
- store helpers: `latest_for_session`, scoped `search` for evidence
- evidence ranking: pinned > scope-exact > subscope-exact > newer > stronger > id (deterministic tie-break)
- freshness flags reflect operational recency, NOT decay
- embedded records (not just ids) so consumers don't make second store calls

**Block 5 — L3B profile beliefs**
- `ProfileBelief` dataclass with strict per-dimension value semantics (`DIMENSION_SPECS` registry)
- 3 belief dimensions, scope-pure mapping:
  - `epistemic_style` ← epistemic scope (signed [-1, +1])
  - `social_orientation` ← social scope (signed [-1, +1])
  - `pragmatic_style` ← pragmatic scope (magnitude [0, +1])
- `BeliefService` with strict evidence-only input (never reads observations or episodes directly)
- `BeliefStore` with split insert/update routing — UNIQUE (user_id, dimension) actually fires
- `BeliefThresholds` configurable policy: tentative → active → stale → invalidated
- staleness via discrete status transitions only (vocabulary: "staleness", never "decay")
- contradiction population: `counterevidence_ids` now populated; classification uses supporting-ledger direction (not value sign) so contradictions stay classified as contradictions even when value drifts to zero
- contradiction can invalidate via two paths: count > supports, OR burst threshold within window
- engine wiring: optional `belief_service` constructor arg, push-based via `process_new_evidence`
- `recompute_for_user` escape hatch for batch repair / threshold changes
- `sweep_staleness` for periodic active → stale transitions
- `RuntimeStateView` extended with `active_beliefs` and `tentative_beliefs` (stale and invalidated NOT surfaced as truth)
- `StateAssembler` reads beliefs but never mutates them (guardrail B enforced by tests)
- schema v4 with profile_beliefs table, UNIQUE (user_id, dimension)
- `dynamics` scope evidence is filed but feeds no belief in Block 5 — honest non-mapping

**Block 6 — Scoped beliefs, cross-scope dimensions, supersession, events, explanations**
- `context_key` per turn — caller-supplied lane string, propagates from `engine.process_turn(context_key=...)` through evidence to beliefs; `None` means global, non-`None` means scoped
- guardrail A: `context_key` does NOT enter the evidence idempotency key — replays of the same source object don't duplicate evidence even if context handling changed
- guardrail B: global and scoped beliefs coexist with no implicit reconciliation; `RuntimeStateView` exposes them in separate buckets
- `interaction_stability` — fourth belief dimension, fed by `dynamics` scope (rupture → -1, sustained_regime → +1); the locked four-case test matrix covers scope-pure × global/scoped and cross-scope × global/scoped
- filing-time supersession in `EvidenceService` — when filing a new record, prior records older than 30 days (configurable) in the same `(user_id, rule_id, context_key)` lane are recorded in the new record's `supersedes` list; old records remain in store as audit history
- supersession-aware recompute — superseded support stays in `supporting_memory_ids` for full provenance but is excluded from value, confidence, stability, and threshold counting; `metadata["superseded_support_count"]` exposes the audit count
- belief transition events — six event types (`belief_tentative_created`, `belief_activated`, `belief_staled`, `belief_invalidated`, `belief_recomputed`, `belief_scoped_created`) emitted via callable `event_handler`; ships `NullEventHandler` (default) and `LoggingEventHandler`; events fire only on real status transitions, never on numeric tweaks
- `BeliefExplanation` dataclass + `BeliefService.explain(belief_id, top_n=5)` for on-demand structured explanations; ranked deterministically by (support_score DESC, created_at DESC, memory_id DESC); never carried on `RuntimeStateView`
- `RuntimeStateView` split: `active_beliefs_global`, `active_beliefs_scoped`, `tentative_beliefs_global`, `tentative_beliefs_scoped`; legacy `active_beliefs` / `tentative_beliefs` properties return the global lists for back-compat
- `all_active_beliefs` convenience property combines both; consumers that use it accept reconciliation responsibility
- schema v5 — `context_key` columns on `memory_evidence` and `profile_beliefs`, composite UNIQUE index `(user_id, dimension, COALESCE(context_key, ''))` so global and scoped beliefs are distinct lanes
- `SQLiteBackend.bootstrap_schema` auto-applies v5 migration steps; all existing call sites work unchanged

## Layout

```
cms_runtime/
├── pyproject.toml
├── requirements/
│   ├── base.txt
│   ├── research.txt
│   └── dev.txt
├── src/
│   └── cms/
│       ├── l1/
│       │   ├── observation.py       L1Observation dataclass
│       │   ├── adapter.py           LegacyExtractorAdapter
│       │   └── service.py           ObservationService
│       ├── l2/
│       │   ├── episode.py           L2Episode dataclass
│       │   ├── policies.py          Closure policies + surprise scorer
│       │   └── service.py           EpisodeService
│       ├── l3/
│       │   ├── evidence.py          MemoryEvidence dataclass + CANONICAL_SCOPES
│       │   ├── rules.py             8 filing rules (pluggable)
│       │   ├── service.py           EvidenceService (idempotent, scope-policed)
│       │   ├── belief.py            ProfileBelief + DIMENSION_SPECS registry
│       │   ├── belief_policy.py     BeliefThresholds + is_belief_stale
│       │   └── belief_service.py    BeliefService (evidence-only, push-based)
│       ├── runtime/
│       │   ├── engine.py            CMSEngine + TurnResult
│       │   ├── retrieval.py         RetrievalService + ordering policy
│       │   ├── assembler.py         StateAssembler
│       │   └── state.py             RuntimeStateView + RetrievalPolicy
│       ├── storage/
│       │   ├── base.py              StorageBackend protocol
│       │   ├── sqlite.py            SQLiteBackend
│       │   ├── schema.py            DDL (v1 + v2 + v3 + v4, idempotent)
│       │   ├── observation_store.py
│       │   ├── episode_store.py
│       │   ├── evidence_store.py
│       │   └── belief_store.py
│       └── migration/
│           └── from_cms_point.py    Legacy CMSPoint → L1Observation
├── scripts/
│   └── bootstrap_db.py
└── tests/
    ├── unit/ (9 files)
    └── integration/ (3 files)
```

## Architectural rules in force

- **No long-term belief without explicit supporting evidence references** — constraint now actively enforced. Every future belief (Block 5) will reference `memory_id`s from L3A.
- **Evidence is not belief** — summaries are rule-owned, deterministic, non-interpretive. No identity claims. Tests enforce this.
- **Consumer-neutral core** — no LLM prompt format, no agent routing, no dashboard rendering baked into the runtime.
- **Provenance is mandatory** — every evidence record carries `(source_kind, source_id, rule_id)`. Idempotency key = `(user_id, source_kind, source_id, rule_id)`.
- **Soft-canonical scope** — field types are free strings, but the service polices producer behavior. Block 3 scopes are locked to `{pragmatic, epistemic, social, dynamics}`.
- **Inert contradiction fields** — `supersedes` and `contradicted_by` present in dataclass and schema from day one. Block 3 never populates them; Block 5 will.
- **No premature taxonomy** — `tags`, `subscope`, `trajectory_signature` stay flexible. Hardened taxonomy deferred until evidence patterns emerge from real data.
- **Research line stays intact** — runtime does not modify `cms_research/`.

## Setup

```bash
pip install -e ".[dev]"
python scripts/bootstrap_db.py
pytest                        # 446 tests, ~3.5s
pytest --cov=cms              # 95% coverage
```

## Durable temporal phase (when needed)

By default, `ObservationService` uses an in-memory per-session counter to compute `temporal_phase`. This is fine for tests and single-process prototypes but resets on process restart and would diverge across multiple workers ingesting for the same session.

For durable phase semantics, callers can supply `turn_index` explicitly:

```python
result = engine.process_turn(
    user_id="alice",
    session_id="sess_001",
    turn_id="t42",
    text="...",
    turn_index=42,    # explicit — survives process restart
)
```

When `turn_index` is supplied, it overrides the internal counter. When absent, the counter is the fallback. This is the recommended pattern for any deployment where phase identity must be reproducible.

## Quick example

```python
from cms.l1 import LegacyExtractorAdapter, ObservationService
from cms.l2 import EpisodeService, WindowedClosurePolicy
from cms.l3 import EvidenceService
from cms.l3.belief_service import BeliefService
from cms.runtime import (
    CMSEngine, RetrievalService, StateAssembler, RetrievalPolicy,
)
from cms.storage import (
    SQLiteBackend, ObservationStore, EpisodeStore, EvidenceStore,
    FULL_SCHEMA_DDL,
)
from cms.storage.belief_store import BeliefStore
from cms_research.cms_core.models import LinguisticFeatureExtractor

backend = SQLiteBackend("data/sqlite/cms_runtime.db")
backend.bootstrap_schema(FULL_SCHEMA_DDL)

obs_store = ObservationStore(backend)
ep_store = EpisodeStore(backend)
ev_store = EvidenceStore(backend)
belief_store = BeliefStore(backend)

# Full L1+L2+L3A+L3B stack
adapter = LegacyExtractorAdapter(LinguisticFeatureExtractor())
obs_service = ObservationService(adapter=adapter, store=obs_store)
ep_service = EpisodeService(
    store=ep_store, policy=WindowedClosurePolicy(max_size=10),
)
ev_service = EvidenceService(store=ev_store)
bf_service = BeliefService(belief_store=belief_store, evidence_store=ev_store)

engine = CMSEngine(
    obs_service, ep_service,
    evidence_service=ev_service,
    belief_service=bf_service,
)

# Block 4 retrieval + state assembly, now with beliefs wired
retrieval = RetrievalService(obs_store, ep_store, ev_store)
assembler = StateAssembler(retrieval, belief_store=belief_store)

# Process turns through the engine
result = engine.process_turn(
    user_id="alice", session_id="sess_001", turn_id="t0",
    text="I'm absolutely certain we need to ship this today.",
)

print(f"Observation: {result.observation.obs_id}")
print(f"Evidence filed: {result.new_evidence_ids}")
print(f"Beliefs updated: {result.updated_belief_ids}")

# Build a canonical state view including beliefs
state = assembler.build("alice", "sess_001")
for b in state.active_beliefs:
    print(f"  ACTIVE   {b.dimension}: value={b.value:+.2f} conf={b.confidence:.2f}")
for b in state.tentative_beliefs:
    print(f"  TENTATIVE {b.dimension}: value={b.value:+.2f} conf={b.confidence:.2f}")

# Periodic staleness sweep (e.g., daily cron)
bf_service.sweep_staleness("alice")

engine.end_session("alice", "sess_001")
```

## Block 3 rule pack

### Observation-level rules (5)

| rule_id                       | scope     | subscope              | fires when                            |
|-------------------------------|-----------|-----------------------|---------------------------------------|
| `obs.pragmatic.high_ratio`    | pragmatic | high_pragmatic_ratio  | \|Im(z1)\|/\|Re(z1)\| ≥ 1.5           |
| `obs.epistemic.certainty`     | epistemic | certainty             | Re(z2) ≥ 0.75                         |
| `obs.epistemic.hedging`       | epistemic | hedging               | Re(z2) ≤ 0.35                         |
| `obs.social.self_reference`   | social    | self_reference        | Im(z3) ≤ 0.35                         |
| `obs.social.other_reference`  | social    | other_reference       | Im(z3) ≥ 0.75                         |

Dead zones (0.35 < x < 0.75) guarantee that certainty↔hedging and self↔other are mutually exclusive per observation.

### Episode-level rules (3)

| rule_id                         | scope     | subscope                     | fires when                                     |
|---------------------------------|-----------|------------------------------|------------------------------------------------|
| `ep.dynamics.rupture`           | dynamics  | rupture                      | length ≤ 5 AND closure_reason contains "surprise" |
| `ep.dynamics.sustained_regime`  | dynamics  | sustained_regime             | length ≥ 10 AND natural closure                |
| `ep.pragmatic.sustained_density`| pragmatic | sustained_pragmatic_density  | trajectory_signature["mean_pragmatic_ratio"] ≥ 1.0 |

All `support_score` values are bounded in [0, 1] and monotonic with trigger strength.

**Note on `sustained_density`:** This rule reads from `trajectory_signature` which is populated by signature producers external to this slice (research-line code or a future runtime hook). In a default Block 3 deployment with no signature producer wired, this rule does not fire. The rule is correct; the trigger source is intentionally pluggable.

## Idempotency guarantees

Evidence filing is safe under retry, replay, and duplicate processing:

- **Fast path**: `EvidenceService` calls `store.has_evidence_for()` before filing. If a record already exists with the same `(user_id, source_kind, source_id, rule_id)`, the rule firing is skipped.
- **Backstop**: The `memory_evidence` table has `UNIQUE (user_id, source_kind, source_id, rule_id)`. If the fast path is bypassed or races, the `INSERT` fails with `IntegrityError` rather than producing a duplicate.

Replay a whole session — observation, episode, evidence — and the evidence count stays constant.

## Pluggable rule packs

Research can experiment without touching the service:

```python
from cms.l3 import EvidencePayload, EvidenceService

def custom_rule(obs):
    if some_condition(obs):
        return EvidencePayload(
            rule_id="custom.my_rule",
            scope="epistemic",             # must be in CANONICAL_SCOPES
            subscope="my_narrow_label",
            summary="observation showed X",  # non-interpretive
            support_score=0.7,
        )
    return None

service = EvidenceService(
    store=ev_store,
    observation_rules=[custom_rule],   # override defaults
    episode_rules=[],
)
```

If the rule produces a scope outside `CANONICAL_SCOPES`, the service raises `ValueError` — producer behavior is policed even when field types are free.

## Auditing evidence provenance

```python
# All evidence produced by a specific observation
records = ev_store.list_for_source("alice", "observation", "obs_xyz")

# All evidence in a specific scope
records = ev_store.list_by_scope("alice", "epistemic")

# Every evidence record carries its full provenance:
for r in records:
    print(r.source_kind, r.source_id, r.rule_id, r.support_score)
```

## Block 4 retrieval ordering policy (locked)

Evidence ranking from `RetrievalService.search_evidence()` is deterministic. Sort precedence:

1. `pinned=True` first (Block 5 will populate the pinned flag — slot reserved, currently always False)
2. exact `scope` match before broader results
3. exact `subscope` match before broader results
4. newer records (higher `created_at`) before older
5. higher `support_score` before lower
6. `memory_id` DESC for deterministic tie-break

Repeated calls with the same inputs return identical results. The candidate pool size scales with the requested limit (4×, minimum 20) so the ordering policy can promote scope-exact records that wouldn't otherwise make the cut.

## Block 4 freshness flags

`StateAssembler` computes operational freshness, not semantic decay:

| flag                          | meaning                                                                |
|-------------------------------|------------------------------------------------------------------------|
| `has_recent_observations`     | latest observation within `recent_observation_seconds` (default 5min)  |
| `has_recent_episodes`         | latest episode within `recent_episode_seconds` (default 30min)         |
| `has_recent_evidence`         | latest evidence within `recent_evidence_seconds` (default 1hr)         |

These thresholds are constructor parameters on `StateAssembler` — no global config. They answer "is this state fresh enough to trust right now?", not "how much should this evidence weigh?". The latter is Block 5.

## Block 5 belief dimensions (locked)

Three dimensions, scope-pure mapping:

| dimension              | source scope | polarity   | value range  |
|------------------------|--------------|------------|--------------|
| `epistemic_style`      | `epistemic`  | signed     | [-1, +1]     |
| `social_orientation`   | `social`     | signed     | [-1, +1]     |
| `pragmatic_style`      | `pragmatic`  | magnitude  | [ 0, +1]     |

For signed dimensions: -1 means full opposite-direction support (hedging / self-reference), +1 means full primary-direction support (certainty / other-reference). For pragmatic_style, value is a magnitude in [0, +1].

`dynamics` scope evidence is filed but feeds no Block 5 belief — honest non-mapping rather than inventing cross-scope coherence semantics prematurely.

## Block 5 belief lifecycle

Status transitions (defaults, all configurable via `BeliefThresholds`):

| Transition | Trigger |
|---|---|
| → tentative | first qualifying support (≥1 record above `min_supporting_strength`) |
| → active | ≥3 supporting records within `active_window_days` (30) AND confidence ≥ 0.5 |
| → stale | active belief with no support refresh in `stale_window_days` (14), via `sweep_staleness()` |
| → invalidated | contradiction count > support count, OR ≥3 contradictions within `invalidation_burst_window_days` (7) |

**Vocabulary contract:** Block 5 uses *staleness* exclusively. *Decay* is reserved for nothing — discrete status transitions only, no continuous score mutation.

**Boundary:** `BeliefService` reads only from evidence (never observations or episodes directly). The supporting ledger is append-only — recomputation re-reads the ledger but never rewrites it. Idempotency is preserved across replays via memory_id check.

**Engine wiring:** Push-based via optional `belief_service` arg on `CMSEngine`. Each turn's new evidence triggers a focused belief update for the affected dimensions only. `recompute_for_user(user_id)` is the offline escape hatch for batch repair or threshold changes.

**Assembler invariant:** `StateAssembler` reads beliefs from the store but never mutates them. Tested explicitly. `RuntimeStateView` exposes `active_beliefs` and `tentative_beliefs` separately. Stale and invalidated beliefs are NOT surfaced as truth.


## Block 6 scoped beliefs and supersession (locked)

**Context lanes.** Each turn carries an optional `context_key: str | None`. `None` means global; non-`None` ("research", "ops", "personal", whatever the caller chooses) routes evidence and beliefs into a scoped lane keyed on `(user_id, dimension, context_key)`. The runtime stores and compares context_key but does not interpret it — semantics are entirely caller-defined.

Two locked guardrails:

- **(A)** `context_key` does NOT enter the evidence idempotency key. Replaying the same `(user_id, source_kind, source_id, rule_id)` twice with different context_keys does not duplicate evidence.
- **(B)** Global and scoped beliefs coexist with no implicit reconciliation. Filing scoped evidence does not write to global beliefs and vice versa. Consumers that want a combined view use `state.all_active_beliefs` and accept reconciliation responsibility.

**Four belief dimensions** (three scope-pure + one cross-scope):

| dimension              | source scope | polarity   | value range  |
|------------------------|--------------|------------|--------------|
| `epistemic_style`      | `epistemic`  | signed     | [-1, +1]     |
| `social_orientation`   | `social`     | signed     | [-1, +1]     |
| `pragmatic_style`      | `pragmatic`  | magnitude  | [ 0, +1]     |
| `interaction_stability`| `dynamics`   | signed     | [-1, +1]     |

`interaction_stability` is the first cross-scope dimension — it consumes `dynamics` evidence (rupture/sustained_regime episode-level rules). The `DIMENSION_SPECS` registry makes the mapping explicit; new dimensions must be declared there.

**Filing-time supersession.** When a new evidence record is filed, the service checks for prior records older than `supersession_window_days` (default 30) in the same `(user_id, rule_id, context_key)` lane and records their ids in the new record's `supersedes` list. Lane awareness is strict: global and scoped lanes are distinct; different rule_ids are different lanes. Old records remain in the store as audit history. Belief recompute reads them but excludes them from primary value/confidence/threshold counting; `belief.metadata["superseded_support_count"]` exposes the count.

**Events.** `BeliefService` accepts an optional `event_handler` callable. Six event types fire on real status transitions and recompute markers (never on numeric tweaks): `belief_tentative_created`, `belief_activated`, `belief_staled`, `belief_invalidated`, `belief_recomputed`, `belief_scoped_created`. `NullEventHandler` drops everything (default); `LoggingEventHandler` writes to Python logging; custom handlers wire any persistence/observability strategy. No belief events table exists — events are derived state.

**Explanations.** Computed on demand via `belief_service.explain(belief_id, top_n=5)`, returning a `BeliefExplanation` dataclass. Top supporting and counterevidence ids are ranked deterministically by `(support_score DESC, created_at DESC, memory_id DESC)`. Superseded records are excluded from active counts but reported via `superseded_count`. Explanations are NOT carried on `RuntimeStateView` — consumers fetch on demand.



| Block | Status | Contents |
|-------|--------|----------|
| 1 | ✅ done | packaging, L1 observation, SQLite storage, migration adapter |
| 2 | ✅ done | L2 episodes, closure policies, engine orchestration |
| 3 | ✅ done | L3A evidence, filing rules, idempotency, scope policing |
| 4 | ✅ done | retrieval, canonical state view, deterministic ordering, freshness flags |
| 5 | ✅ done | L3B beliefs, dimension specs, contradiction, staleness, engine wiring |
| 6 | ✅ done | scoped beliefs, cross-scope dimensions, supersession, events, explanations |

## What Blocks 1-6 do NOT deliver

Still deferred to future blocks:

- consolidation (multi-belief arbitration, belief-to-belief reasoning)
- continuous decay curves (Blocks 5-6 do discrete status transitions only)
- pinned evidence (slot reserved in retrieval ordering, always False today)
- LLM prompt builders
- analyst dashboards
- consumer-specific adapters
- finalized semantic memory taxonomy (still soft-canonical)
- learned ranking
- diversity-aware retrieval
- multi-process / multi-worker durability
- LLM-generated belief summaries
- psychometric scoring
- persistent belief event table (events emit via callback, no `belief_events` table)
- agent routing logic, multi-agent arbitration, hidden routing brain
