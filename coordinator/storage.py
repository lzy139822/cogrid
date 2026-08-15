"""SQLite 持久化存储层。

使用 aiosqlite 提供异步数据库访问，避免阻塞事件循环。
负责管理节点、贡献记录、任务、算力固存产物四类数据的持久化。

设计要点：
- 所有写操作均为异步（aiosqlite），不阻塞事件循环。
- 表结构使用 IF NOT EXISTS 创建，支持重复初始化。
- 写操作采用 INSERT ... ON CONFLICT DO UPDATE（upsert），可重复调用。
- 数据库路径自动创建父目录。

参考：docs/specs/2026-08-15-cogrid-design.md §4 持久化
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import TYPE_CHECKING, Any, Optional

import aiosqlite

if TYPE_CHECKING:
    # 仅用于类型标注，避免运行时循环导入。
    from coordinator.models.contribution import ContributionRecord
    from coordinator.models.node import Node
    from coordinator.models.task import Task

logger = logging.getLogger(__name__)


class Storage:
    """SQLite 持久化存储。

    用法::

        storage = Storage("data/cogrid.db")
        await storage.init()          # 建立连接、建表
        await storage.save_node(node) # 写入
        ...
        await storage.close()         # 关闭连接

    若不传入 Storage 实例，Ledger / TaskQueue 会自动退化为纯内存模式。
    """

    def __init__(self, db_path: str = "data/cogrid.db") -> None:
        """初始化存储配置。

        Args:
            db_path: SQLite 数据库文件路径，父目录会在 init() 时自动创建。
        """
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        """建立数据库连接并创建所有表（IF NOT EXISTS）。"""
        # 自动创建父目录
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        self._db = await aiosqlite.connect(self.db_path)
        # 开启 WAL 模式，提升并发读写性能
        await self._db.execute("PRAGMA journal_mode=WAL")
        # 外键约束
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._create_tables()
        await self._db.commit()
        logger.info(f"SQLite 存储已初始化: {self.db_path}")

    async def _create_tables(self) -> None:
        """创建全部数据表（已存在则跳过）。"""
        await self._db.executescript(
            """
            -- 节点表：记录注册过的贡献者节点元信息
            CREATE TABLE IF NOT EXISTS nodes (
                node_id        TEXT PRIMARY KEY,
                name           TEXT NOT NULL,
                status         TEXT NOT NULL,
                intensity      TEXT NOT NULL,
                cpu_cores      INTEGER NOT NULL DEFAULT 0,
                gpu_count      INTEGER NOT NULL DEFAULT 0,
                memory_mb       INTEGER NOT NULL DEFAULT 0,
                registered_at  REAL NOT NULL,
                last_heartbeat REAL NOT NULL
            );

            -- 贡献记录表：每个节点的累积贡献分与 PoA 探针统计
            CREATE TABLE IF NOT EXISTS contributions (
                node_id              TEXT PRIMARY KEY,
                total_credits        REAL NOT NULL DEFAULT 0,
                probe_success_count  INTEGER NOT NULL DEFAULT 0,
                probe_total_count    INTEGER NOT NULL DEFAULT 0,
                quality_factor       REAL NOT NULL DEFAULT 1.0,
                online_seconds       REAL NOT NULL DEFAULT 0,
                last_credit_time     REAL NOT NULL
            );

            -- 任务表：所有提交过的任务及其状态流转
            CREATE TABLE IF NOT EXISTS tasks (
                task_id          TEXT PRIMARY KEY,
                user_id          TEXT NOT NULL DEFAULT '',
                image            TEXT NOT NULL DEFAULT '',
                command          TEXT NOT NULL DEFAULT '[]',   -- JSON 数组
                cpu_cores        INTEGER NOT NULL DEFAULT 0,
                gpu_count        INTEGER NOT NULL DEFAULT 0,
                memory_mb         INTEGER NOT NULL DEFAULT 0,
                timeout_seconds  INTEGER NOT NULL DEFAULT 3600,
                task_type        TEXT NOT NULL,
                preemptible      INTEGER NOT NULL DEFAULT 1,   -- 0/1
                priority         INTEGER NOT NULL DEFAULT 0,
                status           TEXT NOT NULL,
                assigned_node    TEXT NOT NULL DEFAULT '',
                created_at       REAL NOT NULL,
                started_at       REAL NOT NULL DEFAULT 0,
                completed_at     REAL NOT NULL DEFAULT 0,
                artifact_desc    TEXT NOT NULL DEFAULT ''
            );

            -- 算力固存产物表：填充任务产出的可复用结果
            CREATE TABLE IF NOT EXISTS artifacts (
                task_id        TEXT PRIMARY KEY,
                artifact_path  TEXT NOT NULL DEFAULT '',
                description    TEXT NOT NULL DEFAULT '',
                created_at     REAL NOT NULL
            );
            """
        )

    # ------------------------------------------------------------------
    # 节点
    # ------------------------------------------------------------------

    async def save_node(self, node: "Node") -> None:
        """保存或更新节点信息（upsert）。"""
        await self._db.execute(
            """
            INSERT INTO nodes
                (node_id, name, status, intensity, cpu_cores, gpu_count,
                 memory_mb, registered_at, last_heartbeat)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                name=excluded.name,
                status=excluded.status,
                intensity=excluded.intensity,
                cpu_cores=excluded.cpu_cores,
                gpu_count=excluded.gpu_count,
                memory_mb=excluded.memory_mb,
                last_heartbeat=excluded.last_heartbeat
            """,
            (
                node.node_id,
                node.name,
                node.status.value,
                node.intensity.value,
                node.resources.cpu_cores_total,
                node.resources.gpu_count_total,
                node.resources.memory_mb_total,
                node.registered_at,
                node.last_heartbeat,
            ),
        )
        await self._db.commit()

    async def load_nodes(self) -> list[dict]:
        """加载所有节点，返回字典列表。"""
        async with self._db.execute(
            """
            SELECT node_id, name, status, intensity, cpu_cores, gpu_count,
                   memory_mb, registered_at, last_heartbeat
            FROM nodes
            """
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            {
                "node_id": r[0],
                "name": r[1],
                "status": r[2],
                "intensity": r[3],
                "cpu_cores": r[4],
                "gpu_count": r[5],
                "memory_mb": r[6],
                "registered_at": r[7],
                "last_heartbeat": r[8],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # 贡献记录
    # ------------------------------------------------------------------

    async def save_contribution(self, record: "ContributionRecord") -> None:
        """保存或更新节点贡献记录（upsert）。"""
        await self._db.execute(
            """
            INSERT INTO contributions
                (node_id, total_credits, probe_success_count, probe_total_count,
                 quality_factor, online_seconds, last_credit_time)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                total_credits=excluded.total_credits,
                probe_success_count=excluded.probe_success_count,
                probe_total_count=excluded.probe_total_count,
                quality_factor=excluded.quality_factor,
                online_seconds=excluded.online_seconds,
                last_credit_time=excluded.last_credit_time
            """,
            (
                record.node_id,
                record.total_credits,
                record.probe_success_count,
                record.probe_total_count,
                record.quality_factor,
                record.online_seconds,
                record.last_credit_time,
            ),
        )
        await self._db.commit()

    async def load_contributions(self) -> list[dict]:
        """加载所有贡献记录，返回字典列表。"""
        async with self._db.execute(
            """
            SELECT node_id, total_credits, probe_success_count, probe_total_count,
                   quality_factor, online_seconds, last_credit_time
            FROM contributions
            """
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            {
                "node_id": r[0],
                "total_credits": r[1],
                "probe_success_count": r[2],
                "probe_total_count": r[3],
                "quality_factor": r[4],
                "online_seconds": r[5],
                "last_credit_time": r[6],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # 任务
    # ------------------------------------------------------------------

    async def save_task(self, task: "Task") -> None:
        """保存或更新任务（upsert），command 以 JSON 字符串存储。"""
        await self._db.execute(
            """
            INSERT INTO tasks
                (task_id, user_id, image, command, cpu_cores, gpu_count, memory_mb,
                 timeout_seconds, task_type, preemptible, priority, status,
                 assigned_node, created_at, started_at, completed_at, artifact_desc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                user_id=excluded.user_id,
                image=excluded.image,
                command=excluded.command,
                cpu_cores=excluded.cpu_cores,
                gpu_count=excluded.gpu_count,
                memory_mb=excluded.memory_mb,
                timeout_seconds=excluded.timeout_seconds,
                task_type=excluded.task_type,
                preemptible=excluded.preemptible,
                priority=excluded.priority,
                status=excluded.status,
                assigned_node=excluded.assigned_node,
                started_at=excluded.started_at,
                completed_at=excluded.completed_at,
                artifact_desc=excluded.artifact_desc
            """,
            (
                task.task_id,
                task.user_id,
                task.image,
                json.dumps(task.command),
                task.requirement.cpu_cores_total,
                task.requirement.gpu_count_total,
                task.requirement.memory_mb_total,
                task.timeout_seconds,
                task.task_type.value,
                1 if task.preemptible else 0,
                task.priority,
                task.status.value,
                task.assigned_node,
                task.created_at,
                task.started_at,
                task.completed_at,
                task.artifact_desc,
            ),
        )
        await self._db.commit()

    async def load_tasks(self) -> list[dict]:
        """加载所有任务，返回字典列表（command 反序列化为 list）。"""
        async with self._db.execute(
            """
            SELECT task_id, user_id, image, command, cpu_cores, gpu_count, memory_mb,
                   timeout_seconds, task_type, preemptible, priority, status,
                   assigned_node, created_at, started_at, completed_at, artifact_desc
            FROM tasks
            """
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            {
                "task_id": r[0],
                "user_id": r[1],
                "image": r[2],
                "command": json.loads(r[3]) if r[3] else [],
                "cpu_cores": r[4],
                "gpu_count": r[5],
                "memory_mb": r[6],
                "timeout_seconds": r[7],
                "task_type": r[8],
                "preemptible": bool(r[9]),
                "priority": r[10],
                "status": r[11],
                "assigned_node": r[12],
                "created_at": r[13],
                "started_at": r[14],
                "completed_at": r[15],
                "artifact_desc": r[16],
            }
            for r in rows
        ]

    async def update_task_status(
        self,
        task_id: str,
        status: str,
        node_id: Optional[str] = None,
        started_at: Optional[float] = None,
        completed_at: Optional[float] = None,
    ) -> None:
        """局部更新任务状态。

        仅更新传入的字段，未传入的字段保持不变。
        适用于只关心状态流转的场景，避免整行覆写。
        """
        sets: list[str] = ["status = ?"]
        params: list[Any] = [status]
        if node_id is not None:
            sets.append("assigned_node = ?")
            params.append(node_id)
        if started_at is not None:
            sets.append("started_at = ?")
            params.append(started_at)
        if completed_at is not None:
            sets.append("completed_at = ?")
            params.append(completed_at)
        params.append(task_id)
        await self._db.execute(
            f"UPDATE tasks SET {', '.join(sets)} WHERE task_id = ?",
            params,
        )
        await self._db.commit()

    # ------------------------------------------------------------------
    # 算力固存产物
    # ------------------------------------------------------------------

    async def save_artifact(self, task_id: str, path: str, desc: str) -> None:
        """保存或更新固存产物记录（upsert）。"""
        await self._db.execute(
            """
            INSERT INTO artifacts (task_id, artifact_path, description, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                artifact_path=excluded.artifact_path,
                description=excluded.description,
                created_at=excluded.created_at
            """,
            (task_id, path, desc, time.time()),
        )
        await self._db.commit()

    async def load_artifacts(self) -> list[dict]:
        """加载所有固存产物，返回字典列表。"""
        async with self._db.execute(
            "SELECT task_id, artifact_path, description, created_at FROM artifacts"
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            {
                "task_id": r[0],
                "artifact_path": r[1],
                "description": r[2],
                "created_at": r[3],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """关闭数据库连接。"""
        if self._db is not None:
            await self._db.close()
            self._db = None
            logger.info("SQLite 存储已关闭")
