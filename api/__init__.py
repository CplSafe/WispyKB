# API 路由模块
# 从 main_pgvector.py 拆分出来的各个路由模块

# 认证路由
from .auth import router as auth_router, get_current_user

# 用户管理路由
from .users import router as users_router

# 用户个人路由
from .profile import router as profile_router

# 角色管理路由
from .roles import router as roles_router

# 部门管理路由
from .departments import router as departments_router

# 审计日志路由
from .audit import router as audit_router

# 知识库路由
from .knowledge import router as knowledge_router

# 应用管理路由
from .applications import router as applications_router, share_router as share_router

# 文档管理路由
from .documents import router as documents_router, pool_router as pool_router

# 聊天路由
from .chat import router as chat_router

# 工作流和任务管理路由
from .workflows import router as workflows_router

# 系统配置路由
from .system import router as system_router

# SSO 单点登录路由
from .sso import router as sso_router

# MCP (Model Context Protocol) 路由
from .mcp import router as mcp_router, mcp_services_router

# 监控路由
from .monitoring import router as monitoring_router

# 向量存储路由
from .vector_store import router as vector_store_router

# 飞书集成路由
from .feishu import router as feishu_router

# Pydantic 模型
from .models import (
    # Enums
    UserRole,
    TaskStatus,
    TaskType,
    PermissionAction,
    ResourceType,
    RetrievalMethod,
    NodeType,
    # Models
    AsyncTask,
    LoginRequest,
    CreateUserRequest,
    UpdateUserRequest,
    AssignRoleRequest,
    CreateRoleRequest,
    UpdateRoleRequest,
    CreateDepartmentRequest,
    UpdateDepartmentRequest,
    GrantResourcePermissionRequest,
    CreateKnowledgeBaseRequest,
    UpdateKnowledgeBaseRequest,
    SearchRequest,
    HybridSearchRequest,
    CreateApplicationRequest,
    UpdateApplicationRequest,
    ChatRequest,
    ChatSessionResponse,
    FeedbackRequest,
    WorkflowNode,
    WorkflowEdge,
    WorkflowDefinition,
    CreateWorkflowRequest,
    CaptchaVerifyRequest,
    SystemConfigRequest,
    SSOConfigRequest,
    OAuthCallbackRequest,
)

__all__ = [
    # Routers
    'auth_router',
    'get_current_user',
    'users_router',
    'profile_router',
    'roles_router',
    'departments_router',
    'audit_router',
    'knowledge_router',
    'applications_router',
    'share_router',
    'documents_router',
    'pool_router',
    'chat_router',
    'workflows_router',
    'system_router',
    'sso_router',
    'mcp_router',
    'mcp_services_router',
    'monitoring_router',
    'vector_store_router',
    'feishu_router',
    # Enums
    'UserRole',
    'TaskStatus',
    'TaskType',
    'PermissionAction',
    'ResourceType',
    'RetrievalMethod',
    'NodeType',
    # Models
    'AsyncTask',
    'LoginRequest',
    'CreateUserRequest',
    'UpdateUserRequest',
    'AssignRoleRequest',
    'CreateRoleRequest',
    'UpdateRoleRequest',
    'CreateDepartmentRequest',
    'UpdateDepartmentRequest',
    'GrantResourcePermissionRequest',
    'CreateKnowledgeBaseRequest',
    'UpdateKnowledgeBaseRequest',
    'SearchRequest',
    'HybridSearchRequest',
    'CreateApplicationRequest',
    'UpdateApplicationRequest',
    'ChatRequest',
    'ChatSessionResponse',
    'FeedbackRequest',
    'WorkflowNode',
    'WorkflowEdge',
    'WorkflowDefinition',
    'CreateWorkflowRequest',
    'CaptchaVerifyRequest',
    'SystemConfigRequest',
    'SSOConfigRequest',
    'OAuthCallbackRequest',
]
