"""
Integration test: full L1 → L2 pipeline via CMSEngine.

Proves end-to-end:
  - text → observation → episode
  - observations persisted to observations table
  - episodes persisted to episodes table
  - episode.obs_ids references map to real observations in the store
  - session isolation works across the full stack
  - end_session() flushes pending episodes

This is the Block 2 equivalent of the Block 1 equivalence test: it
validates the architectural assembly, not just individual components.
"""

from itertools import count

import pytest

from cms.l1.adapter import LegacyExtractorAdapter
from cms.l1.service import ObservationService
from cms.l2.policies import WindowedClosurePolicy
from cms.l2.service import EpisodeService
from cms.runtime.engine import CMSEngine
from cms.storage.episode_store import EpisodeStore
from cms.storage.observation_store import ObservationStore
from cms.storage.schema import FULL_SCHEMA_DDL
from cms.storage.sqlite import SQLiteBackend


class MinimalExtractor:
    """Minimal feature extractor for integration tests.

    Returns deterministic features with a small perturbation based on
    text hash — enough variance to keep surprise signals meaningful,
    but not so much that tests become flaky.
    """

    def extract_sentence_features(self, sentence: str) -> dict[str, float]:
        # Small hash-based perturbation for variance
        perturb = (hash(sentence) % 1000) / 10000.0  # 0.0 to 0.1
        return {
            "semantic_density": 0.5 + perturb,
            "pragmatic_load": 0.3,
            "epistemic_certainty": 0.7,
            "temporal_orientation": 0.5,
            "topic_concreteness": 0.6,
            "intent_direction": 0.5,
        }


# ── fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def backend():
    be = SQLiteBackend(":memory:")
    be.bootstrap_schema(FULL_SCHEMA_DDL)
    yield be
    be.close()


@pytest.fixture
def engine(backend):
    obs_store = ObservationStore(backend)
    ep_store = EpisodeStore(backend)

    adapter = LegacyExtractorAdapter(MinimalExtractor())

    # Separate counters for obs and ep ids
    obs_counter = count(0)
    ep_counter = count(0)

    obs_service = ObservationService(
        adapter=adapter,
        store=obs_store,
        id_factory=lambda: f"obs_{next(obs_counter):03d}",
    )
    ep_service = EpisodeService(
        store=ep_store,
        policy=WindowedClosurePolicy(max_size=5),
        id_factory=lambda: f"ep_{next(ep_counter):03d}",
    )

    eng = CMSEngine(obs_service, ep_service)
    return eng, obs_store, ep_store


# ── end-to-end pipeline ──────────────────────────────────────────────


CORPUS = [
    "The server is running correctly today.",
    "We shipped the release this morning.",
    "All tests are passing now.",
    "The database migration completed.",
    "Monitoring shows normal activity.",  # 5th triggers close at max_size=5
    "Starting deployment review now.",     # begins new episode
    "The architecture review went well.",
    "Performance metrics look good.",
    "Documentation has been updated.",
    "Team retrospective tomorrow morning.", # 10th triggers close
]


class TestPipeline:
    def test_observations_persisted_for_each_turn(self, engine):
        eng, obs_store, _ = engine
        for i, text in enumerate(CORPUS):
            eng.process_turn("alice", "s1", f"t{i}", text)
        assert obs_store.count_for_user("alice") == len(CORPUS)

    def test_episodes_closed_at_window_boundary(self, engine):
        eng, _, ep_store = engine
        closed_episodes = []
        for i, text in enumerate(CORPUS):
            result = eng.process_turn("alice", "s1", f"t{i}", text)
            if result.closed_episode is not None:
                closed_episodes.append(result.closed_episode)

        # 10 turns, max_size=5 → close on 5th and 10th turns
        # After 5th: closes episode with 4 obs, 5th starts new
        # After 10th: closes episode with 4 more obs (6th-9th), 10th starts new
        assert len(closed_episodes) >= 1
        # All closed episodes should have been persisted
        for ep in closed_episodes:
            assert ep_store.get(ep.episode_id) is not None

    def test_turn_result_reports_open_size(self, engine):
        eng, _, _ = engine
        # First 4 turns don't close
        for i in range(4):
            result = eng.process_turn("alice", "s1", f"t{i}", CORPUS[i])
            assert result.closed_episode is None
            assert result.open_episode_size == i + 1

    def test_episode_obs_ids_reference_real_observations(self, engine):
        eng, obs_store, _ = engine
        for i, text in enumerate(CORPUS):
            result = eng.process_turn("alice", "s1", f"t{i}", text)
            if result.closed_episode is not None:
                # Every obs_id in the episode should exist in the store
                for obs_id in result.closed_episode.obs_ids:
                    assert obs_store.get(obs_id) is not None, (
                        f"Episode references missing obs_id: {obs_id}"
                    )

    def test_observation_count_matches_turn_count(self, engine):
        eng, obs_store, _ = engine
        for i, text in enumerate(CORPUS):
            eng.process_turn("alice", "s1", f"t{i}", text)
        # Each turn produces exactly one observation
        assert obs_store.count_for_user("alice") == len(CORPUS)

    def test_no_observation_appears_in_two_episodes(self, engine):
        eng, _, ep_store = engine
        for i, text in enumerate(CORPUS):
            eng.process_turn("alice", "s1", f"t{i}", text)

        all_episodes = ep_store.list_for_user("alice")
        seen_obs_ids = set()
        for ep in all_episodes:
            for obs_id in ep.obs_ids:
                assert obs_id not in seen_obs_ids, (
                    f"Observation {obs_id} appears in multiple episodes"
                )
                seen_obs_ids.add(obs_id)


class TestSessionIsolation:
    def test_multiple_sessions_do_not_share_episodes(self, engine):
        eng, _, ep_store = engine
        # 5 obs in s1, 3 obs in s2
        for i in range(5):
            eng.process_turn("alice", "s1", f"s1_t{i}", CORPUS[i])
        for i in range(3):
            eng.process_turn("alice", "s2", f"s2_t{i}", CORPUS[i + 5])

        s1_episodes = ep_store.list_for_session("alice", "s1")
        s2_episodes = ep_store.list_for_session("alice", "s2")

        # s1 had a closure at turn 5 (max_size=5)
        # s2 only has 3 obs, still open
        assert len(s1_episodes) >= 1
        assert len(s2_episodes) == 0

        # No overlap in obs_ids across sessions
        s1_obs = {oid for ep in s1_episodes for oid in ep.obs_ids}
        s2_obs = {oid for ep in s2_episodes for oid in ep.obs_ids}
        assert s1_obs.isdisjoint(s2_obs)

    def test_multi_user_isolation(self, engine):
        eng, _, ep_store = engine
        for i in range(5):
            eng.process_turn("alice", "s1", f"a_t{i}", CORPUS[i])
        for i in range(5):
            eng.process_turn("bob", "s1", f"b_t{i}", CORPUS[i])

        alice_eps = ep_store.list_for_user("alice")
        bob_eps = ep_store.list_for_user("bob")

        # Each user got closure at 5 obs
        assert all(ep.user_id == "alice" for ep in alice_eps)
        assert all(ep.user_id == "bob" for ep in bob_eps)


class TestEndSession:
    def test_end_session_flushes_open_episode(self, engine):
        eng, _, ep_store = engine
        # Process 3 turns (below window of 5) — no automatic close
        for i in range(3):
            result = eng.process_turn("alice", "s1", f"t{i}", CORPUS[i])
            assert result.closed_episode is None

        # End session flushes
        flushed = eng.end_session("alice", "s1")
        assert flushed is not None
        assert flushed.length == 3
        assert flushed.closure_reason == "flush"
        assert ep_store.get(flushed.episode_id) is not None

    def test_end_session_with_no_open_episode(self, engine):
        eng, _, _ = engine
        assert eng.end_session("alice", "never_existed") is None

    def test_end_session_after_natural_closure(self, engine):
        """Calling end_session after an episode just closed should handle cleanly."""
        eng, _, _ = engine
        # 5 turns triggers close; 5th obs starts new episode with 1 obs
        for i in range(5):
            eng.process_turn("alice", "s1", f"t{i}", CORPUS[i])

        # The new episode should have 1 observation; end_session flushes it
        flushed = eng.end_session("alice", "s1")
        assert flushed is not None
        assert flushed.length == 1


class TestConsumerNeutrality:
    """The engine output should be usable without picking a consumer format."""

    def test_turn_result_is_structured_data(self, engine):
        eng, _, _ = engine
        result = eng.process_turn("alice", "s1", "t0", "Hello.")

        # Canonical access — no LLM-specific, no dashboard-specific
        assert result.observation is not None
        assert result.observation.user_id == "alice"
        assert hasattr(result, "closed_episode")
        assert hasattr(result, "open_episode_size")

    def test_can_build_multiple_consumer_views_from_same_result(self, engine):
        """Smoke test: same TurnResult projects cleanly into different shapes."""
        eng, _, _ = engine
        result = eng.process_turn("alice", "s1", "t0", "Hello.")

        # LLM-style projection (hypothetical)
        llm_view = {
            "text": result.observation.raw_text,
            "cms_summary": result.observation.cms_real[0],
        }
        # Dashboard-style projection (hypothetical)
        dashboard_view = {
            "user": result.observation.user_id,
            "session": result.observation.session_id,
            "open": result.open_episode_size,
            "closed_episode_id": (
                result.closed_episode.episode_id
                if result.closed_episode else None
            ),
        }

        assert llm_view["text"] == "Hello."
        assert dashboard_view["user"] == "alice"
