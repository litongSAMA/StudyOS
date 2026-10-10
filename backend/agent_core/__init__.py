"""Framework-independent StudyOS agent core; host integration must supply access checks."""

from .service import AgentService
from .common import AgentError

__all__ = ["AgentService", "AgentError"]
