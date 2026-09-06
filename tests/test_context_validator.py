"""Tests for context validation."""
from zolai.learning.context_validator import ContextValidator, get_context_validator


class TestContextValidator:
    def test_singleton(self):
        v1 = get_context_validator()
        v2 = get_context_validator()
        assert v1 is v2

    def test_init_loads(self):
        validator = ContextValidator()
        # Should not crash even if bible data missing
        assert isinstance(validator.bible_contexts, dict)
        assert isinstance(validator.conversation_history, list)

    def test_add_to_conversation(self):
        validator = ContextValidator()
        validator.add_to_conversation('user', 'pasian')
        validator.add_to_conversation('assistant', 'topa')
        assert len(validator.conversation_history) == 2

    def test_conversation_limit(self):
        validator = ContextValidator()
        for i in range(15):
            validator.add_to_conversation('user', f'word {i}')
        assert len(validator.conversation_history) <= 10

    def test_validate_no_history(self):
        validator = ContextValidator()
        result = validator.validate_context('anything')
        assert result['valid'] is True
        assert result['confidence'] == 'UNKNOWN'

    def test_validate_high_overlap(self):
        validator = ContextValidator()
        validator.add_to_conversation('user', 'pasian topa gam')
        result = validator.validate_context('pasian topa gam')
        assert result['valid'] is True
        assert result['confidence'] == 'HIGH'

    def test_validate_medium_overlap(self):
        validator = ContextValidator()
        validator.add_to_conversation('user', 'pasian topa gam')
        result = validator.validate_context('pasian vantung')
        assert result['valid'] is True
        assert result['confidence'] in ('HIGH', 'MEDIUM')

    def test_validate_low_overlap(self):
        validator = ContextValidator()
        validator.add_to_conversation('user', 'pasian topa gam')
        result = validator.validate_context('banana apple orange')
        assert result['valid'] is False
        assert result['confidence'] == 'LOW'

    def test_get_relevant_passage(self):
        validator = ContextValidator()
        # Even with no bible data, should not crash
        result = validator.get_relevant_passage('pasian')
        # Result may be None if no bible data loaded
        assert result is None or isinstance(result, dict)

    def test_build_context_response(self):
        validator = ContextValidator()
        response = validator.build_context_response('pasian topa')
        assert isinstance(response, str)
        assert len(response) > 0

    def test_get_stats(self):
        validator = ContextValidator()
        stats = validator.get_stats()
        assert 'bible_passages' in stats
        assert 'conversation_turns' in stats
        assert isinstance(stats['bible_passages'], int)
        assert isinstance(stats['conversation_turns'], int)
