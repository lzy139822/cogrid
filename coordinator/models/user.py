"""用户模型与角色定义。

多租户算力合作社的用户实体。每个用户可同时是贡献者（提供算力）
和消费者（使用算力），role 字段标识其主要身份，用于权限校验与统计。

参考：docs/specs/2026-08-15-cogrid-design.md §5 多租户
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class UserRole(str, Enum):
    """用户角色。

    - CONTRIBUTOR: 贡献者 — 注册节点、提供算力，按贡献分获得份额
    - CONSUMER: 消费者 — 提交任务、使用算力，按份额比例获得优先级
    - ADMIN: 管理员 — 系统管理，可查看全局状态、调整配置
    """

    CONTRIBUTOR = "contributor"
    CONSUMER = "consumer"
    ADMIN = "admin"


@dataclass
class User:
    """注册用户。

    密码以 SHA256 + salt 哈希存储，salt 使用 user_id（每用户唯一）。
    token 在注册/登录时生成，用于后续请求的 Bearer 认证。

    Attributes:
        user_id: 用户唯一标识（自动生成 user_ 前缀 + 随机串）
        username: 用户名（唯一，注册时指定）
        password_hash: 密码哈希值（SHA256(password + salt)）
        salt: 密码盐值（使用 user_id）
        token: 当前有效的认证令牌（secrets.token_hex(16)）
        role: 用户角色
        created_at: 注册时间戳
    """
    user_id: str = field(default_factory=lambda: f"user_{uuid.uuid4().hex[:8]}")
    username: str = ""
    password_hash: str = ""
    salt: str = ""
    token: str = ""
    role: UserRole = UserRole.CONTRIBUTOR
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        """转换为字典（用于持久化存储）。"""
        return {
            "user_id": self.user_id,
            "username": self.username,
            "password_hash": self.password_hash,
            "salt": self.salt,
            "token": self.token,
            "role": self.role.value,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "User":
        """从字典构造 User 实例（用于从存储恢复）。"""
        role_str = data.get("role", "contributor")
        try:
            role = UserRole(role_str)
        except ValueError:
            role = UserRole.CONTRIBUTOR
        return cls(
            user_id=data["user_id"],
            username=data["username"],
            password_hash=data.get("password_hash", ""),
            salt=data.get("salt", ""),
            token=data.get("token", ""),
            role=role,
            created_at=data.get("created_at", time.time()),
        )
