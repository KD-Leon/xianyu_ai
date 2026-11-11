"""
任务管理服务
封装任务相关的业务逻辑
"""
from typing import List, Optional
from src.domain.models.task import Task, TaskCreate, TaskUpdate
from src.domain.repositories.task_repository import TaskRepository


class DuplicateTaskNameError(ValueError):
    """任务名称重复。"""


class TaskService:
    """任务管理服务"""

    def __init__(self, repository: TaskRepository):
        self.repository = repository

    @staticmethod
    def _normalize_task_name(name: str) -> str:
        return str(name or "").strip().casefold()

    async def _ensure_unique_task_name(
        self, task_name: str, *, exclude_id: Optional[int] = None
    ) -> None:
        """任务名是爬虫子进程解析配置的键，重名会让定时任务跑错配置。"""
        target = self._normalize_task_name(task_name)
        for existing in await self.repository.find_all():
            if exclude_id is not None and existing.id == exclude_id:
                continue
            if self._normalize_task_name(existing.task_name) == target:
                raise DuplicateTaskNameError(
                    f"任务名称 '{task_name}' 已存在，请使用不同名称。"
                )

    async def get_all_tasks(self) -> List[Task]:
        """获取所有任务"""
        return await self.repository.find_all()

    async def get_task(self, task_id: int) -> Optional[Task]:
        """获取单个任务"""
        return await self.repository.find_by_id(task_id)

    async def create_task(self, task_create: TaskCreate) -> Task:
        """创建新任务"""
        await self._ensure_unique_task_name(task_create.task_name)
        task = Task(**task_create.model_dump(), is_running=False)
        return await self.repository.save(task)

    async def update_task(self, task_id: int, task_update: TaskUpdate) -> Task:
        """更新任务"""
        task = await self.repository.find_by_id(task_id)
        if not task:
            raise ValueError(f"任务 {task_id} 不存在")

        if task_update.task_name is not None:
            await self._ensure_unique_task_name(
                task_update.task_name, exclude_id=task_id
            )

        updated_task = task.apply_update(task_update)
        return await self.repository.save(updated_task)

    async def delete_task(self, task_id: int) -> bool:
        """删除任务"""
        return await self.repository.delete(task_id)

    async def update_task_status(self, task_id: int, is_running: bool) -> Task:
        """更新任务运行状态。

        使用定向 UPDATE，避免"读整行再写回"覆盖用户同时修改的其他字段
        （例如任务刚结束时用户刚把任务禁用，回写又会把它启用）。
        """
        task = await self.repository.update_status(task_id, is_running)
        if not task:
            raise ValueError(f"任务 {task_id} 不存在")
        return task
