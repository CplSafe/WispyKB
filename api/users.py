# 用户管理路由
# 从 main_pgvector.py 拆分的用户相关 API

import uuid
from core import config, audit_log, audit_log_with_changes

import logging
from typing import Optional, Dict, List
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from psycopg.rows import dict_row

from .models import CreateUserRequest, UpdateUserRequest, AssignRoleRequest
from .dependencies import hash_password, validate_pagination

# Import get_current_user from auth module
from .auth import get_current_user

logger = logging.getLogger(__name__)

# 创建路由
router = APIRouter(prefix="/api/v1/system/users", tags=["用户管理"])


async def has_permission(user_id: str, permission: str) -> bool:
    """检查用户权限的简单实现"""
    pool = config.pool
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # 首先检查 users 表中的 role 字段（兼容老系统）
            await cur.execute("""
                SELECT role
                FROM users
                WHERE id = %s AND deleted_at IS NULL
                LIMIT 1
            """, (user_id,))
            user = await cur.fetchone()
            if user and user.get('role') == 'super_admin':
                return True

            # 检查 system_role 表中的超级管理员角色
            await cur.execute("""
                SELECT r.code
                FROM system_role r
                JOIN system_user_role ur ON r.id = ur.role_id
                WHERE ur.user_id = %s AND r.deleted_at IS NULL AND r.code = 'super_admin'
            """, (user_id,))
            if await cur.fetchone():
                return True

    # 检查具体权限（简化版，超级管理员已返回 True）
    # 对于非超级管理员，这里简化处理，实际应该检查具体权限
    return False


# ==================== 用户个人相关 ====================

@router.post("/avatar")
async def upload_user_avatar(
    file: UploadFile = File(...),
    user: Dict = Depends(get_current_user)
):
    """上传用户头像"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    # 验证文件类型
    if not file.content_type or not file.content_type.startswith('image/'):
        raise HTTPException(status_code=400, detail="只支持图片文件")

    # 验证文件大小（最大 2MB）
    MAX_SIZE = 2 * 1024 * 1024
    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(status_code=400, detail="图片大小不能超过 2MB")

    from pathlib import Path
    pool = config.pool
    UPLOAD_DIR = Path(__file__).parent.parent / "static" / "files"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    # 生成文件名
    file_ext = file.filename.split('.')[-1] if '.' in file.filename else 'png'
    unique_filename = f"{uuid.uuid4()}.{file_ext}"
    file_path = UPLOAD_DIR / unique_filename

    # 保存文件
    with open(file_path, "wb") as f:
        f.write(content)

    # 返回 URL
    file_url = f"/static/files/{unique_filename}"

    # 更新用户头像
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET avatar = %s WHERE id = %s",
                (file_url, user['user_id'])
            )
            await conn.commit()

    return {
        "url": file_url,
        "filename": unique_filename
    }


# ==================== 用户管理 CRUD ====================

@router.get("")
async def list_users(
    page: int = 1,
    page_size: int = 20,
    username: Optional[str] = None,
    status: Optional[int] = None,
    dept_id: Optional[str] = None,
    user: Dict = Depends(get_current_user)
):
    """获取用户列表"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    # 验证分页参数
    page, page_size = validate_pagination(page, page_size)

    # 检查权限
    if not await has_permission(user['user_id'], 'system:user:manage'):
        raise HTTPException(status_code=403, detail="无权限访问")

    pool = config.pool
    async with pool.connection() as conn:
        # 构建查询条件
        conditions = ["u.deleted_at IS NULL"]
        params = []

        if username:
            conditions.append("u.username LIKE %s")
            params.append(f"%{username}%")
        if status is not None:
            conditions.append("u.status = %s")
            params.append(status)
        if dept_id:
            conditions.append("u.dept_id = %s")
            params.append(dept_id)

        where_clause = " AND ".join(conditions)

        # 获取总数
        async with conn.cursor(row_factory=dict_row) as cur_count:
            await cur_count.execute(f"""
                SELECT COUNT(*) as total
                FROM users u
                WHERE {where_clause}
            """, params)
            total_result = await cur_count.fetchone()
            total = total_result['total'] if total_result else 0

        # 获取分页数据
        offset = (page - 1) * page_size

        async with conn.cursor(row_factory=dict_row) as cur:
            main_query = f"""
                SELECT
                    u.id, u.username, u.email, u.nickname, u.mobile, u.avatar,
                    u.status, u.dept_id, u.created_at::text as created_at,
                    d.name as dept_name,
                    COALESCE(ARRAY_AGG(DISTINCT r.code) FILTER (WHERE r.code IS NOT NULL), ARRAY[]::TEXT[]) as roles,
                    COALESCE(ARRAY_AGG(DISTINCT r.name) FILTER (WHERE r.name IS NOT NULL), ARRAY[]::TEXT[]) as role_names
                FROM users u
                LEFT JOIN departments d ON u.dept_id = d.id
                LEFT JOIN system_user_role ur ON u.id = ur.user_id
                LEFT JOIN system_role r ON ur.role_id = r.id AND r.deleted_at IS NULL
                WHERE {where_clause}
                GROUP BY u.id, u.username, u.email, u.nickname, u.mobile, u.avatar, u.status, u.dept_id, u.created_at, d.name
                ORDER BY u.created_at DESC
                LIMIT {page_size} OFFSET {offset}
            """
            await cur.execute(main_query, params)
            users = await cur.fetchall()

    return {
        "users": users,
        "total": total,
        "page": page,
        "page_size": page_size
    }


@router.get("/{user_id}")
async def get_user(user_id: str, user: Dict = Depends(get_current_user)):
    """获取用户详情"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    # 检查权限（可以查看自己的信息）
    if user['user_id'] != user_id and not await has_permission(user['user_id'], 'system:user:manage'):
        raise HTTPException(status_code=403, detail="无权限访问")

    pool = config.pool
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("""
                SELECT u.*,
                       d.name as dept_name,
                       d.id as dept_id
                FROM users u
                LEFT JOIN departments d ON u.dept_id = d.id
                WHERE u.id = %s AND u.deleted_at IS NULL
            """, (user_id,))
            user_info = await cur.fetchone()

            if not user_info:
                raise HTTPException(status_code=404, detail="用户不存在")

            # 获取用户角色
            await cur.execute("""
                SELECT r.id, r.name, r.code
                FROM system_role r
                JOIN system_user_role ur ON r.id = ur.role_id
                WHERE ur.user_id = %s AND r.deleted_at IS NULL
            """, (user_id,))
            user_info['roles'] = await cur.fetchall()

            # 获取用户岗位
            await cur.execute("""
                SELECT p.id, p.code, p.name
                FROM system_post p
                JOIN system_user_post up ON p.id = up.post_id
                WHERE up.user_id = %s AND p.deleted_at IS NULL
            """, (user_id,))
            user_info['posts'] = await cur.fetchall()

    return user_info


@router.post("")
@audit_log()
async def create_user(request: CreateUserRequest, user: Dict = Depends(get_current_user)):
    """创建用户"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    if not await has_permission(user['user_id'], 'system:user:create'):
        raise HTTPException(status_code=403, detail="无权限创建用户")

    try:
        pool = config.pool
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                # 检查用户名是否已存在
                await cur.execute("SELECT id FROM users WHERE username = %s AND deleted_at IS NULL", (request.username,))
                if await cur.fetchone():
                    raise HTTPException(status_code=400, detail="用户名已存在")

                # 检查邮箱是否已存在
                if request.email:
                    await cur.execute("SELECT id FROM users WHERE email = %s AND deleted_at IS NULL", (request.email,))
                    if await cur.fetchone():
                        raise HTTPException(status_code=400, detail="邮箱已被使用")

                # 生成密码哈希
                password_hash = hash_password(request.password)

                # 创建用户
                user_id = str(uuid.uuid4())
                await cur.execute("""
                    INSERT INTO users (id, username, password_hash, email, nickname, mobile, avatar, dept_id, status, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """, (user_id, request.username, password_hash, request.email, request.nickname,
                      request.mobile, request.avatar, request.dept_id, request.status))

                # 分配角色
                if request.role_ids:
                    for role_id in request.role_ids:
                        await cur.execute("""
                            INSERT INTO system_user_role (user_id, role_id)
                            VALUES (%s, %s)
                            ON CONFLICT (user_id, role_id) DO NOTHING
                        """, (user_id, role_id))

                # 分配岗位
                if request.post_ids:
                    for post_id in request.post_ids:
                        await cur.execute("""
                            INSERT INTO system_user_post (user_id, post_id)
                            VALUES (%s, %s)
                            ON CONFLICT (user_id, post_id) DO NOTHING
                        """, (user_id, post_id))

                await conn.commit()

        return {"id": user_id, "message": "用户创建成功"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建用户失败: {str(e)}")
        raise HTTPException(status_code=500, detail="创建用户失败，请稍后重试")


@router.put("/{user_id}")
@audit_log_with_changes()
async def update_user(
    user_id: str,
    request: UpdateUserRequest,
    current_user: Dict = Depends(get_current_user)
):
    """更新用户"""
    if not current_user:
        raise HTTPException(status_code=401, detail="未登录")

    if not await has_permission(current_user['user_id'], 'system:user:update'):
        raise HTTPException(status_code=403, detail="无权限更新用户")

    # 用于跟踪变更
    changes = {}

    try:
        pool = config.pool
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("SELECT * FROM users WHERE id = %s AND deleted_at IS NULL", (user_id,))
                old_user = await cur.fetchone()
                if not old_user:
                    raise HTTPException(status_code=404, detail="用户不存在")

                updates = []
                values = []

                if request.email is not None:
                    changes['email'] = {'old': old_user['email'], 'new': request.email}
                    updates.append("email = %s")
                    values.append(request.email)
                if request.nickname is not None:
                    changes['nickname'] = {'old': old_user['nickname'], 'new': request.nickname}
                    updates.append("nickname = %s")
                    values.append(request.nickname)
                if request.mobile is not None:
                    changes['mobile'] = {'old': old_user['mobile'], 'new': request.mobile}
                    updates.append("mobile = %s")
                    values.append(request.mobile)
                if request.dept_id is not None:
                    changes['dept_id'] = {'old': old_user['dept_id'], 'new': request.dept_id}
                    updates.append("dept_id = %s")
                    values.append(request.dept_id)
                if request.avatar is not None:
                    changes['avatar'] = {'old': old_user['avatar'], 'new': request.avatar}
                    updates.append("avatar = %s")
                    values.append(request.avatar)
                if request.status is not None:
                    changes['status'] = {'old': old_user['status'], 'new': request.status}
                    updates.append("status = %s")
                    values.append(request.status)

                if updates:
                    values.append(user_id)
                    await cur.execute(f"""
                        UPDATE users SET {', '.join(updates)}
                        WHERE id = %s
                    """, values)

                # 更新岗位
                if request.post_ids is not None:
                    changes['post_ids'] = {'old': 'N/A', 'new': request.post_ids}
                    await cur.execute("DELETE FROM system_user_post WHERE user_id = %s", (user_id,))
                    for post_id in request.post_ids:
                        await cur.execute("""
                            INSERT INTO system_user_post (user_id, post_id)
                            VALUES (%s, %s)
                        """, (user_id, post_id))

                await conn.commit()

        return {"message": "用户更新成功", "id": user_id, "changes": changes}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新用户失败: {str(e)}")
        raise HTTPException(status_code=500, detail="更新用户失败，请稍后重试")


@router.delete("/{user_id}")
@audit_log()
async def delete_user(user_id: str, current_user: Dict = Depends(get_current_user)):
    """删除用户（软删除）"""
    if not current_user:
        raise HTTPException(status_code=401, detail="未登录")

    if not await has_permission(current_user['user_id'], 'system:user:delete'):
        raise HTTPException(status_code=403, detail="无权限删除用户")

    # 不能删除自己
    if user_id == current_user['user_id']:
        raise HTTPException(status_code=400, detail="不能删除自己")

    pool = config.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                UPDATE users SET deleted_at = NOW()
                WHERE id = %s
            """, (user_id,))
            await conn.commit()

    return {"message": "用户删除成功"}


@router.put("/{user_id}/roles")
@audit_log(entity_type="user_role", action="assign")
async def assign_user_roles(
    user_id: str,
    request: AssignRoleRequest,
    current_user: Dict = Depends(get_current_user)
):
    """为用户分配角色"""
    if not current_user:
        raise HTTPException(status_code=401, detail="未登录")

    if not await has_permission(current_user['user_id'], 'system:role:assign'):
        raise HTTPException(status_code=403, detail="无权限分配角色")

    pool = config.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # 删除现有角色
            await cur.execute("DELETE FROM system_user_role WHERE user_id = %s", (user_id,))

            # 分配新角色
            for role_id in request.role_ids:
                await cur.execute("""
                    INSERT INTO system_user_role (user_id, role_id)
                    VALUES (%s, %s)
                    ON CONFLICT (user_id, role_id) DO NOTHING
                """, (user_id, role_id))

            await conn.commit()

    return {"message": "角色分配成功"}


@router.put("/{user_id}/password")
@audit_log(entity_type="user", action="reset_password")
async def reset_user_password(
    user_id: str,
    new_password: str = Form(...),
    current_user: Dict = Depends(get_current_user)
):
    """重置用户密码"""
    if not current_user:
        raise HTTPException(status_code=401, detail="未登录")

    if not await has_permission(current_user['user_id'], 'system:user:update'):
        raise HTTPException(status_code=403, detail="无权限重置密码")

    password_hash = hash_password(new_password)

    pool = config.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                UPDATE users SET password_hash = %s
                WHERE id = %s
            """, (password_hash, user_id))
            await conn.commit()

    return {"message": "密码重置成功"}
