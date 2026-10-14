"""
Vocabulary Agent - Builds and expands Zolai vocabulary.

This agent:
- Builds vocabulary from Bible corpus
- Learns word relationships (synonyms, antonyms, collocations)
- Manages translation memory
- Collaborates with other agents
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Optional

from .agent_registry import resolve_agent_config
from .base_agent import BaseAgent, AgentMessage, DiscussionMixin
from ..bible_pattern_learner import get_bible_learner

logger = logging.getLogger(__name__)


class VocabularyAgent(BaseAgent, DiscussionMixin):
    """Vocabulary builder agent for Zolai language."""

    def __init__(self, engine=None):
        # Identity, skills and tuning live in the registry (SQLite).
        super().__init__(resolve_agent_config("vocabulary_builder_001", engine), engine)
        
        self.bible_learner = get_bible_learner()
        self.vocabulary: dict = {}
        self.collocations: Counter = Counter()
        self.word_relationships: dict = {}  # synonyms, antonyms, etc.
        self.translation_memory: dict = {}
        
        logger.info("VocabularyBuilder initialized")

    async def process_task(self, task: dict[str, Any]) -> dict[str, Any]:
        """Process a vocabulary task."""
        task_type = task.get("type", "build_vocabulary")
        
        if task_type == "build_vocabulary":
            return await self._build_vocabulary(task)
        elif task_type == "extract_collocations":
            return await self._extract_collocations(task)
        elif task_type == "find_relationships":
            return await self._find_word_relationships(task)
        elif task_type == "get_definition":
            return await self._get_definition(task)
        elif task_type == "expand_vocabulary":
            return await self._expand_vocabulary(task)
        else:
            return {"error": f"Unknown task type: {task_type}"}

    async def handle_message(self, message: AgentMessage) -> Optional[AgentMessage]:
        """Handle messages from other agents."""
        if message.message_type == "get_definition":
            return await self._handle_definition_request(message)
        elif message.message_type == "find_synonyms":
            return await self._handle_synonym_request(message)
        elif message.message_type == "get_collocations":
            return await self._handle_collocation_request(message)
        elif message.message_type == "question":
            return await self._answer_question(message)
        elif message.message_type == "consensus":
            return await self._handle_consensus(message)
        
        return None

    async def _build_vocabulary(self, task: dict[str, Any]) -> dict[str, Any]:
        """Build vocabulary from Bible corpus."""
        limit = task.get("limit", 1000)
        min_frequency = task.get("min_frequency", 1)
        
        learner = get_bible_learner()
        vocab = learner.word_freq.most_common(limit)
        vocab = [(w, c) for w, c in vocab if c >= min_frequency]
        
        # Build detailed vocabulary entries
        vocab_entries = []
        for word, freq in vocab:
            context = self.bible_learner.build_context_for_word(word, limit=2)
            if "No Bible sentences" not in context:
                entry = {
                    "word": word,
                    "frequency": freq,
                    "bible_context": context,
                    "pos": None,  # Would need POS tagger
                    "collocations": [],
                    "synonyms": [],
                    "antonyms": [],
                    "learned_from": "bible",
                }
                self.vocabulary[word] = entry
                vocab_entries.append(entry)
        
        # Extract collocations
        await self._extract_collocations({"limit": 100})
        
        result = {
            "vocabulary_size": len(vocab_entries),
            "min_frequency": min_frequency,
            "entries": vocab_entries[:50],  # Return top 50
        }
        
        self.update_metrics("knowledge_items_learned", len(vocab_entries))
        return result

    async def _extract_collocations(self, task: dict[str, Any]) -> dict[str, Any]:
        """Extract collocations (word pairs) from Bible - optimized version with caching."""
        limit = task.get("limit", 100)
        window_size = task.get("window_size", 5)
        max_verses = task.get("max_verses", 5000)  # Limit verses processed
        
        learner = get_bible_learner()
        
        # Return cached results if available
        if self.collocations:
            top_collocations = self.collocations.most_common(limit)
            return {
                "total_collocations": len(self.collocations),
                "top_collocations": [
                    {"word1": w1, "word2": w2, "frequency": freq}
                    for (w1, w2), freq in top_collocations[:50]
                ],
            }
        
        collocations = Counter()
        verse_count = 0
        
        # Simple collocation extraction from Bible verses (limited)
        for verse in learner.verses:
            if verse_count >= max_verses:
                break
            verse_count += 1
            
            zo = verse.get('zo_tdb77') or ''
            words = zo.lower().split()
            
            for i, word in enumerate(words):
                if len(word) < 2:
                    continue
                # Get surrounding words within window
                start = max(0, i - window_size)
                end = min(len(words), i + window_size + 1)
                context_words = words[start:i] + words[i+1:end]
                
                for cw in context_words:
                    if len(cw) >= 2 and cw != word:
                        pair = tuple(sorted([word, cw]))
                        collocations[pair] += 1
        
        # Store top collocations
        top_collocations = collocations.most_common(limit)
        for (w1, w2), freq in top_collocations:
            if freq >= 2:  # Minimum frequency
                self.collocations[(w1, w2)] = freq
                
        result = {
            "total_collocations": len(collocations),
            "top_collocations": [
                {"word1": w1, "word2": w2, "frequency": freq}
                for (w1, w2), freq in top_collocations[:50]
            ],
        }
        
        return result

    async def _find_word_relationships(self, task: dict[str, Any]) -> dict[str, Any]:
        """Find word relationships (synonyms, antonyms, etc.)."""
        word = task.get("word", "")
        limit = task.get("limit", 10)
        
        if not word:
            return {"error": "No word provided"}
            
        learner = get_bible_learner()
        
        # Get Bible context for the word
        context = learner.build_context_for_word(word, limit=5)
        
        # For now, use collocations as proxy for relationships
        relationships = {
            "synonyms": [],
            "antonyms": [],
            "related": [],
            "collocations": [],
        }
        
        # Find collocations for this word
        for (w1, w2), freq in self.collocations.items():
            if w1 == word:
                relationships["related"].append({"word": w2, "frequency": freq, "type": "collocation"})
            elif w2 == word:
                relationships["related"].append({"word": w1, "frequency": freq, "type": "collocation"})
        
        # Store relationships
        self.word_relationships[word] = relationships
        
        return {
            "word": word,
            "relationships": relationships,
            "bible_context": self.bible_learner.build_context_for_word(word, limit=3),
        }

    async def _get_definition(self, task: dict[str, Any]) -> dict[str, Any]:
        """Get definition for a word."""
        word = task.get("word", "")
        if not word:
            return {"error": "No word provided"}
            
        # Check vocabulary cache
        if word in self.vocabulary:
            return self.vocabulary[word]
            
        # Look up in Bible
        learner = get_bible_learner()
        context = learner.build_context_for_word(word, limit=3)
        
        return {
            "word": word,
            "definition": context if "No Bible sentences" not in context else "Not found in Bible corpus",
            "source": "bible_corpus",
        }

    async def _expand_vocabulary(self, task: dict[str, Any]) -> dict[str, Any]:
        """Expand vocabulary with related words."""
        seed_words = task.get("seed_words", [])
        limit = task.get("limit", 50)
        
        if not seed_words:
            # Use high-frequency words as seeds
            learner = get_bible_learner()
            seed_words = [w for w, _ in learner.word_freq.most_common(20)]
        
        expanded = {}
        for word in seed_words[:10]:
            if word in self.vocabulary:
                continue
            result = await self._get_definition({"word": word})
            expanded[word] = result
            
        return {
            "expanded_count": len(expanded),
            "words": list(expanded.keys()),
        }

    async def _handle_definition_request(self, message: AgentMessage) -> Optional[AgentMessage]:
        """Handle definition request from another agent."""
        word = message.metadata.get("word", "")
        if not word:
            # Try to extract from content
            words = message.content.split()
            for w in words:
                if len(w) > 2:
                    word = w
                    break
        
        if not word:
            return AgentMessage(
                from_agent=self.agent_id,
                to_agent=message.from_agent,
                message_type="error",
                content="No word provided for definition"
            )
            
        result = await self._get_definition({"word": word})
        
        return AgentMessage(
            from_agent=self.agent_id,
            to_agent=message.from_agent,
            message_type="definition",
            content=f"{word}: {result.get('definition', 'Not found')[:200]}",
            metadata={"definition": result}
        )

    async def _handle_synonym_request(self, message: AgentMessage) -> Optional[AgentMessage]:
        """Handle synonym request."""
        word = message.metadata.get("word", "")
        if not word:
            return AgentMessage(
                from_agent=self.agent_id,
                to_agent=message.from_agent,
                message_type="error",
                content="No word provided for synonym search"
            )
            
        # Get relationships
        result = await self._find_word_relationships({"word": word, "limit": 10})
        
        synonyms = [r["word"] for r in result.get("relationships", {}).get("synonyms", [])]
        related = [r["word"] for r in result.get("relationships", {}).get("related", [])[:5]]
        
        return AgentMessage(
            from_agent=self.agent_id,
            to_agent=message.from_agent,
            message_type="synonyms",
            content=f"Synonyms for {word}: {', '.join(synonyms) if synonyms else 'None found'}. Related: {', '.join(related)}",
            metadata={"synonyms": synonyms, "related": related}
        )

    async def _handle_collocation_request(self, message: AgentMessage) -> Optional[AgentMessage]:
        """Handle collocation request."""
        word = message.metadata.get("word", "")
        limit = message.metadata.get("limit", 10)
        
        if not word:
            return AgentMessage(
                from_agent=self.agent_id,
                to_agent=message.from_agent,
                message_type="error",
                content="No word provided for collocation search"
            )
            
        collocations = [
            {"word2": w2, "frequency": freq}
            for (w1, w2), freq in self.collocations.items()
            if w1 == word
        ][:limit]
        
        return AgentMessage(
            from_agent=self.agent_id,
            to_agent=message.from_agent,
            message_type="collocations",
            content=f"Collocations for {word}: {len(collocations)} found",
            metadata={"collocations": collocations}
        )

    async def _answer_question(self, message: AgentMessage) -> Optional[AgentMessage]:
        """Answer vocabulary question."""
        question = message.content
        
        answer = "I can help with Zolai vocabulary: definitions, synonyms, collocations, " \
                 "and word relationships from the Bible corpus."
        
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
        
        consensus = f"VocabularyBuilder on '{topic}': {proposal[:100]}... " \
                   f"(Will verify vocabulary accuracy against Bible corpus)"
        
        return AgentMessage(
            from_agent=self.agent_id,
            to_agent=message.from_agent,
            message_type="consensus_response",
            content=consensus,
            metadata={"topic": topic, "agreed": True, "role": "vocabulary_builder"}
        )

    async def get_vocabulary_stats(self) -> dict[str, Any]:
        """Get vocabulary statistics."""
        learner = get_bible_learner()
        
        return {
            "vocabulary_size": len(self.vocabulary),
            "collocations": len(self.collocations),
            "relationships": len(self.word_relationships),
            "bible_vocab_size": len(learner.word_freq),
            "translation_memory": len(self.translation_memory),
        }
