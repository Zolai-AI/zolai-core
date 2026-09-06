"""Tests for Bible pattern learning and sentence building."""
import pytest
from zolai.learning.bible_pattern_learner import BiblePatternLearner, get_bible_learner
from zolai.learning.sentence_builder import SentenceBuilder, get_sentence_builder


class TestBiblePatternLearner:
    def test_load_bible(self):
        learner = get_bible_learner()
        assert len(learner.verses) > 29000  # 31K minus ~900 None

    def test_extract_patterns(self):
        learner = get_bible_learner()
        total = sum(len(plist) for plist in learner.patterns.values())
        assert total > 29000

    def test_pattern_types_exist(self):
        learner = get_bible_learner()
        assert 'sov' in learner.patterns
        assert len(learner.patterns['sov']) > 0

    def test_get_patterns_for_type(self):
        learner = get_bible_learner()
        sov = learner.get_patterns_for_type('sov', limit=5)
        assert len(sov) <= 5
        assert all(p.example_zo for p in sov)

    def test_build_context_for_word(self):
        learner = get_bible_learner()
        ctx = learner.build_context_for_word('pasian', limit=2, max_tokens=200)
        assert 'pasian' in ctx.lower()
        assert 'Bible' in ctx

    def test_build_pattern_context(self):
        learner = get_bible_learner()
        ctx = learner.build_pattern_context('sov', limit=2, max_tokens=200)
        assert len(ctx) > 0

    def test_word_freq(self):
        learner = get_bible_learner()
        top = learner.word_freq.most_common(10)
        assert len(top) > 0
        # 'in' should be very common
        words = [w for w, c in top]
        assert 'in' in words

    def test_pattern_stats(self):
        learner = get_bible_learner()
        stats = learner.get_pattern_stats()
        assert stats['total_verses'] > 29000
        assert 'pattern_counts' in stats


class TestSentenceBuilder:
    def test_build_from_pattern(self):
        builder = get_sentence_builder()
        result = builder.build_from_pattern('sov')
        assert result['success']
        assert result['sentence']
        assert result['english']
        assert result['source'] == 'Bible (attested)'

    def test_get_real_example(self):
        builder = get_sentence_builder()
        result = builder.get_real_example('pasian')
        assert result['success']
        assert 'pasian' in result['sentence'].lower()

    def test_build_with_word(self):
        builder = get_sentence_builder()
        result = builder.build_with_word('vantung')
        assert result['success']

    def test_list_pattern_types(self):
        builder = get_sentence_builder()
        types = builder.list_pattern_types()
        assert 'sov' in types
        assert len(types) >= 3

    def test_get_pattern_count(self):
        builder = get_sentence_builder()
        counts = builder.get_pattern_count()
        assert 'sov' in counts
        assert counts['sov'] > 0


def test_imports():
    from zolai.learning import get_bible_learner, get_sentence_builder
    assert callable(get_bible_learner)
    assert callable(get_sentence_builder)
