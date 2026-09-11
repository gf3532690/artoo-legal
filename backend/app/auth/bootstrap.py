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
    ApiKeyTypeEnum,
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
    ApiKey,
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

    法条库 fork：外部用户默认落在**默认租户**（``settings.external_user_tenant_id``），
    此时不再需要这个内置租户，跳过创建（否则租户管理列表里会多一个空租户）。
    要退回上游形态，把 ``EXTERNAL_USER_TENANT_ID`` 配回 ``tenant-external-builtin``。
    """
    if get_settings().external_user_tenant_id != EXTERNAL_USER_TENANT_ID:
        return

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


def _seeded_key_id(kind: str, raw_key: str) -> str:
    """由 (用途, 明文) 推导出稳定的 API Key id（= 签名通道的 AK）。

    必须**稳定**，不能每次启动随机：代理 Key 的外部身份命名空间是
    ``(api_key.id, X-External-User-Id)``（见 ``auth/apikey_auth.py`` 的 ``key_source``）。
    若 id 每次重建都变，同一个下游用户在同一把 Key 下会解析成**新身份**，旧个人库立刻
    404（数据还在，只是归到了旧 id 名下）。

    uuid5 不可逆，因此 id 可公开（它本来就是 AK），明文不会从 id 泄漏。
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"artoo-legal/{kind}/{raw_key}"))


def _seed_key_or_none(env_name: str, raw: str | None) -> str | None:
    """读取一条预置 Key 的配置值并做形状校验（空 = 该条不预置）。"""
    value = (raw or "").strip()
    if not value:
        return None
    if len(value) < 16:
        raise RuntimeError(
            f"{env_name} 过短（{len(value)} 字符）：预置的 Key 必须与下游配置里那把是按位相同的"
            f"最长随机串，太短几乎一定是配错了。留空则不预置。"
        )
    if not value.startswith("sk-"):
        # 不致命（校验只比对 SHA256，前缀不影响可用性），但前缀缺失会让人以为是别的凭据。
        logger.warning("%s 不以 sk- 开头（当前前缀 %r），确认与下游配置一致", env_name, value[:3])
    return value


async def _insert_seeded_key(
    session: AsyncSession,
    *,
    raw_key: str,
    key_id: str,
    name: str,
    key_type: str,
    tenant_id: str,
    bound_user_id: str | None = None,
    key_source: str | None = None,
) -> None:
    """按明文预置一把 Key（幂等：按 key_hash 查重，已存在即跳过）。

    **已撤销的 Key 不会被重新激活**：运维显式撤销过就保持撤销（重启复活比 401 更难排查），
    只记 warning 提示"下游会 401"。
    """
    from app.api.auth import get_key_prefix, hash_key

    key_hash = hash_key(raw_key)
    existing = await session.scalar(select(ApiKey).where(ApiKey.key_hash == key_hash))
    if existing is not None:
        if existing.is_active:
            logger.info("预置 API Key 已存在，跳过：%s（prefix=%s）", name, existing.prefix)
        else:
            logger.warning(
                "预置 API Key 已存在但**已被撤销**，保持撤销状态：%s（prefix=%s）——"
                "下游用这把 Key 会收到 401；要恢复请清理该行，或换一个 env 值。",
                name, existing.prefix,
            )
        return

    session.add(
        ApiKey(
            id=key_id,
            key_hash=key_hash,
            prefix=get_key_prefix(raw_key),
            name=name,
            is_active=True,
            call_count=0,
            tenant_id=tenant_id,
            key_type=key_type,
            bound_user_id=bound_user_id,
            key_source=key_source,
        )
    )
    try:
        await session.commit()
    except Exception:
        # API 与 Worker 首启并发引导时可能撞 key_hash 唯一约束：另一侧已建好，幂等收尾。
        await session.rollback()
        if await session.scalar(select(ApiKey).where(ApiKey.key_hash == key_hash)) is None:
            raise
        return
    logger.info("已预置 API Key：%s（prefix=%s, type=%s）", name, get_key_prefix(raw_key), key_type)


async def _seed_api_keys(session: AsyncSession) -> None:
    """按 env 预置两把 API Key，使下游（lite）不必先登录后台手工领取。

    见 ``config.py`` 同名注释：代理 Key 给下游业务链路（Bearer + X-External-User-Id），
    owner 的用户级 Key 给下游 admin 端维护全局法条库。两项都空则整段跳过（上游形态）。

    与 ``_default_legal_tenant_bootstrap`` 的配合：owner 的 Key 必须绑定默认租户管理员，
    因此要在那一步之后调用；管理员缺失时只记 warning 并跳过这一把（代理 Key 不受影响）。
    """
    settings = get_settings()
    proxy_raw = _seed_key_or_none(
        "LEGAL_BOOTSTRAP_PROXY_API_KEY", settings.legal_bootstrap_proxy_api_key
    )
    admin_raw = _seed_key_or_none(
        "LEGAL_BOOTSTRAP_ADMIN_API_KEY", settings.legal_bootstrap_admin_api_key
    )
    if not proxy_raw and not admin_raw:
        return

    if proxy_raw:
        key_id = _seeded_key_id("proxy", proxy_raw)
        await _insert_seeded_key(
            session,
            raw_key=proxy_raw,
            key_id=key_id,
            name="法条库-业务代理Key（预置）",
            key_type=ApiKeyTypeEnum.EXTERNAL_AGENT.value,
            tenant_id=settings.external_user_tenant_id,
            key_source=key_id,  # 命名空间前缀 = 自身 id（与 POST /api/api-keys/external-agent 一致）
        )

    if admin_raw:
        username = (settings.legal_tenant_admin_username or "").strip()
        admin = (
            await session.scalar(select(User).where(User.username == username))
            if username
            else None
        )
        if admin is None or admin.tenant_id is None:
            logger.warning(
                "配置了 LEGAL_BOOTSTRAP_ADMIN_API_KEY，但%s"
                "（LEGAL_TENANT_ADMIN_USERNAME=%r）：这一把未预置，下游维护全局法条库会 401",
                "找不到默认租户管理员" if admin is None else "该管理员不归属任何租户",
                username,
            )
        else:
            await _insert_seeded_key(
                session,
                raw_key=admin_raw,
                key_id=_seeded_key_id("admin", admin_raw),
                name="法条库-全局库维护Key（预置）",
                key_type=ApiKeyTypeEnum.USER_LEVEL.value,
                tenant_id=admin.tenant_id,
                bound_user_id=admin.id,
            )


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

    async with session_factory() as session:
        await _seed_api_keys(session)
