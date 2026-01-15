"""
Core command queue implementation with async execution and timeout monitoring.
"""

import asyncio
from typing import Any, Callable, Awaitable, Optional
from dataclasses import dataclass, field
from datetime import datetime
import uuid
import logging

logger = logging.getLogger(__name__)


@dataclass
class Command:
    """Represents a queued command with execution state."""

    id: str
    func: Callable[..., Awaitable[Any]]
    args: tuple
    kwargs: dict
    timeout: float
    submitted_at: datetime = field(default_factory=datetime.now)
    future: asyncio.Future = field(default_factory=asyncio.Future)
    _task: Optional[asyncio.Task] = field(default=None, init=False)

    def cancel(self) -> bool:
        """
        Cancel this command.

        Returns:
            True if cancellation was successful, False otherwise.
        """
        if self._task and not self._task.done():
            return self._task.cancel()
        return self.future.cancel()


class CommandQueue:
    """
    Generic FIFO command queue with timeout monitoring.

    Provides asynchronous command execution with:
    - FIFO ordering guarantee
    - Per-command timeout monitoring
    - Cancellation support
    - Thread-safe submission
    - Automatic lifecycle management
    """

    def __init__(self, default_timeout: float = 30.0):
        """
        Initialize the command queue.

        Args:
            default_timeout: Default timeout in seconds for commands without explicit timeout.
        """
        self._queue: asyncio.Queue[Command] = asyncio.Queue()
        self._commands: dict[str, Command] = {}
        self._lock = asyncio.Lock()
        self._default_timeout = default_timeout
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False
        self._started = False

    async def start(self) -> None:
        """Start the worker task if not already running."""
        async with self._lock:
            if self._started:
                return

            self._started = True
            self._running = True
            self._worker_task = asyncio.create_task(self._worker())
            logger.debug("CommandQueue worker started")

    async def stop(self, wait: bool = True) -> None:
        """
        Stop the worker and optionally wait for completion.

        Args:
            wait: If True, wait for all queued commands to complete.
        """
        async with self._lock:
            if not self._started:
                return

            self._running = False
            self._started = False

        if wait and self._worker_task:
            await self._worker_task

        logger.debug("CommandQueue worker stopped")

    async def submit(
        self,
        func: Callable[..., Awaitable[Any]],
        *args,
        timeout: Optional[float] = None,
        **kwargs,
    ) -> asyncio.Task:
        """
        Submit a command for asynchronous execution.

        Args:
            func: Async callable to execute.
            *args: Positional arguments for the callable.
            timeout: Timeout in seconds (uses default if not specified).
            **kwargs: Keyword arguments for the callable.

        Returns:
            asyncio.Task that can be awaited or ignored (fire-and-forget).
        """
        # Auto-start on first submission
        if not self._started:
            await self.start()

        cmd_id = str(uuid.uuid4())
        command = Command(
            id=cmd_id,
            func=func,
            args=args,
            kwargs=kwargs,
            timeout=timeout if timeout is not None else self._default_timeout,
        )

        async with self._lock:
            self._commands[cmd_id] = command

        await self._queue.put(command)

        # Create a Task wrapper around the Future for consistent interface
        task = asyncio.create_task(self._await_command(command))
        return task

    async def _await_command(self, command: Command) -> Any:
        """
        Internal helper to await command completion.

        Args:
            command: The command to wait for.

        Returns:
            The result of the command execution.

        Raises:
            Any exception raised during command execution.
        """
        return await command.future

    async def cancel_command(self, cmd_id: str) -> bool:
        """
        Cancel a pending or executing command.

        Args:
            cmd_id: The command ID to cancel.

        Returns:
            True if cancellation was successful, False otherwise.
        """
        async with self._lock:
            command = self._commands.get(cmd_id)

        if command:
            return command.cancel()
        return False

    async def cancel_all(self) -> int:
        """
        Cancel all pending and currently executing commands.

        Returns:
            Number of commands cancelled.
        """
        async with self._lock:
            commands = list(self._commands.values())

        cancelled_count = 0
        for command in commands:
            if command.cancel():
                cancelled_count += 1

        return cancelled_count

    async def _worker(self) -> None:
        """Worker coroutine that processes queued commands."""
        while self._running:
            try:
                # Use timeout to allow checking _running flag periodically
                command = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            # Execute the command with timeout monitoring
            try:
                # Create execution task
                exec_task = asyncio.ensure_future(
                    command.func(*command.args, **command.kwargs)
                )
                command._task = exec_task

                # Wait with timeout
                result = await asyncio.wait_for(exec_task, timeout=command.timeout)

                # Set result if future not cancelled
                if not command.future.cancelled():
                    command.future.set_result(result)

            except asyncio.CancelledError:
                # Command was cancelled
                if not command.future.cancelled():
                    command.future.cancel()
                logger.debug(f"Command {command.id} was cancelled")

            except asyncio.TimeoutError:
                # Command exceeded timeout
                error = TimeoutError(
                    f"Command {command.id} exceeded timeout of {command.timeout}s"
                )
                if not command.future.cancelled():
                    command.future.set_exception(error)
                logger.warning(
                    f"Command {command.id} timed out after {command.timeout}s"
                )

            except Exception as e:
                # Command raised an exception
                if not command.future.cancelled():
                    command.future.set_exception(e)
                logger.error(
                    f"Command {command.id} failed with error: {e}", exc_info=True
                )

            finally:
                self._queue.task_done()
                # Clean up command from registry
                async with self._lock:
                    self._commands.pop(command.id, None)

    async def __aenter__(self):
        """Context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        await self.stop(wait=True)
        return False
