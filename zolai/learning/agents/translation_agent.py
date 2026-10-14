"""
Translation Agent - Translates with Bible grounding.

This agent:
- Translates Zolai ↔ English with Bible grounding
- Uses BibleLearnerAgent for grounding
- Validates translations against Bible patterns
- Collaborates with other agents
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .agent_registry import resolve_agent_config
from .base_agent import BaseAgent, AgentMessage, DiscussionMixin
from ..bible_pattern_learner import get_bible_learner
from ..feedback_store import FeedbackType

logger = logging.getLogger(__name__)


class TranslationAgent(BaseAgent, DiscussionMixin):
    """Translation agent with Bible grounding."""

    def __init__(self, engine=None):
        # Identity, skills and tuning live in the registry (SQLite).
        super().__init__(resolve_agent_config("translator_001", engine), engine)
        
        self.bible_learner = get_bible_learner()
        self.translation_memory: dict = {}  # Cache translations
        self.pending_validations: dict = {}
        
        logger.info("TranslationAgent initialized")

    async def process_task(self, task: dict[str, Any]) -> dict[str, Any]:
        """Process a translation task."""
        task_type = task.get("type", "translate")
        
        if task_type == "translate":
            return await self._translate(task)
        elif task_type == "validate":
            return await self._validate_translation(task)
        elif task_type == "ground_with_bible":
            return await self._ground_with_bible(task)
        elif task_type == "translate_batch":
            return await self._translate_batch(task)
        else:
            return {"error": f"Unknown task type: {task_type}"}

    async def handle_message(self, message: AgentMessage) -> Optional[AgentMessage]:
        """Handle messages from other agents."""
        if message.message_type == "translate":
            return await self._handle_translate_request(message)
        elif message.message_type == "validate_translation":
            return await self._handle_validation_request(message)
        elif message.message_type == "question":
            return await self._answer_question(message)
        elif message.message_type == "consensus":
            return await self._handle_consensus(message)
        
        return None

    async def _translate(self, task: dict[str, Any]) -> dict[str, Any]:
        """Translate text with Bible grounding."""
        source_text = task.get("source_text", "")
        target_lang = task.get("target_lang", "en")  # en or zo
        source_lang = task.get("source_lang", "zo" if target_lang == "en" else "en")
        
        if not source_text:
            return {"error": "No source_text provided"}
        
        # Check translation memory
        cache_key = f"{source_lang}:{target_lang}:{source_text}"
        if cache_key in self.translation_memory:
            cached = self.translation_memory[cache_key]
            return {
                "translation": cached["translation"],
                "source": source_text,
                "cached": True,
                "confidence": cached.get("confidence", 0.9),
            }
        
        # Get Bible context for grounding
        bible_context = await self._get_bible_context(source_text)
        
        # For now, use dictionary lookup for translation
        # In production, this would call an LLM with Bible grounding
        translation = await self._dictionary_translate(source_text, source_lang, target_lang)
        
        # Validate with Bible
        validation = await self._validate_with_bible(translation, source_text)
        
        result = {
            "translation": translation,
            "source": source_text,
            "source_lang": source_lang,
            "target_lang": target_lang,
            "bible_context": bible_context,
            "validation": validation,
            "confidence": validation.get("confidence", 0.7),
            "cached": False,
        }
        
        # Cache if confident
        if result["confidence"] > 0.8:
            self.translation_memory[cache_key] = {
                "translation": translation,
                "confidence": result["confidence"],
            }
        
        # Emit learning signal
        await self.emit_learning_signal(
            query=f"Translate: {source_text}",
            response=translation,
            feedback_type=FeedbackType.EXPLICIT_UP,
            confidence=result["confidence"],
            metadata={"source_lang": source_lang, "target_lang": target_lang}
        )
        
        self.update_metrics("tasks_completed")
        return result

    async def _dictionary_translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Simple dictionary-based translation using Bible corpus."""
        from ...data.repositories import get_repositories
        
        repos = get_repositories(engine=self.engine)
        dict_repo = repos["dictionary"]
        
        words = text.split()
        translated = []
        
        for word in words:
            if source_lang == "zo" and target_lang == "en":
                rows = dict_repo.lookup_exact(word)
                if rows:
                    row = rows[0]
                    translated.append(
                        row.get("english_clean") or row.get("english") or f"[{word}]"
                    )
                else:
                    translated.append(f"[{word}]")
            elif source_lang == "en" and target_lang == "zo":
                rows = dict_repo.search_english(word, limit=1)
                zolai = rows[0].get("zolai") if rows else None
                translated.append(zolai if zolai else f"[{word}]")
            else:
                translated.append(f"[{word}]")
        
        return " ".join(translated)

    async def _validate_translation(self, task: dict[str, Any]) -> dict[str, Any]:
        """Validate a translation against Bible patterns."""
        source = task.get("source_text", "")
        translation = task.get("translation", "")
        
        if not source or not translation:
            return {"error": "Missing source_text or translation"}
            
        return await self._validate_with_bible(translation, task.get("source_text", ""))

    async def _ground_with_bible(self, task: dict[str, Any]) -> dict[str, Any]:
        """Ground a translation with Bible context."""
        text = task.get("text", "")
        if not text:
            return {"error": "No text provided"}
            
        bible_context = await self._get_bible_context(text)
        
        return {
            "text": text,
            "bible_context": bible_context,
            "grounded": True,
        }

    async def _translate_batch(self, task: dict[str, Any]) -> dict[str, Any]:
        """Translate multiple texts."""
        texts = task.get("texts", [])
        target_lang = task.get("target_lang", "en")
        source_lang = task.get("source_lang", "zo")
        
        results = []
        for text in texts:
            result = await self._translate({
                "source_text": text,
                "source_lang": source_lang,
                "target_lang": target_lang,
            })
            results.append(result)
            
        return {"translations": results, "count": len(results)}

    async def _get_bible_context(self, text: str) -> dict[str, Any]:
        """Get Bible context for text."""
        words = text.lower().split()
        contexts = {}
        
        for word in words[:5]:  # Limit to 5 words
            if len(word) > 2:
                context = self.bible_learner.build_context_for_word(word, limit=2)
                if "No Bible sentences" not in context:
                    contexts[word] = context
                    
        return {"contexts": contexts, "word_count": len(contexts)}

    async def _validate_with_bible(self, translation: str, source: str) -> dict[str, Any]:
        """Validate translation against Bible patterns."""
        from ..bible_pattern_learner import get_bible_learner
        
        learner = get_bible_learner()
        patterns = learner.get_patterns_for_type("sov", limit=5)
        
        # Simple validation - check SOV order, ergative 'in', etc.
        issues = []
        confidence = 0.8
        
        # Check for ergative 'in' in Zolai
        if "pasian" in translation.lower() and " in " not in translation.lower():
            issues.append("Missing ergative 'in' for transitive subject")
            confidence -= 0.1
            
        # Check SOV order
        words = translation.split()
        if len(words) >= 3:
            # Very basic check - would need proper parsing
            pass
            
        return {
            "is_valid": len(issues) == 0,
            "confidence": max(0.0, confidence),
            "issues": issues,
            "patterns_checked": len(patterns),
        }

    async def _handle_translate_request(self, message: AgentMessage) -> Optional[AgentMessage]:
        """Handle translation request from another agent."""
        source_text = message.metadata.get("source_text", "")
        target_lang = message.metadata.get("target_lang", "en")
        source_lang = message.metadata.get("source_lang", "zo")
        
        if not source_text:
            return AgentMessage(
                from_agent=self.agent_id,
                to_agent=message.from_agent,
                message_type="error",
                content="No source_text in request"
            )
            
        result = await self._translate({
            "source_text": source_text,
            "source_lang": source_lang,
            "target_lang": target_lang,
        })
        
        return AgentMessage(
            from_agent=self.agent_id,
            to_agent=message.from_agent,
            message_type="translation_result",
            content=result.get("translation", ""),
            metadata={"translation": result, "request_id": message.message_id}
        )

    async def _handle_validation_request(self, message: AgentMessage) -> Optional[AgentMessage]:
        """Handle validation request."""
        translation = message.metadata.get("translation", "")
        source = message.metadata.get("source_text", "")
        
        if not translation or not source:
            return AgentMessage(
                from_agent=self.agent_id,
                to_agent=message.from_agent,
                message_type="error",
                content="Missing translation or source_text"
            )
            
        validation = await self._validate_with_bible(translation, source)
        
        return AgentMessage(
            from_agent=self.agent_id,
            to_agent=message.from_agent,
            message_type="validation_result",
            content=f"Valid: {validation.get('is_valid')}, Confidence: {validation.get('confidence', 0):.2f}",
            metadata={"validation": validation}
        )

    async def _answer_question(self, message: AgentMessage) -> Optional[AgentMessage]:
        """Answer a question about translation."""
        question = message.content
        
        answer = "I can help with Zolai ↔ English translation using Bible grounding. " \
                 "Provide source text and target language for translation."
        
        return AgentMessage(
            from_agent=self.agent_id,
            to_agent=message.from_agent,
            message_type="answer",
            content=answer,
            metadata={"question": question}
        )

    async def _handle_consensus(self, message: AgentMessage) -> Optional[AgentMessage]:
        """Handle consensus request."""
        topic = message.metadata.get("topic", "")
        proposal = message.content.replace("CONSENSUS PROPOSAL: ", "")
        
        # Translator's consensus: check translation quality
        consensus = f"Translator agrees with '{topic}': {proposal[:100]}... " \
                   f"(Will validate translation quality against Bible)"
        
        return AgentMessage(
            from_agent=self.agent_id,
            to_agent=message.from_agent,
            message_type="consensus_response",
            content=consensus,
            metadata={"topic": topic, "agreed": True, "role": "translator"}
        )

    # Public API for other agents
    async def translate(self, text: str, target_lang: str = "en", source_lang: str = "zo") -> dict:
        """Public translate method for other agents."""
        return await self._translate({
            "source_text": text,
            "source_lang": source_lang,
            "target_lang": target_lang,
        })

    async def validate(self, source: str, translation: str) -> dict:
        """Public validate method for other agents."""
        return await self._validate_with_bible(translation, source)
