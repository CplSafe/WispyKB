# Repository 层导出
# 数据访问层统一入口

from .user_repository import UserRepository
from .role_repository import RoleRepository
from .department_repository import DepartmentRepository
from .knowledge_repository import KnowledgeRepository
from .application_repository import ApplicationRepository
from .chat_repository import ChatRepository

__all__ = [
    'UserRepository',
    'RoleRepository',
    'DepartmentRepository',
    'KnowledgeRepository',
    'ApplicationRepository',
    'ChatRepository',
]
