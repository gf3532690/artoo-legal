"""启动引导（tenant-rbac-refactor）：全新初始化，幂等。不做历史数据迁移。

职责：
- TenantBootstrap：创建内置 External_User_Tenant（不预置默认管理员，也不预置公共库）。
  不再预置权限点字典与自定义角色（固定角色模型）。
- SuperAdminBootstrap：首次启动且无 Super_Admin 时按环境变量创建
  (``is_super_admin=True``/``role=None``/``must_change_password=True``)，强制改密；
  缺必需环境变量时 fail-fast（禁止默认口令兜底）。

所有创建均幂等（已存在则跳过、不重复创建、不删除已有记录）。API 进程与 Worker
进程都会经 init_db -> bootstrap，故须容忍并发首启。
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.constants import (
    DEFAULT_LEGAL_KB_NAME,
    DEFAULT_LEGAL_TENANT_ID,
    EXTERNAL_USER_TENANT_ID,
    EXTERNAL_USER_TENANT_NAME,
    TenantRoleEnum,
    TenantTypeEnum,
)
from app.auth.password import hash_password
from app.config import get_settings
from app.schema.db import (
    KnowledgeBase,
    Tenant,
    User,
)

logger = logging.getLogger(__name__)


async def _tenant_bootstrap(session: AsyncSession) -> None:
    """内置 External_User_Tenant（不预置默认管理员，也不预置公共库）。

    固定角色模型下不预置权限点 / 自定义角色 / 角色关联行。外部用户在认证时合成为
    member；该租户的治理由平台 Super_Admin 经管理端点完成（按需补充管理员），
    故引导阶段**不再创建默认管理员**。每个外部用户按 (代理Key, X-External-User-Id)
    懒创建独立身份，各自在自有私有库内读写；不再预置无主公共库。
    """
    # 内置 External_User_Tenant
    ext_tenant = await session.get(Tenant, EXTERNAL_USER_TENANT_ID)
    if ext_tenant is None:
        session.add(
            Tenant(
                id=EXTERNAL_USER_TENANT_ID,
                name=EXTERNAL_USER_TENANT_NAME,
                tenant_type=TenantTypeEnum.EXTERNAL.value,
                is_active=True,
            )
        )
        await session.flush()

    await session.commit()


async def _super_admin_bootstrap(session: AsyncSession) -> None:
    """首次启动且无 Super_Admin 时按环境变量创建（强制改密）。"""
    count = await session.scalar(
        select(func.count(User.id)).where(User.is_super_admin == True)  # noqa: E712
    )
    if count and count > 0:
        return  # 已存在，幂等跳过

    settings = get_settings()
    username = (settings.super_admin_username or "").strip()
    password = settings.super_admin_password or ""
    if not username or not password:
        raise RuntimeError(
            "首次启动需创建 Super_Admin：请配置 SUPER_ADMIN_USERNAME / SUPER_ADMIN_PASSWORD"
            "（禁止默认口令兜底）"
        )

    session.add(
        User(
            id=str(uuid.uuid4()),
            tenant_id=None,        # Super_Admin 不归属任何业务租户
            username=username,
            password_hash=await hash_password(password),
            role=None,             # Super_Admin 不参与租户固定角色
            is_active=True,
            is_super_admin=True,
            must_change_password=True,  # 强制首次登录改密
            token_version=0,
        )
    )
    await session.commit()
    logger.info("已创建初始 Super_Admin（强制首次改密）")


async def _default_legal_tenant_bootstrap(session: AsyncSession) -> None:
    """法条库部署（单租户）：幂等创建默认租户、租户管理员与全局法条库。

    本部署是单租户部署：全局法条库与所有个人库都落在同一个默认租户内，
    全局库由该租户的管理员维护（见 ``docs/legal-recall-implementation-plan.md`` D4）。

    三件事都幂等，且都容忍"由管理员手工创建"：

    1. **默认租户**：固定 id ``DEFAULT_LEGAL_TENANT_ID``，不存在才建。
    2. **租户管理员**：仅在配了 ``LEGAL_TENANT_ADMIN_USERNAME/PASSWORD`` 时创建；
       缺失只记 warning，不 fail-fast——部署方可能随后经管理端点手工建号。
    3. **全局法条库**：按 ``config.is_default_legal_kb`` 标记查找，不存在才建。
       owner 指向上面那个管理员；可见性 ``organization`` + ``read``，使同租户身份
       自然可读（不需要任何跨租户例外）。``chunker_type=laws`` 让入库走法条切分。

    注意：调用方的身份必须属于本租户，否则读不到全局库——这是单租户模型的前提。
    """
    settings = get_settings()

    # 1) 默认租户
    tenant = await session.get(Tenant, DEFAULT_LEGAL_TENANT_ID)
    if tenant is None:
        session.add(
            Tenant(
                id=DEFAULT_LEGAL_TENANT_ID,
                name=settings.legal_tenant_name or DEFAULT_LEGAL_TENANT_NAME,
                tenant_type=TenantTypeEnum.BUSINESS.value,
                is_active=True,
            )
        )
        await session.flush()

    # 2) 租户管理员
    admin: User | None = None
    username = (settings.legal_tenant_admin_username or "").strip()
    password = settings.legal_tenant_admin_password or ""
    if username and password:
        admin = await session.scalar(select(User).where(User.username == username))
        if admin is None:
            admin = User(
                id=str(uuid.uuid4()),
                tenant_id=DEFAULT_LEGAL_TENANT_ID,
                username=username,
                password_hash=await hash_password(password),
                role=TenantRoleEnum.ADMIN.value,
                is_active=True,
                is_super_admin=False,
                must_change_password=True,
                token_version=0,
            )
            session.add(admin)
            await session.flush()
    else:
        logger.warning(
            "未配置 LEGAL_TENANT_ADMIN_USERNAME/PASSWORD：默认租户已创建，"
            "但全局法条库暂无 owner —— 需由管理员手工建号后转让归属，否则只读不可维护"
        )

    # 3) 全局法条库（按 config 标记查找，不用名称——名称允许被改）
    from app.retrieval.legal_scope import DEFAULT_LEGAL_KB_FLAG, _is_default_legal_kb

    existing_rows = await session.execute(
        select(KnowledgeBase.id, KnowledgeBase.config)
    )
    global_kb_id = next(
        (kb_id for kb_id, config in existing_rows.all() if _is_default_legal_kb(config)),
        None,
    )
    if global_kb_id is None:
        session.add(
            KnowledgeBase(
                id=str(uuid.uuid4()),
                name=DEFAULT_LEGAL_KB_NAME,
                description="全租户唯一的法条库；由租户管理员维护，检索时默认并入。",
                config={
                    "chunker_type": "laws",
                    DEFAULT_LEGAL_KB_FLAG: True,
                },
                doc_count=0,
                tenant_id=DEFAULT_LEGAL_TENANT_ID,
                owner_user_id=admin.id if admin is not None else None,
                visibility="organization",
                org_permission="read",
            )
        )
        await session.flush()
        logger.info("已创建全局法条库（owner=%s）", admin.id if admin else "未指定")

    await session.commit()


async def run_bootstrap(session_factory) -> None:
    """统一引导入口：由 init_db 之后调用（API 与 Worker 共用）。

    清理 E 后鉴权始终强制：不再有 auth_enabled 分支。缺 Super_Admin 环境变量一律
    fail-fast（禁止默认口令兜底），由调用方在启动阶段失败退出。
    """
    async with session_factory() as session:
        await _tenant_bootstrap(session)

    async with session_factory() as session:
        await _super_admin_bootstrap(session)

    async with session_factory() as session:
        await _default_legal_tenant_bootstrap(session)
