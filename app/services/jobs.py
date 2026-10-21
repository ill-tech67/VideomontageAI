"""Фоновые задачи: рендер видео с прогрессом и доставкой результата."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile, Message
from sqlalchemy import select

from app.config import Settings, get_settings
from app.db.base import session_scope
from app.db.models import Job, Project
from app.services.rendering import HyperFramesRunner, RenderOptions

logger = logging.getLogger(__name__)

MAX_TELEGRAM_FILE_MB = 49


@dataclass(slots=True)
class RenderRequest:
    """Запрос на рендер."""

    user_id: int
    chat_id: int
    project_id: str | None
    project_dir: Path
    options: RenderOptions
    status_message_id: int | None = None
    title: str = "Видео"


class JobManager:
    """Планировщик фоновых рендеров."""

    def __init__(self, bot: Bot, runner: HyperFramesRunner, settings: Settings | None = None) -> None:
        self.bot = bot
        self.runner = runner
        self.settings = settings or get_settings()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._semaphore = asyncio.Semaphore(max(1, self.settings.max_concurrent_renders))

    # --- публичное API --------------------------------------------
    async def submit_render(self, request: RenderRequest) -> Job:
        """Поставить рендер в очередь и сразу вернуть задачу."""
        async with session_scope() as session:
            job = Job(
                user_id=request.user_id,
                project_id=request.project_id,
                kind="render",
                status="queued",
                stage="queued",
                progress=0,
                message="В очереди на рендер…",
                telegram_message_id=request.status_message_id,
            )
            session.add(job)
            await session.flush()
            job_id = job.id

        task = asyncio.create_task(self._run_render(job_id, request), name=f"render-{job_id}")
        self._tasks[job_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(job_id, None))
        return job

    def active_jobs(self, user_id: int | None = None) -> int:
        return len(self._tasks)

    async def active_job(self, project_id: str) -> Job | None:
        """Найти незавершённый рендер проекта (если он уже идёт)."""
        async with session_scope() as session:
            result = await session.scalars(
                select(Job)
                .where(
                    Job.project_id == project_id,
                    Job.status.in_(("queued", "running")),
                )
                .order_by(Job.created_at.desc())
            )
            return result.first()

    # --- внутренняя логика ----------------------------------------
    async def _update(self, job_id: str, **fields: object) -> None:
        async with session_scope() as session:
            job = await session.get(Job, job_id)
            if job is None:
                return
            for key, value in fields.items():
                setattr(job, key, value)
            session.add(job)

    async def _run_render(self, job_id: str, request: RenderRequest) -> None:
        async with self._semaphore:
            await self._update(
                job_id,
                status="running",
                stage="render",
                progress=1,
                message="Запускаю рендер…",
            )
            await self._notify(request, "🎬 <b>Рендер запущен</b>\nКомпозиция собирается в MP4…")

            last_pct = -1

            async def on_progress(pct: int, code: str) -> None:
                nonlocal last_pct
                if pct - last_pct < 10 and pct < 100:
                    return
                last_pct = pct
                await self._update(job_id, progress=pct, stage=code)
                await self._notify(request, f"🎬 <b>Рендер</b> — {pct}% ({code})")

            try:
                result = await self.runner.render(
                    request.project_dir, request.options, on_progress=on_progress
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Рендер упал")
                await self._finish_error(job_id, request, str(exc))
                return

            if not result.ok:
                await self._finish_error(job_id, request, result.summary() or "Рендер не удался")
                return

            output = Path(request.options.output)
            if not output.is_absolute():
                output = request.project_dir / output
            if not output.exists() or output.stat().st_size == 0:
                await self._finish_error(job_id, request, "Рендер отработал, но файл пустой")
                return

            await self._update(
                job_id,
                status="done",
                stage="done",
                progress=100,
                message="Готово",
                result_path=str(output),
            )
            if request.project_id:
                async with session_scope() as session:
                    project = await session.get(Project, request.project_id)
                    if project is not None:
                        project.status = "done"
                        project.result_path = str(output)
                        project.error = None
                        session.add(project)
            await self._deliver(request, output)

    async def _finish_error(self, job_id: str, request: RenderRequest, error: str) -> None:
        await self._update(
            job_id, status="failed", stage="failed", progress=0, error=error[:2000]
        )
        if request.project_id:
            async with session_scope() as session:
                project = await session.get(Project, request.project_id)
                if project is not None:
                    project.status = "failed"
                    project.error = error[:2000]
                    session.add(project)
        await self._notify(
            request,
            "❌ <b>Рендер не удался</b>\n<pre>"
            + error[:1500].replace("&", "&amp;").replace("<", "&lt;")
            + "</pre>",
        )

    async def _notify(self, request: RenderRequest, text: str) -> None:
        if not request.status_message_id:
            return
        try:
            await self.bot.edit_message_text(
                text,
                chat_id=request.chat_id,
                message_id=request.status_message_id,
                parse_mode="HTML",
            )
        except Exception:  # noqa: BLE001 - сообщение могли удалить
            logger.debug("Не удалось обновить статус рендера", exc_info=True)

    async def _deliver(self, request: RenderRequest, path: Path) -> None:
        size_mb = await asyncio.to_thread(lambda: path.stat().st_size / (1024 * 1024))
        caption = f"✅ <b>{request.title}</b>\nГотово за {size_mb:.1f} МБ"
        try:
            if size_mb > MAX_TELEGRAM_FILE_MB:
                await self.bot.send_document(
                    request.chat_id,
                    document=FSInputFile(path),
                    caption=caption + "\n(файл крупнее лимита Telegram — отправлен как документ)",
                )
            else:
                await self.bot.send_video(
                    request.chat_id,
                    video=FSInputFile(path),
                    caption=caption,
                    supports_streaming=True,
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Не удалось отправить видео")
            await self.bot.send_message(
                request.chat_id,
                f"✅ Рендер готов, но отправить файл не вышло: {exc}\n"
                f"Файл лежит на сервере: <code>{path}</code>",
                parse_mode="HTML",
            )


async def send_progress_hint(message: Message, text: str) -> None:
    """Быстрое обновление промежуточного сообщения (без parse_mode)."""
    try:
        await message.edit_text(text)
    except Exception:  # noqa: BLE001
        logger.debug("Не удалось обновить сообщение", exc_info=True)
