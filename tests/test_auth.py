"""用户认证系统测试。"""

import pytest
import asyncio
from coordinator.auth import UserManager, hash_password, generate_token
from coordinator.models.user import User, UserRole


def test_hash_password():
    """测试密码哈希。"""
    salt = "test_salt"
    h1 = hash_password("password123", salt)
    h2 = hash_password("password123", salt)
    assert h1 == h2
    assert h1 != hash_password("password456", salt)
    assert h1 != hash_password("password123", "other_salt")


def test_generate_token():
    """测试 token 生成。"""
    t1 = generate_token()
    t2 = generate_token()
    assert len(t1) == 32
    assert t1 != t2


@pytest.mark.asyncio
async def test_user_manager_register():
    """测试用户注册。"""
    mgr = UserManager()
    user_id, token = await mgr.register("alice", "pass123")
    assert user_id is not None
    assert len(token) == 32
    assert mgr.get_user(user_id) is not None
    assert mgr.get_user(user_id).username == "alice"


@pytest.mark.asyncio
async def test_user_manager_register_duplicate():
    """测试重复用户名注册失败。"""
    mgr = UserManager()
    await mgr.register("alice", "pass123")
    with pytest.raises(ValueError, match="已存在"):
        await mgr.register("alice", "pass456")


@pytest.mark.asyncio
async def test_user_manager_login():
    """测试登录。"""
    mgr = UserManager()
    user_id, reg_token = await mgr.register("bob", "secret")

    # 正确密码
    result = await mgr.login("bob", "secret")
    assert result is not None
    login_user_id, login_token = result
    assert login_user_id == user_id

    # 错误密码
    assert await mgr.login("bob", "wrong") is None

    # 不存在的用户
    assert await mgr.login("nobody", "pass") is None


@pytest.mark.asyncio
async def test_verify_token():
    """测试 token 验证。"""
    mgr = UserManager()
    user_id, token = await mgr.register("carol", "pass")

    # 正确 token
    assert mgr.verify_token(token) == user_id

    # 错误 token
    assert mgr.verify_token("invalid_token") is None


@pytest.mark.asyncio
async def test_user_roles():
    """测试用户角色。"""
    mgr = UserManager()
    user_id, _ = await mgr.register("admin", "pass", role=UserRole.ADMIN)
    assert mgr.get_user(user_id).role == UserRole.ADMIN

    user_id2, _ = await mgr.register("contributor", "pass", role=UserRole.CONTRIBUTOR)
    assert mgr.get_user(user_id2).role == UserRole.CONTRIBUTOR


def test_user_to_dict():
    """测试用户序列化。"""
    user = User(
        user_id="u1", username="test", password_hash="hash",
        salt="salt", token="tok", role=UserRole.CONTRIBUTOR,
    )
    d = user.to_dict()
    assert d["user_id"] == "u1"
    assert d["username"] == "test"
    assert d["role"] == "contributor"
    user2 = User.from_dict(d)
    assert user2.user_id == "u1"
    assert user2.username == "test"


@pytest.mark.asyncio
async def test_user_manager_persistence():
    """测试用户持久化。"""
    import tempfile, os
    from coordinator.storage import Storage

    db_path = os.path.join(tempfile.mkdtemp(), "test_auth.db")
    storage = Storage(db_path)
    await storage.init()

    mgr1 = UserManager(storage)
    user_id, token = await mgr1.register("persist_user", "pass123")

    # 模拟重启
    mgr2 = UserManager(storage)
    await mgr2.load_from_storage()

    user = mgr2.get_user(user_id)
    assert user is not None
    assert user.username == "persist_user"
    assert mgr2.verify_token(token) == user_id

    await storage.close()
