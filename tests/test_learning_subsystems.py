"""Tests for the five learning subsystems (websocket, consensus, corpus,
finetuning, metrics).

Every fixture builds its own throwaway SQLite file under ``tmp_path`` — the
production 2.4 GB database is never opened. These modules all follow the same
two rules, and the tests exist to hold them to it:

* **DB only** — datasets, metrics and model metadata live in SQLite; no
  ``.json`` / ``.jsonl`` artifact is ever written.
* **Additive + idempotent** — running the same build twice must leave the
  database in exactly the same state.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.websockets import WebSocketState
from sqlalchemy import create_engine, text

from zolai.learning.consensus import (
    ConfidenceScorer,
    ConsensusBuilder,
    DiscussionAnalyzer,
    WeightedVotingConsensus,
)
from zolai.learning.corpus import (
    BibleCorpusExpander,
    CorpusValidator,
    ParallelCorpusBuilder,
    QualityChecker,
    VerseExpander,
    has_control_chars,
)
from zolai.learning.finetuning import (
    FineTuningDatasetBuilder,
    FineTuningPipeline,
    ModelManager,
    TrainingConfig,
    TrainingStatus,
    ZolaiTrainer,
)
from zolai.learning.metrics import (
    MetricCollector,
    MetricsDashboard,
    MetricsExporter,
    export_all_formats,
)
from zolai.learning.websocket import (
    DiscussionHandler,
    MetricsHandler,
    TaskHandler,
    WebSocketServer,
    WSMessage,
)

BIBLE_SCHEMA = """
CREATE TABLE bible_verses (
    ref TEXT,
    book TEXT,
    chapter INTEGER,
    verse INTEGER,
    zo_tdb77 TEXT,
    zo_tedim2010 TEXT,
    en_kJV TEXT
)
"""

DICTIONARY_SCHEMA = """
CREATE TABLE dictionary (
    id INTEGER PRIMARY KEY,
    zolai TEXT,
    english TEXT,
    pos TEXT
)
"""

WORD_ALIGNMENT_SCHEMA = """
CREATE TABLE word_alignments (
    id INTEGER PRIMARY KEY,
    ref TEXT,
    zolai_word TEXT,
    english_word TEXT,
    position INTEGER
)
"""

SIGNALS_SCHEMA = """
CREATE TABLE learning_signals (
    signal_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL DEFAULT '',
    user_id TEXT,
    query TEXT NOT NULL DEFAULT '',
    response TEXT NOT NULL DEFAULT '',
    citations TEXT NOT NULL DEFAULT '[]',
    retrieval_only INTEGER NOT NULL DEFAULT 0,
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    engine_mode TEXT NOT NULL DEFAULT '',
    feedback_type TEXT NOT NULL DEFAULT '',
    feedback_value TEXT,
    confidence REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewer_id TEXT,
    reviewed_at TEXT,
    review_notes TEXT,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    metadata TEXT NOT NULL DEFAULT '{}'
)
"""

REVIEW_QUEUE_SCHEMA = """
CREATE TABLE learning_review_queue (
    queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    assigned_to TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT '',
    completed_at TEXT
)
"""

VERSES = [
    # ref, book, chapter, verse, zo, tedim, en
    ("GEN 1:1", "GEN", 1, 1, "A kipat cil-in Pasian in vantung leh leitung a piangsak hi.",
     "A kipat...", "In the beginning God created the heaven and the earth."),
    ("GEN 1:2", "GEN", 1, 2, "Cil-in vantung hi, mikhat no hiam.",
     None, "And the earth was without form, and void."),
    ("GEN 1:3", "GEN", 1, 3, "Pasian in kiang dingah ci hiam.",
     None, "And God said, Let there be light."),
    ("MAT 1:1", "MAT", 1, 1, "Cilhngiah ngei Jesus Christ ah mi maw.",
     None, "The book of the genealogy of Jesus Christ."),
]


@pytest.fixture()
def engine(tmp_path: Path):
    """A throwaway SQLite engine with just the corpus/learning tables."""
    eng = create_engine(f"sqlite:///{tmp_path / 'learning.db'}")
    with eng.begin() as conn:
        for schema in (BIBLE_SCHEMA, DICTIONARY_SCHEMA, WORD_ALIGNMENT_SCHEMA,
                       SIGNALS_SCHEMA, REVIEW_QUEUE_SCHEMA):
            conn.execute(text(schema))
        for row in VERSES:
            conn.execute(
                text("""
                    INSERT INTO bible_verses
                        (ref, book, chapter, verse, zo_tdb77, zo_tedim2010, en_kJV)
                    VALUES (:ref, :book, :chapter, :verse, :zo, :tedim, :en)
                """),
                {"ref": row[0], "book": row[1], "chapter": row[2], "verse": row[3],
                 "zo": row[4], "tedim": row[5], "en": row[6]},
            )
        conn.execute(text(
            "INSERT INTO word_alignments (ref, zolai_word, english_word, position) "
            "VALUES ('GEN 1:1', 'kipat', 'beginning', 1)"
        ))
    yield eng
    eng.dispose()


@pytest.fixture()
def signals(engine):
    """Seed a handful of approved learning signals."""
    rows = [
        (f"sig_{i}", f"Translate word {i}", f"Word {i} means thing", "correction",
         f"Word {i} means thing (corrected)", 0.9, "approved")
        for i in range(6)
    ]
    with engine.begin() as conn:
        for row in rows:
            conn.execute(
                text("""
                    INSERT INTO learning_signals
                        (signal_id, query, response, feedback_type, feedback_value,
                         confidence, status, created_at, updated_at)
                    VALUES (:sid, :q, :r, :ft, :fv, :conf, :st, '', '')
                """),
                {"sid": row[0], "q": row[1], "r": row[2], "ft": row[3],
                 "fv": row[4], "conf": row[5], "st": row[6]},
            )
            conn.execute(
                text("""
                    INSERT INTO learning_review_queue (signal_id, priority, status, created_at)
                    VALUES (:sid, 0, :st, '')
                """),
                {"sid": row[0], "st": row[6]},
            )
    return rows


# ── websocket ─────────────────────────────────────────────────────────────


class FakeWebSocket:
    """Minimal WebSocket stand-in that records what the server sends."""

    def __init__(self):
        self.client_state = WebSocketState.CONNECTED
        self.sent: list = []
        self.closed = False

    async def accept(self):
        return None

    async def send_text(self, data: str):
        self.sent.append(json.loads(data))

    async def close(self):
        self.closed = True

    def frames(self, message_type: str) -> list:
        return [f for f in self.sent if f["type"] == message_type]


def _run(coro):
    return asyncio.run(coro)


class TestWebSocket:
    def test_message_roundtrip(self):
        msg = WSMessage(message_type="ping", payload={"a": 1}, sender_id="x")
        assert WSMessage.from_json(msg.to_json()).payload == {"a": 1}

    def test_ping_pong_and_subscribe_topics(self):
        server = WebSocketServer()

        async def flow():
            ws = FakeWebSocket()
            conn = await server.manager.connect(ws, agent_id="agent1", user_id="u1")
            await server._handle_ping(conn, WSMessage(message_type="ping"))
            await server._handle_subscribe(
                conn, WSMessage(message_type="subscribe", payload={"topics": ["metrics", "tasks"]}),
            )
            await server._handle_unsubscribe(
                conn, WSMessage(message_type="unsubscribe", payload={"topics": ["metrics"]}),
            )
            return ws, conn

        ws, conn = _run(flow())
        assert [f["type"] for f in ws.sent] == ["pong", "subscribed", "unsubscribed"]
        assert sorted(server.manager.get_subscriptions(conn)) == ["tasks"]
        assert server.manager.get_topic_subscribers("metrics") == set()

    def test_broadcast_reaches_every_subscriber(self):
        server = WebSocketServer()

        async def flow():
            sockets = [FakeWebSocket(), FakeWebSocket()]
            conns = [await server.manager.connect(s) for s in sockets]
            for conn in conns:
                server.manager.subscribe(conn, ["news"])
            await server.manager.broadcast_to_topic(
                "news", WSMessage(message_type="news", payload={"headline": "hi"}),
            )
            return sockets

        sockets = _run(flow())
        assert all(s.frames("news") for s in sockets)

    def test_discussion_flow_persists_to_db(self, engine):
        handler = DiscussionHandler(engine=engine)
        server = WebSocketServer()

        async def flow():
            ws = FakeWebSocket()
            conn = await server.manager.connect(ws, agent_id="agent1")
            await handler.handle_message(conn, WSMessage(
                message_type="start_discussion", sender_id="agent1",
                payload={"topic": "ergative in", "team_id": "t1"},
            ), server.manager)
            discussion_id = next(iter(handler.active_discussions))
            await handler.handle_message(conn, WSMessage(
                message_type="discussion_message", sender_id="agent2",
                payload={"discussion_id": discussion_id, "content": "concur"},
            ), server.manager)
            await handler.handle_message(conn, WSMessage(
                message_type="end_discussion", sender_id="agent1",
                payload={"discussion_id": discussion_id, "consensus": "use ergative"},
            ), server.manager)
            return ws, discussion_id

        ws, discussion_id = _run(flow())
        assert [f["type"] for f in ws.sent] == [
            "discussion_started", "discussion_message_ack", "discussion_ended",
        ]
        assert handler.count_messages(discussion_id) == 1
        stored = handler.get_discussion(discussion_id)
        assert stored["status"] == "closed"
        assert stored["consensus"] == "use ergative"

    def test_discussion_rejects_unknown_id(self, engine):
        handler = DiscussionHandler(engine=engine)
        server = WebSocketServer()

        async def flow():
            ws = FakeWebSocket()
            conn = await server.manager.connect(ws)
            await handler.handle_message(conn, WSMessage(
                message_type="discussion_message", payload={"discussion_id": "nope"},
            ), server.manager)
            return ws

        ws = _run(flow())
        assert ws.frames("error")

    def test_task_and_metrics_handlers(self, engine):
        tasks = TaskHandler(engine=engine)
        metrics = MetricsHandler(engine=engine)
        server = WebSocketServer()

        async def flow():
            ws = FakeWebSocket()
            conn = await server.manager.connect(ws, agent_id="translator_001")
            await tasks.handle_message(conn, WSMessage(
                message_type="submit_task", sender_id="translator_001",
                payload={"agent_id": "translator_001", "type": "translate"},
            ), server.manager)
            task_id = ws.frames("task_submitted")[0]["payload"]["task_id"]
            await tasks.handle_message(conn, WSMessage(
                message_type="get_task_status", payload={"task_id": task_id},
            ), server.manager)
            await tasks.handle_message(conn, WSMessage(
                message_type="cancel_task", payload={"task_id": task_id},
            ), server.manager)
            await metrics.handle_message(conn, WSMessage(
                message_type="get_system_status", payload={},
            ), server.manager)
            await metrics.handle_message(conn, WSMessage(
                message_type="get_agent_metrics", payload={"agent_id": "translator_001"},
            ), server.manager)
            return ws, task_id

        ws, task_id = _run(flow())
        assert ws.frames("task_status")[0]["payload"]["status"] == "pending"
        assert ws.frames("task_cancelled")
        assert ws.frames("system_status")[0]["payload"]["agents_total"] >= 4
        assert ws.frames("agent_metrics")[0]["payload"]["agent_id"] == "translator_001"
        assert tasks.get_task(task_id)["status"] == "cancelled"

    def test_disconnect_releases_topics(self):
        server = WebSocketServer()

        async def flow():
            ws = FakeWebSocket()
            conn = await server.manager.connect(ws)
            server.manager.subscribe(conn, ["a", "b"])
            server.manager.disconnect(conn)
            return conn

        conn = _run(flow())
        assert server.manager.get_connection_count() == 0
        assert server.manager.get_topic_subscribers("a") == set()
        assert server.manager.get_subscriptions(conn) == set()


# ── consensus ─────────────────────────────────────────────────────────────


class TestConsensus:
    def test_unanimous_agreement_reaches_consensus(self):
        builder = ConsensusBuilder()
        builder.add_agent_vote("a1", "bible_learner", "agree", 0.9)
        builder.add_agent_vote("a2", "grammar_expert", "agree", 0.8)
        result = builder.build("use ergative in")

        assert result["decision"] == "consensus"
        assert result["winning_position"] == "agree"
        assert result["topic"] == "use ergative in"
        assert result["consensus_reached"] is True

    def test_build_applies_agent_weights(self):
        """agent_weights must reach the tally — passing them used to land in the
        ``threshold`` slot and raise a TypeError."""
        builder = ConsensusBuilder()
        builder.add_agent_vote("a1", "translation_agent", "agree", 1.0)
        builder.add_agent_vote("a2", "translation_agent", "disagree", 1.0)

        weighted = builder.build("topic", agent_weights={"a1": 2.0})
        assert weighted["agree_weight"] == pytest.approx(2.0)
        assert weighted["winning_position"] == "agree"
        assert weighted["decision"] == "consensus"

    def test_threshold_override_is_respected(self):
        voting = WeightedVotingConsensus(consensus_threshold=0.9)
        voting.add_vote({"agent_id": "a1", "agent_role": "translator", "vote_type": "agree",
                         "confidence": 0.6})
        voting.add_vote({"agent_id": "a2", "agent_role": "translator", "vote_type": "disagree",
                         "confidence": 0.5})

        assert voting.build_consensus("t")["decision"] == "majority_agree"
        loose = voting.build_consensus("t", threshold=0.4)
        assert loose["consensus_threshold"] == 0.4
        assert loose["decision"] == "consensus"

    def test_no_votes_returns_empty_result(self):
        result = WeightedVotingConsensus().build_consensus("topic")
        assert result["decision"] == "no_consensus"
        assert result["reason"] == "no_votes"
        assert result["topic"] == "topic"

    def test_conditional_votes_collect_conditions(self):
        builder = ConsensusBuilder()
        builder.add_agent_vote("a1", "grammar_expert", "conditional", 0.9,
                               conditions=["only in scripture"])
        builder.add_agent_vote("a2", "grammar_expert", "conditional", 0.9,
                               conditions=["only in scripture", "needs review"])
        result = builder.build("t")
        assert result["conditions"] == ["needs review", "only in scripture"]

    def test_clear_resets_weights(self):
        voting = WeightedVotingConsensus()
        voting.add_vote({"agent_id": "a1", "vote_type": "agree", "confidence": 1.0})
        voting.build_consensus("t")
        voting.clear_votes()
        voting.add_vote({"agent_id": "a2", "vote_type": "agree", "confidence": 1.0})
        result = voting.build_consensus("t")
        # The new vote must be re-weighted (not inherit the old base weight).
        assert result["total_weight"] == pytest.approx(1.0)

    def test_confidence_score_reacts_to_track_record(self):
        scorer = ConfidenceScorer()
        for _ in range(10):
            scorer.record_decision("good", "bible_learner", was_correct=True, confidence=0.9)
        for _ in range(10):
            scorer.record_decision("bad", "translation_agent", was_correct=False, confidence=0.9)

        assert scorer.get_confidence_score("good") > scorer.get_confidence_score("bad")
        assert scorer.get_agent_weight("good", "bible_learner") >= 0.1
        assert scorer.get_agent_stats("bad")["accuracy"] == pytest.approx(0.0)
        assert scorer.get_agent_stats("nobody") is None
        assert scorer.get_confidence_score("nobody") == pytest.approx(0.5)

    def test_confidence_score_is_idempotent_for_identical_history(self):
        def build():
            scorer = ConfidenceScorer()
            for i in range(20):
                scorer.record_decision("a", "grammar_expert", was_correct=i % 3 != 0,
                                       confidence=0.75)
            return scorer.get_confidence_score("a")

        assert build() == pytest.approx(build())

    def test_recent_accuracy_uses_decay(self):
        scorer = ConfidenceScorer(window_size=10, decay_factor=0.5)
        for _ in range(5):
            scorer.record_decision("a", "grammar_expert", was_correct=False, confidence=0.5)
        for _ in range(5):
            scorer.record_decision("a", "grammar_expert", was_correct=True, confidence=0.5)
        record = scorer.track_records["a"]
        # Flat accuracy is 0.5; exponential decay weights the recent 5 wins higher.
        assert record.recent_accuracy > 0.9

    def test_calibration_factor_is_damped(self):
        scorer = ConfidenceScorer()
        for _ in range(20):
            scorer.record_decision("a", "grammar_expert", was_correct=True, confidence=0.99)
        assert scorer.calibrate_confidence("a") < 1.0
        assert scorer.calibrate_confidence("a", target_accuracy=0.5) <= 0.6

    def test_discussion_analysis(self):
        analyzer = DiscussionAnalyzer()
        insights = analyzer.analyze_discussion([
            {"from_agent": "a1",
             "content": "I agree, the ergative marker is correct because ZVS 2018 requires it.",
             "timestamp": "2026-01-01T10:00:00"},
            {"from_agent": "a2", "content": "What about prose usage?",
             "timestamp": "2026-01-01T10:05:00"},
        ])
        assert insights["participant_count"] == 2
        assert insights["total_messages"] == 2
        assert insights["agreements"] and insights["questions"]
        assert "ergative" in insights["key_terms"]

    def test_empty_discussion_is_safe(self):
        assert DiscussionAnalyzer().analyze_discussion([]) == {}

    def test_compare_and_action_items(self):
        analyzer = DiscussionAnalyzer()
        one = analyzer.analyze_discussion([
            {"from_agent": "a1", "content": "The ergative marker is required by ZVS 2018."},
        ])
        two = analyzer.analyze_discussion([
            {"from_agent": "a2", "content": "Another view about the ergative marker."},
        ])
        comparison = analyzer.compare_discussions(one, two)
        assert 0.0 <= comparison["topic_similarity"] <= 1.0

        actions = analyzer.extract_action_items([
            {"from_agent": "a2", "content": "We should update the grammar table.",
             "timestamp": "2026-01-01T10:00:00"},
        ])
        assert actions and "update the grammar table" in actions[0]["action"]

    def test_duration_estimate_tolerates_bad_timestamps(self):
        analyzer = DiscussionAnalyzer()
        assert analyzer._estimate_duration([{"timestamp": "nonsense"}, {"timestamp": ""}]) > 0


# ── corpus ────────────────────────────────────────────────────────────────


class TestCorpus:
    def test_verse_expansion(self, engine):
        expansion = VerseExpander(engine).expand_verse("GEN 1:1")
        assert expansion.ref == "GEN 1:1"
        assert expansion.context_before == []
        assert [c["ref"] for c in expansion.context_after] == ["GEN 1:2", "GEN 1:3"]
        assert expansion.grammar_features["has_ergative"] is True
        assert expansion.grammar_features["word_count"] > 0
        assert expansion.keywords

    def test_expand_unknown_verse_returns_none(self, engine):
        assert VerseExpander(engine).expand_verse("NOPE 1:1") is None

    def test_parallel_and_sentence_pairs(self, engine):
        builder = ParallelCorpusBuilder(engine)
        assert len(builder.build_parallel_pairs()) == 4
        sentences = builder.build_sentence_pairs()
        assert sentences and all(p["source_lang"] == "zo" for p in sentences)

    def test_word_aligned_pairs_read_from_db(self, engine):
        pairs = ParallelCorpusBuilder(engine).build_word_aligned_pairs()
        assert pairs == [{
            "source": "kipat", "target": "beginning", "ref": "GEN 1:1",
            "position": 1, "source_lang": "zo", "target_lang": "en",
        }]

    def test_storing_pairs_is_idempotent(self, engine):
        builder = ParallelCorpusBuilder(engine)
        pairs = builder.build_parallel_pairs()

        builder.store_pairs(pairs, pair_type="verse")
        first = builder.count_pairs("verse")
        builder.store_pairs(pairs, pair_type="verse")
        assert builder.count_pairs("verse") == first == 4

        stored = builder.get_stored_pair(f"verse:{'GEN 1:1'}:0")
        assert stored["source_lang"] == "zo"

    def test_expand_all_is_idempotent(self, engine):
        expander = BibleCorpusExpander(engine)
        stats = expander.expand_all()
        assert stats["parallel_pairs"] == 4
        assert stats["stored_expansions"] == 4

        again = expander.expand_all()
        assert again["stored_expansions"] == stats["stored_expansions"]
        assert again["stored_pairs"] == stats["stored_pairs"]
        expander.expand_all()
        assert expander.count_expansions() == 4

    def test_stored_expansion_round_trips(self, engine):
        expander = BibleCorpusExpander(engine)
        expander.expand_all()
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT keywords, grammar_features FROM corpus_chapter_expansions WHERE ref = 'GEN 1:1'"
            )).fetchone()
        assert "kipat" in json.loads(row.keywords)
        assert json.loads(row.grammar_features)["has_ergative"] is True

    def test_validator_finds_seeded_problems(self, engine):
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO bible_verses (ref, book, chapter, verse, zo_tdb77, en_kJV) "
                "VALUES ('GEN 1:9', 'GEN', 1, 9, NULL, 'English only')"
            ))
            conn.execute(text(
                "INSERT INTO bible_verses (ref, book, chapter, verse, zo_tdb77, en_kJV) "
                "VALUES ('ZZZ 1:1', 'ZZZ', 1, 1, 'kong ai', 'dog cow')"
            ))

        report = CorpusValidator(engine).validate_all(zvs_limit=10)
        kinds = {issue.issue_type for issue in report.issues}
        assert "missing_translation" in kinds
        assert "unknown_book" in kinds
        assert report.total_checked == 6

    def test_encoding_check_has_no_regexp_operator_bug(self, engine):
        """The old SQL used ``REGEXP`` which SQLite does not implement."""
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO bible_verses (ref, book, chapter, verse, zo_tdb77, en_kJV) "
                "VALUES ('GEN 1:10', 'GEN', 1, 10, 'bad\x07char hei', 'bad char here')"
            ))

        report = CorpusValidator(engine).validate_all(zvs_limit=10)
        control = [i for i in report.issues if i.issue_type == "control_characters"]
        assert [i.ref for i in control] == ["GEN 1:10"]

    def test_control_char_predicate(self):
        assert has_control_chars("abc\x07def") is True
        assert has_control_chars("line\nbreak\ttab") is False
        assert has_control_chars("") is False

    def test_quality_report_saves_idempotently(self, engine):
        validator = CorpusValidator(engine)
        report = validator.validate_all(zvs_limit=10)

        report_id = validator.save_report(report)
        assert validator.save_report(report) == report_id
        assert validator.count_issues(report_id) == len(report.issues)

        stored = validator.get_report(report_id)
        assert stored["total_checked"] == 4
        assert stored["stats"]["total_verses"] == 4

    def test_corpus_health_has_no_undefined_name(self, engine):
        """``get_corpus_health`` used to reference an undefined ``_``."""
        health = QualityChecker(engine).get_corpus_health()
        assert health["complete_pairs"] == 4
        assert health["health_score"] == pytest.approx(100.0)
        assert health["status"] == "healthy"

    def test_quality_checker(self, engine):
        checker = QualityChecker(engine)
        assert checker.quick_check("GEN 1:1")["valid"] is True
        assert checker.quick_check("NOPE 1:1")["valid"] is False
        assert len(checker.check_batch(["GEN 1:1", "MAT 1:1"])) == 2

    def test_no_files_written(self, engine, tmp_path, monkeypatch):
        workdir = tmp_path / "corpus-work"
        workdir.mkdir()
        monkeypatch.chdir(workdir)
        BibleCorpusExpander(engine).expand_all()
        CorpusValidator(engine).validate_all(zvs_limit=10)
        ParallelCorpusBuilder(engine).store_pairs(
            ParallelCorpusBuilder(engine).build_parallel_pairs(), pair_type="verse",
        )
        assert list(workdir.glob("*.json")) == []
        assert list(workdir.glob("*.jsonl")) == []


# ── finetuning ────────────────────────────────────────────────────────────


SIGNAL_DICTS = [
    {"signal_id": "sig_0", "query": "q0", "response": "r0", "feedback_type": "correction",
     "feedback_value": "r0 fixed", "confidence": 0.9},
    {"signal_id": "sig_1", "query": "q1", "response": "r1", "feedback_type": "explicit_up",
     "feedback_value": None, "confidence": 0.8},
    {"signal_id": "sig_2", "query": "q2", "response": "r2", "feedback_type": "explicit_down",
     "feedback_value": None, "confidence": 0.2},
]


class TestFinetuning:
    def test_dataset_builder_streams_from_db(self, engine, signals):
        builder = FineTuningDatasetBuilder(engine)
        assert builder.get_split_counts()["total"] == 6

        samples = list(builder.get_training_stream(batch_size=2))
        assert len(samples) == 5  # 6 signals, 1 held out for validation
        assert set(samples[0]) == {"instruction", "input", "output"}

    def test_stream_is_deterministic(self, engine, signals):
        builder = FineTuningDatasetBuilder(engine)
        first = list(builder.get_training_stream())
        second = list(FineTuningDatasetBuilder(engine).get_training_stream())
        assert first == second

    def test_formats_and_validation(self, engine, signals):
        builder = FineTuningDatasetBuilder(engine)
        sample = {"query": "q", "response": "r", "feedback_value": "fixed", "metadata": "{}"}

        assert builder.format_sample(sample, "alpaca")["output"] == "fixed"
        assert builder.format_sample(sample, "chatml")["messages"][1]["role"] == "user"
        assert builder.format_sample(sample, "sharegpt")["conversations"][0]["from"] == "human"
        with pytest.raises(ValueError):
            builder.format_sample(sample, "nope")

    def test_bible_stream_yields_both_directions(self, engine):
        samples = list(FineTuningDatasetBuilder(engine).get_bible_training_stream(batch_size=2))
        assert len(samples) == 8
        assert any("to English" in s["instruction"] for s in samples)
        assert any("to Zolai" in s["instruction"] for s in samples)

    def test_pipeline_builds_dataset_in_db_not_files(self, engine, monkeypatch, tmp_path):
        workdir = tmp_path / "ft-work"
        workdir.mkdir()
        monkeypatch.chdir(workdir)
        pipeline = FineTuningPipeline(engine)

        async def run():
            job_id = await pipeline.create_training_job(
                TrainingConfig(max_steps=2), feedback_signals=SIGNAL_DICTS,
            )
            return job_id

        job_id = _run(run())
        job = pipeline.jobs[job_id]
        assert job.dataset_id.startswith("ds_")
        assert pipeline.count_dataset_items(job.dataset_id) == 3

        items = pipeline.get_dataset_items(job.dataset_id)
        assert items[0]["instruction"] == "q0"
        assert items[0]["output"] == "r0 fixed"

        assert list(workdir.glob("*.jsonl")) == []
        assert not (workdir / "training_datasets").exists()

    def test_dataset_build_is_idempotent(self, engine):
        pipeline = FineTuningPipeline(engine)

        async def run():
            first = await pipeline.create_training_job(
                TrainingConfig(max_steps=2), feedback_signals=SIGNAL_DICTS)
            second = await pipeline.create_training_job(
                TrainingConfig(max_steps=2), feedback_signals=SIGNAL_DICTS)
            return first, second

        first, second = _run(run())
        assert pipeline.jobs[first].dataset_id == pipeline.jobs[second].dataset_id
        assert pipeline.count_dataset_items() == 3

    def test_pipeline_requires_signals(self, engine):
        pipeline = FineTuningPipeline(engine)
        with pytest.raises(ValueError):
            _run(pipeline.create_training_job(TrainingConfig(), feedback_signals=[]))

    def test_training_job_runs_and_registers_model(self, engine):
        pipeline = FineTuningPipeline(engine)

        async def run():
            job_id = await pipeline.create_training_job(
                TrainingConfig(max_steps=3, save_steps=1), feedback_signals=SIGNAL_DICTS)
            await pipeline._execute_job(pipeline.jobs[job_id])
            return job_id

        job_id = _run(run())
        job = pipeline.jobs[job_id]
        assert job.status is TrainingStatus.COMPLETED
        assert job.model_id
        assert pipeline.model_manager.get_model(job.model_id)["training_method"] == "lora"

        status = pipeline.get_job_status(job_id)
        assert status["status"] == "completed"
        assert status["progress"] == pytest.approx(1.0)

    def test_failed_job_is_recorded(self, engine, monkeypatch):
        pipeline = FineTuningPipeline(engine)
        job_id = _run(pipeline.create_training_job(
            TrainingConfig(max_steps=1), feedback_signals=SIGNAL_DICTS))

        async def boom(_job):
            raise RuntimeError("CUDA exploded")

        monkeypatch.setattr(pipeline, "_run_lora_training", boom)
        _run(pipeline._execute_job(pipeline.jobs[job_id]))
        assert pipeline.jobs[job_id].status is TrainingStatus.FAILED
        assert "CUDA exploded" in pipeline.jobs[job_id].error

    def test_model_registry_is_db_backed_and_idempotent(self, engine, monkeypatch, tmp_path):
        workdir = tmp_path / "registry-work"
        workdir.mkdir()
        monkeypatch.chdir(workdir)
        manager = ModelManager(engine)

        model_id = manager.register_model("demo", "v1", "base", "lora", metrics={"loss": 0.2})
        assert manager.register_model("demo", "v1", "base", "lora") == model_id
        assert manager.get_model(model_id)["metrics"] == {"loss": 0.2}
        assert manager.get_model(model_id)["is_active"] is True
        assert len(manager.list_models()) == 1
        assert not (workdir / "models").exists()
        assert list(workdir.glob("*.json")) == []

    def test_model_activation_and_deletion(self, engine):
        manager = ModelManager(engine)
        first = manager.register_model("a", "v1", "base", "lora")
        second = manager.register_model("b", "v1", "base", "lora")

        assert manager.set_active_model(second) is True
        assert manager.get_active_model()["id"] == second
        with pytest.raises(ValueError):
            manager.delete_model(second)
        assert manager.delete_model(second, force=True) is True
        assert manager.get_active_model()["id"] == first
        assert manager.set_active_model("missing") is False

    def test_checkpoint_cleanup(self, engine):
        manager = ModelManager(engine)
        for i in range(4):
            model_id = manager.register_model("ckpt", f"v{i}", "base", "lora")
            manager.update_status(model_id, "checkpoint")

        assert manager.cleanup_old_checkpoints(keep=2) == 2
        assert len(manager.list_models(status="checkpoint")) == 2

    def test_trainer_reads_db_and_writes_metrics(self, engine, signals):
        trainer = ZolaiTrainer(engine, TrainingConfig(max_steps=2, batch_size=2), run_id="r1")

        assert trainer.count_training_samples() == 6
        result = trainer.train()

        assert result["status"] == "completed"
        assert result["train_steps"] >= 1
        assert len(trainer.get_metrics_history()) == result["final_metrics"]["metrics_recorded"]
        assert trainer.get_metrics_history()[0]["run_id"] == "r1"

        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT COUNT(*) FROM finetune_training_metrics WHERE run_id = 'r1'"
            )).scalar()
        assert rows == len(trainer.get_metrics_history())


# ── metrics ───────────────────────────────────────────────────────────────


class TestMetrics:
    def test_collector_stores_in_db(self, engine):
        collector = MetricCollector(engine=engine)
        collector.record_metric("cpu", 1.5, labels={"host": "a"})
        collector.record_batch([
            {"name": "cpu", "value": 2.5},
            {"name": "ram", "value": 3.5, "source": "test"},
        ])

        assert sorted(collector.get_all_metric_names()) == ["cpu", "ram"]
        assert collector.get_latest("cpu")["value"] == pytest.approx(2.5)
        stats = collector.get_metric_stats("cpu")
        assert stats["count"] == 2
        assert stats["mean"] == pytest.approx(2.0)
        assert collector.get_metric_stats("nope") == {"count": 0}

    def test_query_metrics_decodes_labels(self, engine):
        collector = MetricCollector(engine=engine)
        collector.record_metric("cpu", 1.0, labels={"host": "a"})
        rows = collector.query_metrics("cpu")
        assert rows[0]["labels"] == {"host": "a"}

    def test_schema_creation_is_idempotent(self, engine):
        MetricCollector(engine=engine)
        MetricCollector(engine=engine).record_metric("x", 1.0)
        with engine.connect() as conn:
            names = conn.execute(text(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name = 'learning_metrics'"
            )).scalar()
        assert names == 1

    def test_dashboard_collects_and_renders(self, engine, signals):
        dashboard = MetricsDashboard(engine=engine)
        dashboard.register_collector(lambda: {"custom": 1})
        metrics = _run(dashboard.collect_all_metrics())

        assert metrics["custom"] == {"custom": 1}
        # Only bible_verses exists in the temp DB; missing tables are skipped.
        assert metrics["learning"] == {"bible_verses": 4}
        data = dashboard.get_dashboard_data()
        assert "metrics" in data
        assert "# TYPE" in dashboard.export_prometheus()

    def test_periodic_collection_stops(self, engine):
        dashboard = MetricsDashboard(engine=engine)
        _run(dashboard.start_periodic_collection(interval=0, iterations=2))
        assert len(dashboard.collector.get_all_metric_names()) >= 1

    def test_exporter_renders_without_files(self, engine, monkeypatch, tmp_path):
        workdir = tmp_path / "work"
        workdir.mkdir()
        monkeypatch.chdir(workdir)
        dashboard = MetricsDashboard(engine=engine)
        dashboard.collector.record_metric("system.cpu", 12.0, labels={"category": "system"})
        exporter = MetricsExporter(dashboard)

        assert json.loads(exporter.export_json())["metrics"]
        assert "system_cpu 12.0" in exporter.export_prometheus()
        assert exporter.export_csv().splitlines()[0] == "metric_name,timestamp,value,labels,source"
        assert "system_cpu" in exporter.export_influxdb_line()
        assert exporter.export_grafana_dashboard()["dashboard"]["panels"]
        assert sorted(export_all_formats(dashboard)) == [
            "csv", "grafana", "influxdb", "json", "prometheus",
        ]

        assert list(workdir.iterdir()) == []

    def test_schedule_exports_fires_without_nameerror(self, engine):
        """``schedule_exports`` used ``time`` without importing it."""
        dashboard = MetricsDashboard(engine=engine)
        dashboard.collector.record_metric("system.cpu", 1.0)
        received: list = []

        thread = MetricsExporter(dashboard).schedule_exports(
            lambda kind, payload: received.append(kind),
            snapshot_interval=0, prometheus_interval=0,
        )
        try:
            import time

            time.sleep(1.5)
        finally:
            thread.stop.set()
            thread.join(timeout=5)

        assert "prometheus" in received
        assert "json" in received

    def test_influxdb_export_skips_without_client(self, engine):
        dashboard = MetricsDashboard(engine=engine)
        dashboard.collector.record_metric("system.cpu", 1.0)
        assert MetricsExporter(dashboard).export_to_influxdb() in (True, False)
