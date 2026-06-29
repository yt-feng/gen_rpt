from abc import ABC, abstractmethod

class ReportRepository(ABC):
    @abstractmethod
    def find_by_id(self, id: str): pass

class ReviewRepository(ABC):
    @abstractmethod
    def find_by_id(self, id: str): pass

class WorkflowRepository(ABC):
    @abstractmethod
    def find_by_id(self, id: str): pass

class UserRepository(ABC):
    @abstractmethod
    def find_by_id(self, id: str): pass

class CommentRepository(ABC):
    @abstractmethod
    def find_by_id(self, id: str): pass

class VersionRepository(ABC):
    @abstractmethod
    def find_by_id(self, id: str): pass
