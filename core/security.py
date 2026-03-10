# 安全配置和工具函数
# 政府服务器部署前安全检查清单

import os
import secrets
import logging
from typing import List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


# ==================== 密钥生成工具 ====================

def generate_jwt_secret() -> str:
    """生成强随机 JWT 密钥（至少 64 字符）"""
    return secrets.token_urlsafe(48)  # 生成 64 字符的 URL 安全密钥


def generate_api_key() -> str:
    """生成 API 密钥"""
    return f"ak_{secrets.token_urlsafe(32)}"


def generate_db_password(length: int = 24) -> str:
    """生成强数据库密码"""
    import string
    import random

    # 包含大小写字母、数字和特殊字符
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(random.choice(chars) for _ in range(length))


# ==================== 安全配置验证 ====================

class SecurityConfig:
    """安全配置验证"""

    # 密钥最小长度
    JWT_SECRET_MIN_LENGTH = 32
    DB_PASSWORD_MIN_LENGTH = 16

    # 生产环境必需的环境变量
    REQUIRED_ENV_VARS = [
        "JWT_SECRET",
        "POSTGRES_PASSWORD",
        "REDIS_PASSWORD",  # Redis 也应该设置密码
    ]

    # 推荐的安全配置
    RECOMMENDED_ENV_VARS = [
        "LLM_ENGINE",
        "VLLM_ENABLED",
        "CORS_ORIGINS",
    ]

    @classmethod
    def validate_jwt_secret(cls, secret: str) -> tuple[bool, str]:
        """验证 JWT 密钥强度"""
        if not secret:
            return False, "JWT_SECRET 不能为空"

        if len(secret) < cls.JWT_SECRET_MIN_LENGTH:
            return False, f"JWT_SECRET 长度必须至少 {cls.JWT_SECRET_MIN_LENGTH} 字符（当前: {len(secret)}）"

        # 检查是否为默认值（明显不安全的密钥）
        insecure_defaults = [
            "dev-secret-key-do-not-use-in-production-2024",
            "secret",
            "changeme",
            "password",
            "123456",
        ]

        if secret.lower() in insecure_defaults:
            return False, f"JWT_SECRET 使用了不安全的默认值: {secret}"

        return True, "✅ JWT_SECRET 强度符合要求"

    @classmethod
    def validate_db_password(cls, password: str) -> tuple[bool, str]:
        """验证数据库密码强度"""
        if not password:
            return False, "数据库密码不能为空"

        if len(password) < cls.DB_PASSWORD_MIN_LENGTH:
            return False, f"数据库密码长度建议至少 {cls.DB_PASSWORD_MIN_LENGTH} 字符（当前: {len(password)}）"

        # 检查密码复杂度
        has_upper = any(c.isupper() for c in password)
        has_lower = any(c.islower() for c in password)
        has_digit = any(c.isdigit() for c in password)
        has_special = any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in password)

        complexity_score = sum([has_upper, has_lower, has_digit, has_special])

        if complexity_score < 3:
            return False, (
                f"数据库密码复杂度不足。建议包含: "
                f"大写字母、小写字母、数字、特殊字符中的至少 3 种"
            )

        return True, "✅ 数据库密码强度符合要求"

    @classmethod
    def validate_cors_origins(cls, origins_str: str) -> tuple[bool, str, List[str]]:
        """验证 CORS 配置"""
        if not origins_str:
            return False, "CORS_ORIGINS 未配置", []

        # 尝试解析 CORS_ORIGINS
        try:
            import json
            origins = json.loads(origins_str)

            if not isinstance(origins, list):
                return False, "CORS_ORIGINS 必须是数组格式", []

            if "*" in origins:
                return False, "⚠️  危险: CORS_ORIGINS 包含通配符 '*'，生产环境不允许！", []

            if len(origins) == 0:
                return False, "CORS_ORIGINS 不能为空数组", []

            # 验证每个 origin 格式
            for origin in origins:
                if not origin.startswith(("http://", "https://")):
                    return False, f"无效的 origin: {origin}（必须以 http:// 或 https:// 开头）", []

            return True, f"✅ CORS_ORIGINS 配置正确（{len(origins)} 个域名）", origins

        except json.JSONDecodeError:
            return False, "CORS_ORIGINS 格式错误，必须是 JSON 数组", []

    @classmethod
    def check_environment_security(cls) -> dict:
        """全面的环境安全检查"""
        results = {
            "critical": [],  # 严重问题（必须修复）
            "warning": [],   # 警告（建议修复）
            "info": [],      # 信息提示
            "passed": [],    # 已通过
        }

        # 1. 检查 JWT_SECRET
        jwt_secret = os.getenv("JWT_SECRET", "")
        is_valid, message = cls.validate_jwt_secret(jwt_secret)
        if is_valid:
            results["passed"].append(("JWT_SECRET", message))
        else:
            results["critical"].append(("JWT_SECRET", message))

        # 2. 检查数据库密码
        db_password = os.getenv("POSTGRES_PASSWORD", "")
        is_valid, message = cls.validate_db_password(db_password)
        if is_valid:
            results["passed"].append(("POSTGRES_PASSWORD", message))
        else:
            results["warning"].append(("POSTGRES_PASSWORD", message))

        # 3. 检查 Redis 密码
        redis_password = os.getenv("REDIS_PASSWORD", "")
        if not redis_password:
            results["warning"].append(("REDIS_PASSWORD", "⚠️  Redis 未设置密码"))
        else:
            results["passed"].append(("REDIS_PASSWORD", "✅ Redis 已设置密码"))

        # 4. 检查 CORS 配置
        cors_origins = os.getenv("CORS_ORIGINS", '["*"]')
        is_valid, message, origins = cls.validate_cors_origins(cors_origins)
        if is_valid:
            results["passed"].append(("CORS_ORIGINS", message))
        else:
            if "危险" in message:
                results["critical"].append(("CORS_ORIGINS", message))
            else:
                results["warning"].append(("CORS_ORIGINS", message))

        # 5. 检查环境类型
        environment = os.getenv("ENVIRONMENT", "development")
        if environment == "production":
            results["info"].append(("ENVIRONMENT", "当前为生产环境模式"))
        else:
            results["warning"].append(("ENVIRONMENT", f"当前为 {environment} 模式，部署前请设置 ENVIRONMENT=production"))

        # 6. 检查 DEBUG 模式
        debug_mode = os.getenv("DEBUG", "true").lower() == "true"
        if debug_mode:
            results["critical"].append(("DEBUG", "⚠️  危险: DEBUG=true 在生产环境会泄露敏感信息！"))
        else:
            results["passed"].append(("DEBUG", "✅ DEBUG 模式已关闭"))

        # 7. 检查日志级别
        log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        if log_level == "DEBUG":
            results["warning"].append(("LOG_LEVEL", "⚠️  生产环境不建议使用 DEBUG 日志级别"))
        else:
            results["passed"].append(("LOG_LEVEL", f"✅ 日志级别: {log_level}"))

        # 8. 检查 API 密钥
        openai_key = os.getenv("OPENAI_API_KEY", "")
        anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")

        if openai_key and openai_key.startswith("sk-"):
            results["info"].append(("OPENAI_API_KEY", "✅ OpenAI 密钥已配置"))

        if anthropic_key and anthropic_key.startswith("sk-ant-"):
            results["info"].append(("ANTHROPIC_API_KEY", "✅ Anthropic 密钥已配置"))

        return results

    @classmethod
    def print_security_report(cls):
        """打印安全检查报告"""
        results = cls.check_environment_security()

        print("\n" + "=" * 70)
        print("🔒 安全配置检查报告")
        print("=" * 70)

        # 严重问题
        if results["critical"]:
            print("\n🚨 严重问题（必须修复）:")
            for item, message in results["critical"]:
                print(f"  ❌ {item}: {message}")

        # 警告
        if results["warning"]:
            print("\n⚠️  警告（建议修复）:")
            for item, message in results["warning"]:
                print(f"  ⚠️  {item}: {message}")

        # 通过的检查
        if results["passed"]:
            print("\n✅ 已通过:")
            for item, message in results["passed"]:
                print(f"  {item}: {message}")

        # 信息提示
        if results["info"]:
            print("\nℹ️  信息:")
            for item, message in results["info"]:
                print(f"  {item}: {message}")

        # 总结
        print("\n" + "=" * 70)
        critical_count = len(results["critical"])
        warning_count = len(results["warning"])
        passed_count = len(results["passed"])

        if critical_count == 0 and warning_count == 0:
            print("✅ 所有安全检查通过！系统可以安全部署。")
        elif critical_count == 0:
            print(f"⚠️  有 {warning_count} 个警告需要注意，但可以部署。")
        else:
            print(f"🚨 有 {critical_count} 个严重问题必须修复后才能部署！")

        print("=" * 70 + "\n")

        return results


# ==================== 敏感数据脱敏 ====================

def mask_sensitive_data(data: str, mask_char: str = "*", visible_start: int = 4, visible_end: int = 4) -> str:
    """
    脱敏敏感数据（如 API 密钥、密码等）

    Args:
        data: 原始数据
        mask_char: 掩码字符（默认 *）
        visible_start: 开头可见字符数
        visible_end: 结尾可见字符数

    Returns:
        脱敏后的数据

    Examples:
        >>> mask_sensitive_data("sk-1234567890abcdef")
        "sk-1234****cdef"
        >>> mask_sensitive_data("password123", visible_end=0)
        "pass********"
    """
    if not data or len(data) <= visible_start + visible_end:
        return mask_char * max(len(data), 8)

    return data[:visible_start] + mask_char * (len(data) - visible_start - visible_end) + data[-visible_end:]


# ==================== 安全加固建议 ====================

SECURITY_RECOMMENDATIONS = """
🔐 政府服务器部署安全建议

1. 密钥管理
   - 使用强随机密钥（至少 32 字符）
   - 定期轮换密钥（建议每 90 天）
   - 不要在代码中硬编码密钥
   - 使用环境变量或密钥管理服务

2. 数据库安全
   - 设置强密码（大小写+数字+特殊字符）
   - 限制数据库访问 IP
   - 启用 SSL/TLS 连接
   - 定期备份

3. 网络安全
   - 配置防火墙规则
   - 使用 HTTPS（TLS 1.2+）
   - 限制管理端口访问
   - 启用 DDoS 防护

4. 应用安全
   - 关闭 DEBUG 模式
   - 配置 CORS 白名单
   - 启用速率限制
   - 定期更新依赖

5. 审计和监控
   - 启用操作日志
   - 监控异常行为
   - 定期安全审计
   - 日志脱敏处理

6. 政府服务器特殊要求
   - 数据不外泄
   - 符合等保要求
   - 定期安全评估
   - 应急响应预案
"""


def print_security_recommendations():
    """打印安全建议"""
    print(SECURITY_RECOMMENDATIONS)


if __name__ == "__main__":
    # 运行安全检查
    SecurityConfig.print_security_report()
    print_security_recommendations()
