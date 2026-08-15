"""Docker 任务执行。

优先使用 Docker SDK 运行容器；若 Docker 不可用（如开发/测试环境），
降级为子进程模拟执行——直接在本地运行 command，适用于
busybox echo / sleep 等探针和填充任务。

降级方案说明：
    无 Docker 环境下，command 列表被直接交给 subprocess.Popen 执行。
    这对探针任务（如 ["echo", "hello"]、["sleep", "3"]）和
    填充任务的冒烟测试足够。

Checkpoint 支持：
    支持 checkpoint 的任务在被抢占/超时时，执行器会尝试从约定的
    checkpoint 文件路径（CHECKPOINT_PATH）读取任务保存的进度快照，
    并通过 TaskResult.checkpoint_data 回传给协调器，以便重新调度时续跑。
    优雅停止：先发 SIGTERM 等待 PREEMPT_GRACE_SECONDS，再强制 SIGKILL。
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Optional

from agent.reporter import TaskAssignment

logger = logging.getLogger("cogrid.agent.executor")

# 优雅抢占的宽限时间（秒）。与 coordinator.scheduler.PREEMPT_GRACE_SECONDS 保持一致。
# 任务收到停止信号后有这段时间保存 checkpoint，之后强制终止。
PREEMPT_GRACE_SECONDS = 3

# 约定的 checkpoint 文件路径。支持 checkpoint 的任务应将进度快照写入此路径。
# Docker 模式下从容器内读取，子进程模式下从本地工作目录读取。
CHECKPOINT_PATH = "/tmp/cogrid_checkpoint"


@dataclass
class TaskResult:
    """任务执行结果。

    由 execute_task() 返回，传给 reporter.report_result() 上报。
    """

    task_id: str
    success: bool
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    artifact_path: str = ""
    # 任务被抢占时保存的 checkpoint 数据，回传给协调器以便续跑
    checkpoint_data: str = ""


class TaskExecutor:
    """任务执行器。

    优先使用 Docker SDK 运行容器；若 Docker 不可用，降级为子进程执行。
    Docker 可用性在首次调用时检测并缓存。

    线程安全：execute_task 可在 asyncio.to_thread 中并发调用。
    """

    def __init__(self) -> None:
        self._docker_client: Optional[Any] = None
        self._docker_available: Optional[bool] = None

    @property
    def docker_available(self) -> bool:
        """检查 Docker 是否可用（懒检测，结果缓存）。"""
        if self._docker_available is not None:
            return self._docker_available

        try:
            import docker  # 延迟导入，非 Docker 环境不加载

            self._docker_client = docker.from_env()
            self._docker_client.ping()
            self._docker_available = True
            logger.info("Docker 可用，使用容器模式执行任务")
        except Exception as e:
            self._docker_available = False
            logger.warning("Docker 不可用（%s），降级为子进程模式", e)
        return self._docker_available

    def execute_task(self, task: TaskAssignment) -> TaskResult:
        """执行任务，返回结果。

        根据 Docker 可用性自动选择执行方式：
            - Docker 可用：运行容器（含资源限制、超时、GPU 支持）
            - Docker 不可用：子进程模拟执行

        Checkpoint 支持：
            - 执行前检查是否有 checkpoint_data（来自上次被抢占的任务），有则记录日志
            - 超时/被抢占时，若 can_checkpoint=True，尝试从 CHECKPOINT_PATH 读取进度快照

        Args:
            task: 协调器分配的任务

        Returns:
            TaskResult 执行结果（始终返回，不抛异常）
        """
        logger.info(
            "开始执行任务: %s type=%s image=%s command=%s",
            task.task_id,
            task.task_type,
            task.image,
            task.command,
        )

        # 检查是否有可恢复的 checkpoint
        if task.checkpoint_data:
            logger.info(
                "任务 %s 携带 checkpoint 数据（%d 字节），将尝试从断点续跑",
                task.task_id,
                len(task.checkpoint_data),
            )
        if task.can_checkpoint:
            logger.info(
                "任务 %s 支持 checkpoint，超时/抢占时将尝试保存进度到 %s",
                task.task_id,
                CHECKPOINT_PATH,
            )

        if self.docker_available:
            return self._execute_docker(task)
        return self._execute_subprocess(task)

    def shutdown(self) -> None:
        """清理资源，关闭 Docker 客户端连接。"""
        if self._docker_client is not None:
            try:
                self._docker_client.close()
            except Exception:
                pass
            self._docker_client = None

    # ------------------------------------------------------------------
    # Docker 执行
    # ------------------------------------------------------------------

    @staticmethod
    def _capture_checkpoint_docker(
        container: Any, can_checkpoint: bool
    ) -> str:
        """从容器内读取 checkpoint 文件。

        支持 checkpoint 的任务在被终止前应将进度快照写入 CHECKPOINT_PATH。
        本方法在容器被 kill 前调用，尝试读取该文件。

        Returns:
            checkpoint 数据字符串；无法读取或不支持时返回空串。
        """
        if not can_checkpoint:
            return ""
        try:
            result = container.exec_run(
                ["cat", CHECKPOINT_PATH], demux=False
            )
            # exec_run 返回 (exit_code, output)
            if hasattr(result, "exit_code") and result.exit_code == 0:
                output = result.output
                if isinstance(output, bytes):
                    return output.decode("utf-8", errors="replace")
                return str(output) if output else ""
        except Exception as e:
            logger.debug("读取容器 checkpoint 失败: %s", e)
        return ""

    def _execute_docker(self, task: TaskAssignment) -> TaskResult:
        """通过 Docker SDK 执行任务。

        流程：拉取镜像 -> 运行容器（含资源限制）-> 等待完成（带超时）
              -> 获取日志 -> 清理容器
        """
        import docker  # 延迟导入

        start = time.monotonic()

        try:
            # 1. 拉取镜像（本地不存在时）
            try:
                self._docker_client.images.get(task.image)
            except docker.errors.ImageNotFound:
                logger.info("拉取镜像: %s", task.image)
                self._docker_client.images.pull(task.image)

            # 2. 构建资源限制
            #    CPU: 1 核 = 100000 微秒/周期 (cpu_period=100000)
            cpu_quota = (
                int(task.cpu_cores * 100000) if task.cpu_cores > 0 else None
            )
            mem_limit = f"{task.memory_mb}m" if task.memory_mb > 0 else None

            # 3. GPU 设备请求（nvidia-container-runtime）
            device_requests = None
            if task.gpu_count > 0:
                try:
                    device_requests = [
                        docker.types.DeviceRequest(
                            count=task.gpu_count,
                            capabilities=[["compute"]],
                        )
                    ]
                except Exception as e:
                    logger.warning("GPU 设备请求创建失败: %s", e)

            # 4. 运行容器（分离模式，后台运行）
            container = self._docker_client.containers.run(
                image=task.image,
                command=task.command if task.command else None,
                detach=True,
                cpu_quota=cpu_quota,
                cpu_period=100000 if cpu_quota else None,
                mem_limit=mem_limit,
                device_requests=device_requests,
                stdout=True,
                stderr=True,
                # TODO(executor): 生产环境应对填充/探针任务启用
                # network_mode="none" 实现网络隔离。当前使用默认 bridge
                # 网络以保证兼容性。
                # 参考：docs/specs/2026-08-15-cogrid-design.md §8 安全
                network_mode="bridge",
            )

            # 5. 等待容器结束（带超时）
            exit_code = -1
            checkpoint_data = ""
            try:
                result = container.wait(timeout=task.timeout_seconds)
                exit_code = result.get("StatusCode", -1)
            except Exception:
                # 超时或连接中断：先尝试保存 checkpoint，再终止容器
                logger.warning(
                    "任务 %s 执行超时（%ds），尝试保存 checkpoint 后终止容器",
                    task.task_id,
                    task.timeout_seconds,
                )
                # 优雅停止：先尝试读取 checkpoint，再 kill
                checkpoint_data = self._capture_checkpoint_docker(
                    container, task.can_checkpoint
                )
                if checkpoint_data:
                    logger.info(
                        "任务 %s 已保存 checkpoint（%d 字节）",
                        task.task_id,
                        len(checkpoint_data),
                    )
                # 先发 SIGTERM 等待宽限期，再强制 kill
                try:
                    container.stop(timeout=PREEMPT_GRACE_SECONDS)
                except Exception:
                    try:
                        container.kill()
                    except Exception:
                        pass
                # 等待容器真正停止
                try:
                    container.wait(timeout=5)
                except Exception:
                    pass

            # 6. 获取日志输出
            stdout, stderr = self._get_container_logs(container)

            # 7. 清理容器
            try:
                container.remove(force=True)
            except Exception:
                pass

            duration = time.monotonic() - start
            success = exit_code == 0

            logger.info(
                "任务 %s 完成: success=%s exit_code=%d 耗时=%.1fs",
                task.task_id,
                success,
                exit_code,
                duration,
            )

            return TaskResult(
                task_id=task.task_id,
                success=success,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_seconds=duration,
                checkpoint_data=checkpoint_data,
            )

        except docker.errors.ImageNotFound:
            duration = time.monotonic() - start
            msg = f"镜像不存在且拉取失败: {task.image}"
            logger.error("任务 %s %s", task.task_id, msg)
            return TaskResult(
                task_id=task.task_id,
                success=False,
                exit_code=-1,
                stderr=msg,
                duration_seconds=duration,
            )
        except Exception as e:
            duration = time.monotonic() - start
            logger.error(
                "任务 %s 执行异常: %s", task.task_id, e, exc_info=True
            )
            return TaskResult(
                task_id=task.task_id,
                success=False,
                exit_code=-1,
                stderr=str(e),
                duration_seconds=duration,
            )

    @staticmethod
    def _get_container_logs(container: Any) -> tuple[str, str]:
        """获取容器的 stdout / stderr 文本。

        优先使用 demux 分离输出；失败时回退到合并日志。
        """
        try:
            stdout_bytes, stderr_bytes = container.logs(
                stdout=True, stderr=True, demux=True
            )
            stdout = (stdout_bytes or b"").decode("utf-8", errors="replace")
            stderr = (stderr_bytes or b"").decode("utf-8", errors="replace")
        except Exception:
            # demux 失败时回退到合并日志（全部放入 stdout）
            try:
                raw = container.logs(stdout=True, stderr=True)
                stdout = (
                    raw.decode("utf-8", errors="replace") if raw else ""
                )
                stderr = ""
            except Exception:
                stdout = ""
                stderr = "无法获取容器日志"
        return stdout, stderr

    # ------------------------------------------------------------------
    # 子进程降级执行
    # ------------------------------------------------------------------

    @staticmethod
    def _capture_checkpoint_subprocess(can_checkpoint: bool) -> str:
        """从本地文件系统读取 checkpoint 文件。

        子进程模式下，任务与执行器在同一文件系统，checkpoint 文件
        写入 CHECKPOINT_PATH 后可直接读取。

        Returns:
            checkpoint 数据字符串；无法读取或不支持时返回空串。
        """
        if not can_checkpoint:
            return ""
        try:
            if os.path.exists(CHECKPOINT_PATH):
                with open(CHECKPOINT_PATH, "r", encoding="utf-8") as f:
                    return f.read()
        except Exception as e:
            logger.debug("读取本地 checkpoint 失败: %s", e)
        return ""

    def _execute_subprocess(self, task: TaskAssignment) -> TaskResult:
        """降级方案：子进程模拟执行。

        直接在本地运行 command（忽略 image），适用于无 Docker 环境
        下的探针和填充任务测试。超时则 kill 子进程。
        """
        start = time.monotonic()

        # 空命令直接返回成功
        if not task.command:
            return TaskResult(
                task_id=task.task_id,
                success=True,
                exit_code=0,
                duration_seconds=0.0,
            )

        try:
            proc = subprocess.Popen(
                task.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError as e:
            duration = time.monotonic() - start
            return TaskResult(
                task_id=task.task_id,
                success=False,
                exit_code=-1,
                stderr=f"命令未找到: {e}",
                duration_seconds=duration,
            )
        except Exception as e:
            duration = time.monotonic() - start
            logger.error(
                "任务 %s 子进程启动失败: %s", task.task_id, e, exc_info=True
            )
            return TaskResult(
                task_id=task.task_id,
                success=False,
                exit_code=-1,
                stderr=str(e),
                duration_seconds=duration,
            )

        try:
            stdout, stderr = proc.communicate(
                timeout=task.timeout_seconds
            )
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            # 超时：优雅停止——先 SIGTERM 等待宽限期（可保存 checkpoint），
            # 再强制 SIGKILL
            logger.warning(
                "任务 %s 执行超时（%ds），优雅终止子进程",
                task.task_id,
                task.timeout_seconds,
            )
            # 先发 SIGTERM，让任务有机会保存 checkpoint
            try:
                proc.terminate()  # 发送 SIGTERM
            except Exception:
                pass
            try:
                stdout, stderr = proc.communicate(
                    timeout=PREEMPT_GRACE_SECONDS
                )
            except subprocess.TimeoutExpired:
                # 宽限期内未退出，强制 SIGKILL
                proc.kill()
                stdout, stderr = proc.communicate()
            # 尝试读取 checkpoint
            checkpoint_data = self._capture_checkpoint_subprocess(
                task.can_checkpoint
            )
            if checkpoint_data:
                logger.info(
                    "任务 %s 已保存 checkpoint（%d 字节）",
                    task.task_id,
                    len(checkpoint_data),
                )
            duration = time.monotonic() - start
            return TaskResult(
                task_id=task.task_id,
                success=False,
                exit_code=-1,
                stdout=stdout or "",
                stderr=(stderr or "")
                + f"\n[超时: {task.timeout_seconds}s]",
                duration_seconds=duration,
                checkpoint_data=checkpoint_data,
            )

        duration = time.monotonic() - start
        success = exit_code == 0

        logger.info(
            "任务 %s 完成（子进程）: success=%s exit_code=%d 耗时=%.1fs",
            task.task_id,
            success,
            exit_code,
            duration,
        )

        return TaskResult(
            task_id=task.task_id,
            success=success,
            exit_code=exit_code,
            stdout=stdout or "",
            stderr=stderr or "",
            duration_seconds=duration,
        )
