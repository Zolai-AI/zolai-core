"""Domain agent runtime (additive) — manifest-backed agents over P3.

Exports the small stable surface used by CLI/API/tests. Internal modules
keep heavier logic; this package never mutates legacy P3 behavior.
"""

from .manifest import AgentManifest, ManifestStatus, TeamManifest, load_manifest_file, load_manifest_index
from .registry import DomainAgentRegistry, get_domain_registry
from .runner import run_domain_agent, run_domain_team

__all__ = [
    "AgentManifest",
    "ManifestStatus",
    "TeamManifest",
    "DomainAgentRegistry",
    "get_domain_registry",
    "load_manifest_file",
    "load_manifest_index",
    "run_domain_agent",
    "run_domain_team",
]
