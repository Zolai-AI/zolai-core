"""
Agent Team Management and Discussion Facilitation.

This module provides:
- AgentTeamManager: Manages teams of agents
- DiscussionFacilitator: Facilitates agent discussions
- Team coordination and task distribution
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .base_agent import BaseAgent, AgentMessage, AgentRole, AgentSkill, DiscussionContext

logger = logging.getLogger(__name__)


@dataclass
class AgentTeam:
    """A team of agents working together."""
    team_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""
    members: list[str] = field(default_factory=list)  # agent_ids
    roles: list[AgentRole] = field(default_factory=list)
    shared_knowledge: dict = field(default_factory=dict)
    discussion_history: list[DiscussionContext] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    status: str = "active"  # active, paused, disbanded


class AgentTeamManager:
    """Manages teams of agents."""
    
    def __init__(self, engine=None):
        self.engine = engine
        self.agents: dict[str, BaseAgent] = {}
        self.teams: dict[str, AgentTeam] = {}
        self.message_bus: asyncio.Queue = asyncio.Queue()
        self.agent_to_team: dict[str, str] = {}
        
        logger.info("AgentTeamManager initialized")

    def register_agent(self, agent: BaseAgent):
        """Register an agent with the team manager."""
        self.agents[agent.agent_id] = agent
        agent.set_message_callback(self._deliver_message)
        logger.info(f"Registered agent: {agent.name} ({agent.agent_id})")

    def unregister_agent(self, agent_id: str):
        """Unregister an agent."""
        if agent_id in self.agents:
            self.agents.pop(agent_id)
            if agent_id in self.agent_to_team:
                team_id = self.agent_to_team.pop(agent_id)
                if team_id in self.teams:
                    self.teams[team_id].members.remove(agent_id)
            logger.info(f"Unregistered agent: {agent_id}")

    def create_team(self, name: str, description: str = "", member_ids: list[str] = None) -> AgentTeam:
        """Create a new team with specified members."""
        team = AgentTeam(
            name=name,
            description=description,
            members=member_ids or [],
        )
        
        for agent_id in member_ids or []:
            if agent_id in self.agents:
                self.agent_to_team[agent_id] = team.team_id
                self.agents[agent_id].team_id = team.team_id
                self.agents[agent_id].config.team_id = team.team_id
                
        self.teams[team.team_id] = team
        logger.info(f"Created team: {name} ({team.team_id}) with {len(team.members)} members")
        return team

    def get_team(self, team_id: str) -> Optional[AgentTeam]:
        """Get a team by ID."""
        return self.teams.get(team_id)

    def get_agent_team(self, agent_id: str) -> Optional[AgentTeam]:
        """Get the team an agent belongs to."""
        team_id = self.agent_to_team.get(agent_id)
        return self.teams.get(team_id) if team_id else None

    async def _deliver_message(self, message: AgentMessage):
        """Deliver a message to the target agent or team."""
        if message.to_agent:
            # Direct message
            if message.to_agent in self.agents:
                await self.agents[message.to_agent].message_queue.put(message)
        elif message.team_id:
            # Broadcast to team
            team = self.teams.get(message.team_id)
            if team:
                for member_id in team.members:
                    if member_id != message.from_agent and member_id in self.agents:
                        await self.agents[member_id].message_queue.put(message)
        else:
            # Broadcast to all agents (not recommended for production)
            for agent in self.agents.values():
                if agent.agent_id != message.from_agent:
                    await agent.message_queue.put(message)

    async def send_to_agent(self, from_agent: str, to_agent: str, 
                           message_type: str, content: str, metadata: dict = None):
        """Convenience method to send a message between agents."""
        message = AgentMessage(
            from_agent=from_agent,
            to_agent=to_agent,
            message_type=message_type,
            content=content,
            metadata=metadata or {}
        )
        await self._deliver_message(message)

    async def broadcast_to_team(self, from_agent: str, team_id: str,
                               message_type: str, content: str, metadata: dict = None):
        """Broadcast a message to a team."""
        message = AgentMessage(
            from_agent=from_agent,
            team_id=team_id,
            message_type=message_type,
            content=content,
            metadata=metadata or {}
        )
        await self._deliver_message(message)

    def get_team_skills(self, team_id: str) -> list[AgentSkill]:
        """Get all skills available in a team."""
        team = self.teams.get(team_id)
        if not team:
            return []
            
        skills = set()
        for agent_id in team.members:
            if agent_id in self.agents:
                skills.update(self.agents[agent_id].skills)
        return list(skills)

    def get_team_roles(self, team_id: str) -> list[AgentRole]:
        """Get all roles in a team."""
        team = self.teams.get(team_id)
        if not team:
            return []
        return list(set(
            self.agents[agent_id].role
            for agent_id in team.members
            if agent_id in self.agents
        ))


class DiscussionFacilitator:
    """Facilitates discussions between agents."""
    
    def __init__(self, team_manager: AgentTeamManager):
        self.team_manager = team_manager
        self.active_discussions: dict[str, DiscussionContext] = {}
        
        logger.info("DiscussionFacilitator initialized")

    async def start_discussion(self, team_id: str, topic: str, 
                              initiator_id: str, initial_message: str = "") -> DiscussionContext:
        """Start a new discussion in a team."""
        team = self.team_manager.get_team(team_id)
        if not team:
            raise ValueError(f"Team {team_id} not found")
            
        context = DiscussionContext(
            topic=topic,
            participants=team.members.copy(),
        )
        
        self.active_discussions[topic] = context
        
        # Notify all participants
        if initial_message:
            await self.team_manager.broadcast_to_team(
                initiator_id,
                team_id,
                "discussion",
                f"DISCUSSION STARTED: {topic}\n\n{initial_message}",
            )
        
        logger.info(f"Started discussion '{topic}' in team {team_id} by {initiator_id}")
        return context

    async def add_message(self, topic: str, message: AgentMessage):
        """Add a message to a discussion."""
        if topic in self.active_discussions:
            self.active_discussions[topic].messages.append(message)
            
            # Broadcast to participants
            team_id = message.team_id
            if team_id:
                team = self.team_manager.get_team(message.team_id)
                if team:
                    for member_id in team.participants:
                        if member_id != message.from_agent:
                            if member_id in self.team_manager.agents:
                                await self.team_manager.agents[member_id].message_queue.put(message)

    async def conclude_discussion(self, topic: str, facilitator_id: str, 
                                 consensus: str = "") -> Optional[DiscussionContext]:
        """Conclude a discussion with optional consensus."""
        if topic not in self.active_discussions:
            return None
            
        context = self.active_discussions[topic]
        context.consensus = consensus
        context.status = "concluded"
        
        # Notify participants of conclusion
        if context.messages:
            await self.team_manager.broadcast_to_team(
                facilitator_id,
                context.messages[0].team_id,
                "discussion_concluded",
                f"DISCUSSION CONCLUDED: {topic}\n\nConsensus: {consensus or 'No consensus reached'}",
            )
        
        # Archive and remove from active
        del self.active_discussions[topic]
        logger.info(f"Concluded discussion '{topic}' with consensus: {bool(consensus)}")
        return context

    async def build_consensus(self, topic: str) -> str:
        """Build consensus from discussion messages."""
        if topic not in self.active_discussions:
            return "No active discussion on this topic"
            
        context = self.active_discussions[topic]
        messages = context.messages
        
        if not messages:
            return "No messages in discussion"
            
        # Simple consensus building - summarize all positions
        positions = defaultdict(list)
        for msg in messages:
            if msg.message_type in ("discussion", "consensus", "position"):
                positions[msg.from_agent].append(msg.content)
        
        if not positions:
            return "No clear positions expressed"
            
        # Build consensus summary
        summary = f"CONSENSUS SUMMARY for '{topic}':\n\n"
        for agent, points in positions.items():
            summary += f"\n{agent}:\n"
            for point in points:
                summary += f"  - {point[:200]}\n"
                
        return summary

    def get_discussion_status(self, topic: str) -> Optional[dict]:
        """Get status of a discussion."""
        if topic not in self.active_discussions:
            return None
            
        ctx = self.active_discussions[topic]
        return {
            "topic": ctx.topic,
            "participants": ctx.participants,
            "message_count": len(ctx.messages),
            "status": ctx.status,
            "consensus": ctx.consensus,
            "created_at": ctx.created_at.isoformat(),
        }


# Convenience function to create a standard learning team
def create_standard_learning_team(team_manager: AgentTeamManager, 
                                  engine=None) -> AgentTeam:
    """Create a standard learning team with all core agents."""
    from .bible_learner_agent import BibleLearnerAgent
    from .translation_agent import TranslationAgent
    from .grammar_agent import GrammarAgent
    from .vocabulary_agent import VocabularyAgent
    
    # Create agents
    bible_learner = BibleLearnerAgent(engine)
    translator = TranslationAgent(engine)
    grammar_expert = GrammarAgent(engine)
    vocab_builder = VocabularyAgent(engine)
    
    # Register all agents
    for agent in [bible_learner, translator, grammar_expert, vocab_builder]:
        team_manager.register_agent(agent)
    
    # Create team
    member_ids = [bible_learner.agent_id, translator.agent_id, 
                  grammar_expert.agent_id, vocab_builder.agent_id]
    
    team = team_manager.create_team(
        name="Zolai Learning Team",
        description="Standard learning team with Bible learner, translator, grammar expert, and vocabulary builder",
        member_ids=member_ids
    )
    
    return team
