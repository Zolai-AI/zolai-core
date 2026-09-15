"""Tests for the Gemini model router and configs."""

from __future__ import annotations

from zolai.llm.gemini.models import (
    FLASH,
    PRO,
    PRO_PLUS,
    ModelConfig,
    ModelRouter,
)


class TestModelConfig:
    def test_flash_config(self):
        assert FLASH.name == "gemini-2.5-flash"
        assert FLASH.temperature == 0.1
        assert FLASH.max_tokens == 2048

    def test_pro_config(self):
        assert PRO.name == "gemini-2.5-pro"
        assert PRO.temperature == 0.2
        assert PRO.max_tokens == 4096

    def test_pro_plus_config(self):
        assert PRO_PLUS.name == "gemini-2.5-pro"
        assert PRO_PLUS.temperature == 0.3
        assert PRO_PLUS.max_tokens == 8192

    def test_frozen(self):
        # Dataclass is frozen — assignment should raise
        import pytest
        with pytest.raises(AttributeError):
            FLASH.temperature = 0.5  # type: ignore[misc]


class TestModelRouter:
    def test_high_complexity_always_pro_plus(self):
        router = ModelRouter()
        result = router.route("word", complexity="high")
        assert result is PRO_PLUS
        result = router.route("sentence", complexity="high")
        assert result is PRO_PLUS

    def test_grammar_always_pro_plus(self):
        router = ModelRouter()
        result = router.route("grammar", complexity="low")
        assert result is PRO_PLUS
        result = router.route("grammar", complexity="medium")
        assert result is PRO_PLUS

    def test_word_with_dictionary_evidence_uses_flash(self):
        router = ModelRouter()
        result = router.route("word", complexity="low", evidence_tiers=("dictionary",))
        assert result is FLASH

    def test_word_with_bible_evidence_uses_flash(self):
        router = ModelRouter()
        result = router.route("word", complexity="medium", evidence_tiers=("bible",))
        assert result is FLASH

    def test_word_without_evidence_uses_pro(self):
        router = ModelRouter()
        result = router.route("word", complexity="medium", evidence_tiers=())
        assert result is PRO

    def test_sentence_uses_pro(self):
        router = ModelRouter()
        result = router.route("sentence", complexity="medium")
        assert result is PRO

    def test_translation_uses_pro(self):
        router = ModelRouter()
        result = router.route("translation", complexity="medium")
        assert result is PRO

    def test_default_route(self):
        router = ModelRouter()
        result = router.route("word")
        # No evidence tiers → falls through to sentence check → returns PRO
        assert result is PRO

    def test_custom_models(self):
        custom_flash = ModelConfig(name="custom-flash", temperature=0.0)
        custom_pro = ModelConfig(name="custom-pro", temperature=0.5)
        custom_pro_plus = ModelConfig(name="custom-pro-plus", temperature=0.9)
        router = ModelRouter(flash=custom_flash, pro=custom_pro, pro_plus=custom_pro_plus)

        assert router.route("sentence", complexity="medium") is custom_pro
        assert router.route("grammar", complexity="low") is custom_pro_plus
        assert router.route("word", complexity="low", evidence_tiers=("dictionary",)) is custom_flash
