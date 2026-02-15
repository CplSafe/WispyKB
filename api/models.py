# API Pydantic 模型
# 从 main_pgvector.py 提取的请求/响应模型

from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from enum import Enum


# ==================== 枚举类型 ====================

class UserRole(str, Enum):
    """用户角色枚举"""
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskType(str, Enum):
    """任务类型"""
    DOCUMENT_UPLOAD = "document_upload"  # 文档上传处理
    DOCUMENT_INDEX = "document_index"    # 文档索引/重索引
    KNOWLEDGE_BASE_DELETE = "kb_delete"  # 知识库删除
    BATCH_DELETE = "batch_delete"        # 批量删除
    EMBEDDING_GENERATION = "embedding_gen"  # 向量生成


class PermissionAction(str, Enum):
    """权限操作类型"""
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"
    EXECUTE = "execute"
    MANAGE = "manage"  # 完全控制权限


class ResourceType(str, Enum):
    """资源类型"""
    USER = "user"
    ROLE = "role"
    PERMISSION = "permission"
    KNOWLEDGE_BASE = "knowledge_base"
    DOCUMENT = "document"
    APPLICATION = "application"
    WORKFLOW = "workflow"
    CHAT = "chat"
    SYSTEM = "system"


class RetrievalMethod(str, Enum):
    """检索方法 - 参考 Dify 的检索策略"""
    SEMANTIC_SEARCH = "semantic_search"  # 纯向量语义搜索
    FULL_TEXT_SEARCH = "full_text_search"  # 纯全文关键词搜索
    HYBRID_SEARCH = "hybrid_search"  # 混合搜索（向量+关键词）
    HYBRID_RERANK = "hybrid_rerank"  # 混合搜索 + Rerank 重排序


class NodeType(str, Enum):
    """工作流节点类型"""
    START = "start"           # 开始节点
    END = "end"               # 结束节点
    LLM = "llm"               # LLM 调用节点
    KNOWLEDGE = "knowledge"    # 知识库检索节点
    CODE = "code"             # 代码执行节点
    CONDITION = "condition"    # 条件判断节点
    TEMPLATE = "template"      # 模板转换节点
    HTTP = "http"             # HTTP 请求节点


# ==================== 任务相关模型 ====================

class AsyncTask(BaseModel):
    """异步任务模型"""
    id: str
    type: TaskType
    status: TaskStatus
    progress: float = 0.0  # 0-100
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: str
    updated_at: str
    metadata: Dict[str, Any] = {}


# ==================== 用户相关模型 ====================

class LoginRequest(BaseModel):
    """登录请求"""
    username: str
    password: str


class CreateUserRequest(BaseModel):
    """创建用户请求"""
    username: str
    password: str
    email: Optional[str] = None
    nickname: Optional[str] = None
    mobile: Optional[str] = None
    dept_id: Optional[str] = None
    post_ids: Optional[List[str]] = []
    role_ids: Optional[List[str]] = []
    avatar: Optional[str] = None
    status: Optional[int] = 0


class UpdateUserRequest(BaseModel):
    """更新用户请求"""
    email: Optional[str] = None
    nickname: Optional[str] = None
    mobile: Optional[str] = None
    dept_id: Optional[str] = None
    post_ids: Optional[List[str]] = []
    avatar: Optional[str] = None
    status: Optional[int] = None


class AssignRoleRequest(BaseModel):
    """分配角色请求"""
    role_ids: List[str]


# ==================== 角色相关模型 ====================

class CreateRoleRequest(BaseModel):
    """创建角色请求"""
    name: str
    code: str
    sort: Optional[int] = 0
    status: Optional[int] = 0
    data_scope: Optional[int] = 5  # 默认仅本人数据
    remark: Optional[str] = None
    menu_ids: Optional[List[str]] = []


class UpdateRoleRequest(BaseModel):
    """更新角色请求"""
    name: Optional[str] = None
    sort: Optional[int] = None
    status: Optional[int] = None
    data_scope: Optional[int] = None
    remark: Optional[str] = None
    menu_ids: Optional[List[str]] = None


# ==================== 部门相关模型 ====================

class CreateDepartmentRequest(BaseModel):
    """创建部门请求"""
    name: str
    parent_id: Optional[str] = None
    sort: Optional[int] = 0
    status: Optional[int] = 0
    remark: Optional[str] = None


class UpdateDepartmentRequest(BaseModel):
    """更新部门请求"""
    name: Optional[str] = None
    parent_id: Optional[str] = None
    sort: Optional[int] = None
    status: Optional[int] = None
    remark: Optional[str] = None


# ==================== 权限相关模型 ====================

class GrantResourcePermissionRequest(BaseModel):
    """授予资源权限请求"""
    resource_type: str  # 'knowledge_base', 'application', 'workflow'
    resource_id: str
    user_id: Optional[str] = None
    role_id: Optional[str] = None
    dept_id: Optional[str] = None
    permissions: List[str]  # ['read', 'write', 'delete', 'manage']
    expires_at: Optional[str] = None


# ==================== 知识库相关模型 ====================

class CreateKnowledgeBaseRequest(BaseModel):
    """创建知识库请求"""
    name: str
    description: Optional[str] = None
    embedding_model: Optional[str] = "nomic-embed-text"
    chunk_size: Optional[int] = 512
    chunk_overlap: Optional[int] = 50


class UpdateKnowledgeBaseRequest(BaseModel):
    """更新知识库请求"""
    name: Optional[str] = None
    description: Optional[str] = None
    embedding_model: Optional[str] = None
    chunk_size: Optional[int] = None
    chunk_overlap: Optional[int] = None


class SearchRequest(BaseModel):
    """搜索请求"""
    query: str
    top_k: Optional[int] = 5
    threshold: Optional[float] = 0.0


class HybridSearchRequest(BaseModel):
    """混合搜索请求 - 支持三段式搜索：向量 + 关键词 + Rerank"""
    query: str
    top_k: Optional[int] = 5
    alpha: Optional[float] = 0.7  # 向量/关键词混合权重，默认0.7表示70%向量+30%关键词
    enable_rerank: Optional[bool] = False  # 是否启用Rerank重排序
    rerank_alpha: Optional[float] = 0.3  # Rerank分数权重，默认0.3表示30%rerank+70%原分数
    rerank_model: Optional[str] = "bge-reranker-v2-m3"  # Rerank模型名称


# ==================== 应用相关模型 ====================

class CreateApplicationRequest(BaseModel):
    """创建应用请求"""
    name: str
    description: Optional[str] = None
    model: Optional[str] = "qwen2.5:7b"
    knowledge_base_ids: Optional[List[str]] = []
    is_public: Optional[bool] = False
    system_prompt: Optional[str] = None
    welcome_message: Optional[str] = None
    share_password: Optional[str] = None
    # LLM 参数
    temperature: Optional[float] = 0.1  # 温度，0-1，越低越严格
    max_tokens: Optional[int] = 2048  # 最大生成 token 数
    top_p: Optional[float] = 0.9  # nucleus sampling
    # RAG 检索参数
    top_k: Optional[int] = 5  # 检索返回的文档数量
    retrieval_method: Optional[str] = "semantic_search"  # 检索方法
    similarity_threshold: Optional[float] = 0.0  # 相似度阈值
    # Embedding 参数
    embedding_model: Optional[str] = "nomic-embed-text"  # embedding 模型
    embedding_dimension: Optional[int] = 768  # 向量维度，必须和模型一致
    # 图片提示词相关（已废弃，保留兼容性）
    image_prompt_template: Optional[str] = None
    auto_image_prompt: Optional[bool] = True


class UpdateApplicationRequest(BaseModel):
    """更新应用请求"""
    name: Optional[str] = None
    description: Optional[str] = None
    model: Optional[str] = None
    knowledge_base_ids: Optional[List[str]] = None
    is_public: Optional[bool] = None
    system_prompt: Optional[str] = None
    welcome_message: Optional[str] = None
    share_password: Optional[str] = None
    # LLM 参数
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    # RAG 检索参数
    top_k: Optional[int] = None
    retrieval_method: Optional[str] = None
    similarity_threshold: Optional[float] = None
    # Embedding 参数
    embedding_model: Optional[str] = None
    embedding_dimension: Optional[int] = None
    # 图片提示词相关（已废弃，保留兼容性）
    image_prompt_template: Optional[str] = None
    auto_image_prompt: Optional[bool] = None


# ==================== 聊天相关模型 ====================

class ChatRequest(BaseModel):
    """聊天请求"""
    message: str
    conversation_id: Optional[str] = None
    session_id: Optional[str] = None  # 新增：会话ID，用于多轮对话管理
    context: Optional[List[Dict[str, str]]] = None  # 对话上下文，支持永久记忆
    stream: Optional[bool] = True  # 是否使用流式输出，默认True
    retrieval_method: Optional[str] = "semantic_search"  # 检索方法：semantic_search, full_text_search, hybrid_search
    top_k: Optional[int] = 3  # 检索返回数量


class ChatSessionResponse(BaseModel):
    """聊天会话响应模型"""
    id: str
    application_id: str
    user_id: Optional[str]
    title: Optional[str]
    message_count: int
    created_at: str
    updated_at: str


class FeedbackRequest(BaseModel):
    """反馈请求"""
    message_id: str
    feedback_type: str  # 'like' or 'dislike'
    comment: Optional[str] = None


# ==================== 工作流相关模型 ====================

class WorkflowNode(BaseModel):
    """工作流节点"""
    id: str
    type: NodeType
    name: str
    config: Dict[str, Any] = {}
    position: Dict[str, float] = {"x": 0, "y": 0}
    inputs: List[str] = []
    outputs: List[str] = []


class WorkflowEdge(BaseModel):
    """工作流连线"""
    id: str
    source: str    # 源节点ID
    target: str    # 目标节点ID
    condition: Optional[str] = None  # 条件表达式


class WorkflowDefinition(BaseModel):
    """工作流定义"""
    nodes: List[WorkflowNode]
    edges: List[WorkflowEdge]
    variables: Dict[str, Any] = {}  # 工作流变量


class CreateWorkflowRequest(BaseModel):
    """创建工作流请求"""
    name: str
    description: Optional[str] = None
    definition: WorkflowDefinition


# ==================== 验证码相关模型 ====================

class CaptchaVerifyRequest(BaseModel):
    """验证码验证请求"""
    captcha_id: str
    x: int  # 用户拖动的x坐标
    y: int  # 用户拖动的y坐标（应该接近0）


# ==================== 系统配置相关模型 ====================

class SystemConfigRequest(BaseModel):
    """系统配置请求"""
    site_name: Optional[str] = None
    site_title: Optional[str] = None
    logo: Optional[str] = None  # base64 encoded image
    favicon: Optional[str] = None  # base64 encoded image
    primary_color: Optional[str] = None
    theme: Optional[str] = None  # 'light' or 'dark'


# ==================== SSO 相关模型 ====================

class SSOConfigRequest(BaseModel):
    """SSO 配置请求"""
    provider: str  # 'feishu', 'dingtalk', 'wechat', 'oidc', 或自定义名称
    enabled: bool = True
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    redirect_uri: Optional[str] = None
    app_id: Optional[str] = None  # For Feishu/DingTalk
    app_secret: Optional[str] = None  # For Feishu/DingTalk

    # 通用 OIDC 配置
    display_name: Optional[str] = None  # 显示名称，如"广东省政务云登录"
    auth_url: Optional[str] = None  # 授权地址，如 https://xxx.com/authorize
    token_url: Optional[str] = None  # Token 地址，如 https://xxx.com/token
    userinfo_url: Optional[str] = None  # 用户信息地址，如 https://xxx.com/userinfo
    scope: Optional[str] = 'openid email profile'  # 授权范围
    issuer: Optional[str] = None  # Issuer，用于验证
    jwks_url: Optional[str] = None  # JWKS 地址，用于验证 id_token（可选）

    # 响应类型和模式
    response_type: Optional[str] = 'code'
    response_mode: Optional[str] = None  # query, form_post, fragment
    grant_type: Optional[str] = 'authorization_code'

    # 额外的认证参数
    extra_auth_params: Optional[Dict[str, str]] = None  # 额外的授权参数
    extra_token_params: Optional[Dict[str, str]] = None  # 额外的 token 参数


class OAuthCallbackRequest(BaseModel):
    """OAuth 回调请求"""
    code: str
    state: Optional[str] = None
    provider: str
