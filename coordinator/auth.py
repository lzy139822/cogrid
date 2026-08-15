"""用户认证与多租户管理模块。

负责：
- 用户注册（username + password -> user_id + token）
- 用户登录验证（password 用 SHA256 + salt 哈希，salt 使用 user_id）
- Token 验证（Bearer token -> user_id）
- 用户信息查询

持久化策略：
- 构造函数接收可选的 Storage 实例。传入后，注册/登录等写操作会保存到
  SQLite（users 表）；不传入时退化为纯内存模式。
- 运行时以内存缓存为查询源（快速），写操作同步落盘（保证持久化）。
- 启动时通过 load_from_storage() 从 SQLite 恢复全部用户到内存。

安全说明：
- 密码使用 hashlib.sha256(password + salt).hexdigest() 存储，salt 为 user_id。
- Token 使用 secrets.token_hex(16) 生成（32 字符十六进制）。
- 点火阶段使用简单哈希，生产环境应替换为 bcrypt/argon2。

参考：docs/specs/2026-08-15-cogrid-design.md §5 多租户
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from typing import TYPE_CHECKING, Optional

from coordinator.models.user import User, UserRole

if TYPE_CHECKING:
    from coordinator.storage import Storage

logger = logging.getLogger(__name__)


def hash_password(password: str, salt: str) -> str:
    """使用 SHA256 + salt 对密码进行哈希。

    Args:
        password: 明文密码
        salt: 盐值（使用 user_id）

    Returns:
        64 字符十六进制哈希字符串
    """
    return hashlib.sha256((password + salt).encode("utf-8")).hexdigest()


def generate_token() -> str:
    """生成随机认证令牌（32 字符十六进制）。"""
    return secrets.token_hex(16)


class UserManager:
    """用户认证管理器 — 多租户算力合作社的"门卫"。

    线程安全：在单进程 async 上下文中使用，内存操作无需加锁。
    对于多进程部署，应通过 Storage（SQLite）做唯一性校验。

    用法::

        # 内存模式（测试 / 轻量场景）
        mgr = UserManager()
        user_id, token = await mgr.register("alice", "pass123")

        # 持久化模式（生产）
        mgr = UserManager(storage)
        await mgr.load_from_storage()  # 启动时恢复
        user_id, token = await mgr.register("alice", "pass123")
    """

    def __init__(self, storage: "Optional[Storage]" = None) -> None:
        """初始化用户管理器。

        Args:
            storage: 可选的 Storage 实例。传入后用户数据持久化到 SQLite；
                     不传入时为纯内存模式。
        """
        self._storage = storage
        # 内存索引：快速查询
        self._users_by_id: dict[str, User] = {}
        self._users_by_username: dict[str, User] = {}
        self._tokens: dict[str, str] = {}  # token -> user_id

    async def load_from_storage(self) -> None:
        """从 SQLite 加载全部用户到内存缓存。

        应在应用启动时调用。未注入 Storage 时为空操作。
        """
        if self._storage is None:
            return
        try:
            users = await self._storage.load_all_users()
        except Exception:
            logger.exception("从存储加载用户失败，将以空状态启动")
            return
        for user_dict in users:
            user = User.from_dict(user_dict)
            self._index_user(user)
        logger.info(f"从存储恢复 {len(users)} 个用户")

    def _index_user(self, user: User) -> None:
        """将用户加入内存索引。"""
        self._users_by_id[user.user_id] = user
        self._users_by_username[user.username] = user
        if user.token:
            self._tokens[user.token] = user.user_id

    def _remove_user_tokens(self, user: User) -> None:
        """从 token 索引中移除用户的旧 token。"""
        if user.token and self._tokens.get(user.token) == user.user_id:
            del self._tokens[user.token]

    async def _persist(self, user: User) -> None:
        """将用户持久化到 SQLite。

        未注入 Storage 时直接返回。持久化失败仅记录日志，不阻断业务。
        """
        if self._storage is None:
            return
        try:
            await self._storage.save_user(user.to_dict())
        except Exception:
            logger.exception(f"持久化用户 {user.user_id} 失败")

    async def register(
        self,
        username: str,
        password: str,
        role: UserRole = UserRole.CONTRIBUTOR,
    ) -> tuple[str, str]:
        """注册新用户。

        Args:
            username: 用户名（唯一）
            password: 明文密码
            role: 用户角色，默认 CONTRIBUTOR

        Returns:
            (user_id, token) 元组

        Raises:
            ValueError: 用户名已存在
        """
        if not username or not password:
            raise ValueError("用户名和密码不能为空")

        # 检查用户名唯一性（内存索引）
        if username in self._users_by_username:
            raise ValueError(f"用户名 '{username}' 已存在")

        # 生成 user_id、salt、密码哈希、token
        user_id = f"user_{secrets.token_hex(8)}"
        salt = user_id  # salt 使用 user_id
        password_hash = hash_password(password, salt)
        token = generate_token()

        user = User(
            user_id=user_id,
            username=username,
            password_hash=password_hash,
            salt=salt,
            token=token,
            role=role,
        )

        # 写入内存索引
        self._index_user(user)

        # 持久化
        await self._persist(user)

        logger.info(f"用户注册成功: {username} -> {user_id}")
        return (user_id, token)

    async def login(self, username: str, password: str) -> Optional[tuple[str, str]]:
        """用户登录验证。

        Args:
            username: 用户名
            password: 明文密码

        Returns:
            (user_id, token) 元组，验证失败返回 None
        """
        user = self._users_by_username.get(username)
        if user is None:
            logger.warning(f"登录失败：用户名不存在 '{username}'")
            return None

        # 验证密码
        expected_hash = hash_password(password, user.salt)
        if expected_hash != user.password_hash:
            logger.warning(f"登录失败：密码错误 '{username}'")
            return None

        # 生成新 token（每次登录刷新 token）
        self._remove_user_tokens(user)
        new_token = generate_token()
        user.token = new_token
        self._tokens[new_token] = user.user_id

        # 持久化新 token
        await self._persist(user)

        logger.info(f"用户登录成功: {username} -> {user.user_id}")
        return (user.user_id, new_token)

    def verify_token(self, token: str) -> Optional[str]:
        """验证 token，返回对应的 user_id。

        Args:
            token: Bearer token 字符串

        Returns:
            user_id，无效 token 返回 None
        """
        if not token:
            return None
        return self._tokens.get(token)

    def get_user(self, user_id: str) -> Optional[User]:
        """按 user_id 获取用户。

        Args:
            user_id: 用户 ID

        Returns:
            User 实例，不存在返回 None
        """
        return self._users_by_id.get(user_id)

    def get_user_by_username(self, username: str) -> Optional[User]:
        """按 username 获取用户。

        Args:
            username: 用户名

        Returns:
            User 实例，不存在返回 None
        """
        return self._users_by_username.get(username)

    def get_all_users(self) -> list[User]:
        """获取所有用户列表。"""
        return list(self._users_by_id.values())

    @property
    def user_count(self) -> int:
        """当前注册用户总数。"""
        return len(self._users_by_id)
