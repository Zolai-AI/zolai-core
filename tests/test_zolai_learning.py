"""Test suite for Zolai AI learning and understanding."""
from zolai.api.rag_context_v2 import build_zolai_context_v2, get_rag_context_v2
from zolai.cleaner.dict_cleaner import get_dict_cleaner
from zolai.learning.sentence_validator import get_sentence_validator


class TestDictCleaner:
    def test_cleaner_instance(self):
        cleaner = get_dict_cleaner()
        assert cleaner is not None

    def test_clean_entry(self):
        cleaner = get_dict_cleaner()
        # Test with good entry
        good_entry = {'zolai': 'pasian', 'english': 'God', 'source': 'test'}
        result = cleaner._clean_entry(good_entry, 0)
        assert result is not None
        assert result['zolai'] == 'pasian'

    def test_remove_bad_entry(self):
        cleaner = get_dict_cleaner()
        # Test with non-printable chars
        bad_entry = {'zolai': '\x13 embed coreldraw', 'english': 'test', 'source': 'test'}
        result = cleaner._clean_entry(bad_entry, 0)
        assert result is None

    def test_remove_short_entry(self):
        cleaner = get_dict_cleaner()
        # Test with very short headword
        short_entry = {'zolai': 'a', 'english': 'test', 'source': 'test'}
        result = cleaner._clean_entry(short_entry, 0)
        assert result is None


class TestSentenceValidator:
    def test_validator_instance(self):
        validator = get_sentence_validator()
        assert validator is not None

    def test_validate_bible_sentence(self):
        validator = get_sentence_validator()
        # Real Bible sentence
        result = validator.validate("Pasian in vantung leh leitung a piangsak hi.")
        assert result['confidence'] in ['VERIFIED', 'PARTIAL', 'HIGH']
        assert result['in_bible'] or result['partial_match']

    def test_validate_fake_sentence(self):
        validator = get_sentence_validator()
        # Fake sentence
        result = validator.validate("This is not Zolai at all")
        assert result['confidence'] in ['LOW', 'MEDIUM']

    def test_word_authenticity(self):
        validator = get_sentence_validator()
        # Sentence with real Zolai words
        result = validator.validate("Pasian in gam a piangsak hi")
        assert result['word_score'] > 0.5


class TestRAGContextV2:
    def test_rag_v2_instance(self):
        rag = get_rag_context_v2()
        assert rag is not None

    def test_build_context(self):
        context = build_zolai_context_v2("pasian")
        assert isinstance(context, str)
        assert len(context) > 0

    def test_extract_words(self):
        rag = get_rag_context_v2()
        words = rag.extract_zolai_words("Na dam na?")
        assert isinstance(words, list)

    def test_lookup_dictionary(self):
        rag = get_rag_context_v2()
        results = rag.lookup_dictionary("pasian", limit=3)
        assert isinstance(results, list)


def test_all_imports():
    from zolai.api.rag_context_v2 import get_rag_context_v2
    from zolai.cleaner.dict_cleaner import get_dict_cleaner
    from zolai.learning.sentence_validator import get_sentence_validator
    assert callable(get_dict_cleaner)
    assert callable(get_sentence_validator)
    assert callable(get_rag_context_v2)
