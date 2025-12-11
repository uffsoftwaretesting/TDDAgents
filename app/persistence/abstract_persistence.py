from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List

from app.config import AgentState

class PersistenceStrategy(ABC):
    """Abstract base class for persistence strategies following DIP."""
    
    @abstractmethod
    def save(self, key: str, data: AgentState) -> None:
        """Save data to the persistence layer."""
        pass
    
    @abstractmethod
    def load(self, key: str) -> AgentState:
        """Load data from the persistence layer."""
        pass
    
    @abstractmethod
    def delete(self, key: str) -> None:
        """Delete data from the persistence layer."""
        pass
    
    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check if key exists in the persistence layer."""
        pass
    
    @abstractmethod
    def clear_all(self) -> None:
        """Clear all data from the persistence layer."""
        pass
    
    # High-level methods for TDD workflow state management
    def save_state(self, task_key: str, state: AgentState) -> None:
        """
        Save TDD workflow state.
        
        Args:
            task_key: Unique identifier for the task
            state: Complete state dictionary
        """
        key = f"state:{task_key}"
        self.save(key, state)
    
    def load_state(self, task_key: str) -> Optional[AgentState]:
        """
        Load TDD workflow state.
        
        Args:
            task_key: Unique identifier for the task
            
        Returns:
            State dictionary or None if not found
        """
        key = f"state:{task_key}"
        if not self.exists(key):
            return None
        return self.load(key)
    
    def delete_state(self, task_key: str) -> None:
        """
        Delete TDD workflow state.
        
        Args:
            task_key: Unique identifier for the task
        """
        key = f"state:{task_key}"
        self.delete(key)
    
    def list_tasks(self) -> List[str]:
        """
        List all saved task keys.
        
        Returns:
            List of task keys
        """
        # This method should be implemented by concrete classes
        raise NotImplementedError("list_tasks must be implemented by concrete persistence classes")


class VectorPersistenceStrategy(ABC):
    """Abstract base class for vector database operations."""
    
    @abstractmethod
    def store_embedding(self, key: str, vector: List[float], metadata: Dict[str, Any]) -> None:
        """Store a vector embedding with metadata."""
        pass
    
    @abstractmethod
    def search_similar(self, vector: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
        """Search for similar vectors."""
        pass
    
    @abstractmethod
    def delete_embedding(self, key: str) -> None:
        """Delete a vector embedding."""
        pass
