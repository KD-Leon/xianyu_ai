"""
进程管理服务
负责管理爬虫进程的启动和停止
"""

import asyncio
import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from typing import Awaitable, Callable, Dict, TextIO

from src.ai_handler import send_ntfy_notification
from src.config import STATE_FILE
from src.failure_guard import FailureGuard
from src.infrastructure.persistence.sqlite_task_repository import find_task_by_name_sync
from src.utils import build_task_log_path

STOP_TIMEOUT_SECONDS = 20
SPIDER_DEBUG_LIMIT_ENV = "SPIDER_DEBUG_LIMIT"
LifecycleHook = Callable[[int], Awaitable[None] | None]


class ProcessService:
    """进程管理服务"""

    def __init__(self, pid_dir: str | None = None):
        self.processes: Dict[int, asyncio.subprocess.Process] = {}
        self.log_paths: Dict[int, str] = {}
        self.log_handles: Dict[int, TextIO] = {}
        self.task_names: Dict[int, str] = {}
        self.exit_watchers: Dict[int, asyncio.Task] = {}
        self.failure_guard = FailureGuard()
        self._on_started: LifecycleHook | None = None
        self._on_stopped: LifecycleHook | None = None
        # 串行化启动，避免同一任务被定时与手动同时拉起两次。
        self._start_lock = asyncio.Lock()
        # 运行标记目录：服务重启后据此回收上一进程遗留的爬虫子进程。
        self._pid_dir = pid_dir or os.getenv(
            "TASK_RUNTIME_DIR", "logs/.task_runtime"
        )

    def set_lifecycle_hooks(
        self,
        *,
        on_started: LifecycleHook | None = None,
        on_stopped: LifecycleHook | None = None,
    ) -> None:
        self._on_started = on_started
        self._on_stopped = on_stopped

    # ---- 运行标记：用于服务重启后回收孤儿爬虫进程 ----

    def _pid_file_path(self, task_id: int) -> str:
        return os.path.join(self._pid_dir, f"task_{task_id}.pid")

    def _write_pid_file(self, task_id: int, pid: int, task_name: str) -> None:
        try:
            os.makedirs(self._pid_dir, exist_ok=True)
            path = self._pid_file_path(task_id)
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(
                    {"task_id": task_id, "task_name": task_name, "pid": pid}, fh
                )
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except OSError as exc:
            print(f"[ProcessService] 写入运行标记失败: {exc}")

    def _remove_pid_file(self, task_id: int) -> None:
        with contextlib.suppress(OSError):
            os.remove(self._pid_file_path(task_id))

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @staticmethod
    def _read_command_line(pid: int) -> str | None:
        """读取进程命令行；Windows 上无法可靠读取，返回 None。"""
        if sys.platform == "win32":
            return None
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout.strip() or None

    @staticmethod
    def _terminate_pid_group(pid: int, timeout: float = 10.0) -> None:
        try:
            pgid = os.getpgid(pid)
        except OSError:
            pgid = pid

        try:
            os.killpg(pgid, signal.SIGTERM)
        except OSError:
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGTERM)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not ProcessService._pid_alive(pid):
                return
            time.sleep(0.2)

        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)

    def reconcile_orphans(self) -> None:
        """回收上一个服务进程遗留的爬虫子进程。

        否则重启后 UI/调度认为任务空闲，下一次触发会再起一份，
        造成重复 AI 费用、重复通知以及图片目录互相删除。
        """
        if not os.path.isdir(self._pid_dir):
            return

        for name in os.listdir(self._pid_dir):
            if not name.endswith(".pid"):
                continue
            path = os.path.join(self._pid_dir, name)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    info = json.load(fh)
                pid = int(info.get("pid") or 0)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                with contextlib.suppress(OSError):
                    os.remove(path)
                continue

            if pid <= 0 or not self._pid_alive(pid):
                with contextlib.suppress(OSError):
                    os.remove(path)
                continue

            command = self._read_command_line(pid)
            if command is None:
                # 无法确认归属时宁可不动，避免误杀同 PID 的无关进程。
                print(
                    f"[ProcessService] 检测到残留进程 PID={pid}，"
                    "无法确认是否为本项目的爬虫，已跳过清理。"
                )
                continue
            if "spider_v2.py" not in command:
                with contextlib.suppress(OSError):
                    os.remove(path)
                continue

            print(f"[ProcessService] 终止上次运行遗留的爬虫进程 PID={pid}")
            self._terminate_pid_group(pid)
            with contextlib.suppress(OSError):
                os.remove(path)

    async def _invoke_hook(self, hook: LifecycleHook | None, task_id: int) -> None:
        if hook is None:
            return
        result = hook(task_id)
        if asyncio.iscoroutine(result):
            await result

    def _resolve_cookie_path(self, task_name: str) -> str | None:
        """Best-effort cookie/state path for a task."""
        try:
            task = find_task_by_name_sync(task_name)
            if task and isinstance(task.account_state_file, str) and task.account_state_file.strip():
                return task.account_state_file.strip()
        except Exception:
            pass

        return STATE_FILE if os.path.exists(STATE_FILE) else None

    def is_running(self, task_id: int) -> bool:
        """检查任务是否正在运行"""
        process = self.processes.get(task_id)
        return process is not None and process.returncode is None

    async def _drain_finished_process(self, task_id: int) -> None:
        process = self.processes.get(task_id)
        if process is None or process.returncode is None:
            return

        watcher = self.exit_watchers.get(task_id)
        if watcher is not None:
            await asyncio.shield(watcher)
            return

        self._cleanup_runtime(task_id, process)
        await self._invoke_hook(self._on_stopped, task_id)

    def _open_log_file(self, task_id: int, task_name: str) -> tuple[str, TextIO]:
        os.makedirs("logs", exist_ok=True)
        log_file_path = build_task_log_path(task_id, task_name)
        log_file_handle = open(log_file_path, "a", encoding="utf-8")
        return log_file_path, log_file_handle

    def _build_spawn_command(self, task_name: str) -> list[str]:
        command = [
            sys.executable,
            "-u",
            "spider_v2.py",
            "--task-name",
            task_name,
        ]
        debug_limit = str(os.getenv(SPIDER_DEBUG_LIMIT_ENV, "")).strip()
        if debug_limit.isdigit() and int(debug_limit) > 0:
            command.extend(["--debug-limit", debug_limit])
        return command

    async def _spawn_process(
        self,
        task_name: str,
        log_file_handle: TextIO,
    ) -> asyncio.subprocess.Process:
        preexec_fn = os.setsid if sys.platform != "win32" else None
        child_env = os.environ.copy()
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env["PYTHONUTF8"] = "1"
        return await asyncio.create_subprocess_exec(
            *self._build_spawn_command(task_name),
            stdout=log_file_handle,
            stderr=log_file_handle,
            preexec_fn=preexec_fn,
            env=child_env,
        )

    def _register_runtime(
        self,
        task_id: int,
        task_name: str,
        process: asyncio.subprocess.Process,
        log_file_path: str,
        log_file_handle: TextIO,
    ) -> None:
        self.processes[task_id] = process
        self.log_paths[task_id] = log_file_path
        self.log_handles[task_id] = log_file_handle
        self.task_names[task_id] = task_name
        self._write_pid_file(task_id, process.pid, task_name)
        self.exit_watchers[task_id] = asyncio.create_task(self._watch_process_exit(process))

    async def start_task(self, task_id: int, task_name: str) -> bool:
        """启动任务进程（串行化，避免并发启动同一任务）"""
        async with self._start_lock:
            return await self._start_task_locked(task_id, task_name)

    async def _start_task_locked(self, task_id: int, task_name: str) -> bool:
        await self._drain_finished_process(task_id)
        if self.is_running(task_id):
            print(f"任务 '{task_name}' (ID: {task_id}) 已在运行中")
            return False

        # 登录态解析与熔断状态都涉及文件/SQLite IO，放到线程池避免阻塞事件循环。
        cookie_path = await asyncio.to_thread(self._resolve_cookie_path, task_name)
        decision = await asyncio.to_thread(
            self.failure_guard.should_skip_start,
            task_name,
            cookie_path=cookie_path,
        )
        if decision.skip:
            await self._notify_skip(task_name, decision)
            return False

        log_file_path = ""
        log_file_handle = None
        try:
            log_file_path, log_file_handle = self._open_log_file(task_id, task_name)
            process = await self._spawn_process(task_name, log_file_handle)
        except Exception as exc:
            self._close_log_handle(log_file_handle)
            print(f"启动任务 '{task_name}' 失败: {exc}")
            return False

        self._register_runtime(task_id, task_name, process, log_file_path, log_file_handle)
        print(f"启动任务 '{task_name}' (PID: {process.pid})")
        await self._invoke_hook(self._on_started, task_id)
        return True

    async def _notify_skip(self, task_name: str, decision) -> None:
        print(
            f"[FailureGuard] 跳过启动任务 '{task_name}'，已暂停重试 "
            f"(连续失败 {decision.consecutive_failures}/{self.failure_guard.threshold})"
        )
        if not decision.should_notify:
            return
        try:
            await send_ntfy_notification(
                {
                    "商品标题": f"[任务暂停] {task_name}",
                    "当前售价": "N/A",
                    "商品链接": "#",
                },
                "任务处于暂停状态，将跳过执行。\n"
                f"原因: {decision.reason}\n"
                f"连续失败: {decision.consecutive_failures}/{self.failure_guard.threshold}\n"
                f"暂停到: {decision.paused_until.strftime('%Y-%m-%d %H:%M:%S') if decision.paused_until else 'N/A'}\n"
                "修复方法: 更新登录态/cookies文件后会自动恢复。",
            )
        except Exception as exc:
            print(f"发送任务暂停通知失败: {exc}")

    async def _watch_process_exit(self, process: asyncio.subprocess.Process) -> None:
        returncode = await process.wait()
        task_id = self._find_task_id_by_process(process)
        if task_id is None:
            return
        if returncode not in (0, None):
            # 子进程可能绕过 FailureGuard 直接退出（如登录态缺失），
            # 至少把退出码写进日志，避免任务"悄无声息地结束"。
            print(
                f"[ProcessService] 任务进程 PID={process.pid} "
                f"异常退出，退出码={returncode}"
            )
        self._cleanup_runtime(task_id, process)
        await self._invoke_hook(self._on_stopped, task_id)

    def _find_task_id_by_process(self, process: asyncio.subprocess.Process) -> int | None:
        for task_id, current_process in self.processes.items():
            if current_process is process:
                return task_id
        return None

    def _cleanup_runtime(
        self,
        task_id: int,
        process: asyncio.subprocess.Process,
    ) -> None:
        if self.processes.get(task_id) is not process:
            return
        self.processes.pop(task_id, None)
        self.log_paths.pop(task_id, None)
        self.task_names.pop(task_id, None)
        self._close_log_handle(self.log_handles.pop(task_id, None))
        self.exit_watchers.pop(task_id, None)
        self._remove_pid_file(task_id)

    def _close_log_handle(self, log_handle: TextIO | None) -> None:
        if log_handle is None:
            return
        with contextlib.suppress(Exception):
            log_handle.close()

    def _append_stop_marker(self, log_path: str | None) -> None:
        if not log_path:
            return
        try:
            timestamp = datetime.now().strftime(" %Y-%m-%d %H:%M:%S")
            with open(log_path, "a", encoding="utf-8") as log_file:
                log_file.write(f"[{timestamp}] !!! 任务已被终止 !!!\n")
        except Exception as exc:
            print(f"写入任务终止标记失败: {exc}")

    async def stop_task(self, task_id: int) -> bool:
        """停止任务进程"""
        await self._drain_finished_process(task_id)
        process = self.processes.get(task_id)
        if process is None:
            print(f"任务 ID {task_id} 没有正在运行的进程")
            return False
        if process.returncode is not None:
            await self._await_exit_watcher(task_id)
            print(f"任务进程 {process.pid} (ID: {task_id}) 已退出，略过停止")
            return False

        try:
            await self._terminate_process(process, task_id)
            self._append_stop_marker(self.log_paths.get(task_id))
            await self._await_exit_watcher(task_id)
            print(f"任务进程 {process.pid} (ID: {task_id}) 已终止")
            return True
        except ProcessLookupError:
            print(f"进程 (ID: {task_id}) 已不存在")
            return False
        except Exception as exc:
            print(f"停止任务进程 (ID: {task_id}) 时出错: {exc}")
            return False

    async def _terminate_process(
        self,
        process: asyncio.subprocess.Process,
        task_id: int,
    ) -> None:
        if sys.platform != "win32":
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        else:
            process.terminate()

        try:
            await asyncio.wait_for(process.wait(), timeout=STOP_TIMEOUT_SECONDS)
            return
        except asyncio.TimeoutError:
            print(
                f"任务进程 {process.pid} (ID: {task_id}) 未在 "
                f"{STOP_TIMEOUT_SECONDS} 秒内退出，准备强制终止..."
            )

        if sys.platform != "win32":
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        else:
            process.kill()
        await process.wait()

    async def _await_exit_watcher(self, task_id: int) -> None:
        watcher = self.exit_watchers.get(task_id)
        if watcher is None:
            return
        await asyncio.shield(watcher)

    def reindex_after_delete(self, deleted_task_id: int) -> None:
        """删除任务后同步重排运行时索引，避免任务下标漂移。"""
        self.processes = self._reindex_mapping(self.processes, deleted_task_id)
        self.log_paths = self._reindex_mapping(self.log_paths, deleted_task_id)
        self.log_handles = self._reindex_mapping(self.log_handles, deleted_task_id)
        self.task_names = self._reindex_mapping(self.task_names, deleted_task_id)
        self.exit_watchers = self._reindex_mapping(self.exit_watchers, deleted_task_id)

    def _reindex_mapping(self, mapping: Dict[int, object], deleted_task_id: int) -> Dict[int, object]:
        reindexed: Dict[int, object] = {}
        for task_id, value in mapping.items():
            if task_id == deleted_task_id:
                continue
            next_task_id = task_id - 1 if task_id > deleted_task_id else task_id
            reindexed[next_task_id] = value
        return reindexed

    async def stop_all(self) -> None:
        """停止所有任务进程"""
        task_ids = list(self.processes.keys())
        for task_id in task_ids:
            await self.stop_task(task_id)
