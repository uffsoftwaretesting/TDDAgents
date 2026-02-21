from typing import Optional
from langgraph.persistence.checkpointer import Checkpointer
from app.persistence.abstract_persistence import PersistenceStrategy
from app.persistence.redis_persistence import RedisPersistence
from app.persistence.memory_persistence import InMemoryPersistence
from app.config import Config



class PersistenceFactory:
    """Factory for creating persistence strategy instances."""
    
    @staticmethod
    def create_persistence(
        strategy: str,
        specific_url: Optional[str] = None
    ) -> PersistenceStrategy:
        """
        Create a persistence strategy instance.
        
        Args:
            strategy: Type of persistence ("redis" or "memory")
            specific_url: Specific connection URL (only for redis strategy)
        
        Returns:
            PersistenceStrategy instance
        
        Raises:
            ValueError: If strategy is not supported
        """
        if strategy == "redis":
            return RedisPersistence(specific_url)
        elif strategy == "memory":
            return InMemoryPersistence()
        elif strategy == "postgres":
            return Checkpointer(Config.POSTGRES_URL)
        else:
            raise ValueError(f"Unsupported persistence strategy: {strategy}")