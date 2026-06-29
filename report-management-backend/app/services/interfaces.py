from abc import ABC, abstractmethod

class ReportService(ABC):
    @abstractmethod
    def get_report(self, report_id: str): pass

class ReviewService(ABC):
    @abstractmethod
    def get_review(self, review_id: str): pass

class WorkflowService(ABC):
    @abstractmethod
    def start_workflow(self, workflow_id: str): pass

class StorageService(ABC):
    @abstractmethod
    def get_file(self, path: str): pass

class UserService(ABC):
    @abstractmethod
    def get_user(self, user_id: str): pass

class PublishingService(ABC):
    @abstractmethod
    def publish(self, report_id: str): pass

class VersionService(ABC):
    @abstractmethod
    def get_version(self, version_id: str): pass

class CommentService(ABC):
    @abstractmethod
    def get_comment(self, comment_id: str): pass
