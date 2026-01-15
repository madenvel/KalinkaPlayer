"""
Decorators for seamless command queue integration.
"""

import asyncio
import functools
import inspect
from typing import Any, Callable, Optional, TypeVar, overload
import weakref

from .queue import CommandQueue

# Global registry of command queues per instance
# Uses weak references to avoid preventing garbage collection
_queue_registry: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()

T = TypeVar("T")


@overload
def queued(
    func: Callable[..., Any],
) -> Callable[..., asyncio.Task]: ...


@overload
def queued(
    func: None = None,
    *,
    timeout: Optional[float] = None,
    queue_attr: str = "_command_queue",
) -> Callable[[Callable[..., Any]], Callable[..., asyncio.Task]]: ...


def queued(
    func: Optional[Callable[..., Any]] = None,
    *,
    timeout: Optional[float] = None,
    queue_attr: str = "_command_queue",
) -> Any:
    """
    Decorator to route async method calls through a command queue.

    The decorated method will automatically submit calls to a CommandQueue
    and return an asyncio.Task that can be awaited or ignored.

    Can be used with or without parentheses:
        @queued
        @queued()
        @queued(timeout=5.0)

    Args:
        func: The async function being decorated (set automatically).
        timeout: Optional timeout in seconds for this specific method.
        queue_attr: Attribute name to store the queue instance (default: "_command_queue").

    Returns:
        Decorated method that returns asyncio.Task.

    Example:
        >>> class MyService:
        ...     @queued
        ...     async def process1(self, data: str) -> str:
        ...         return f"Processed: {data}"
        ...
        ...     @queued(timeout=5.0)
        ...     async def process2(self, data: str) -> str:
        ...         return f"Processed: {data}"
        ...
        >>> service = MyService()
        >>> task1 = service.process1("hello")  # Returns Task, queued
        >>> task2 = service.process2("world")  # Returns Task, queued
        >>> result1 = await task1  # Wait for result
        >>> result2 = await task2  # Wait for result
    """

    def decorator(f: Callable[..., Any]) -> Callable[..., asyncio.Task]:
        @functools.wraps(f)
        def wrapper(self, *args, **kwargs) -> asyncio.Task:
            # Get or create the command queue for this instance
            queue = _get_or_create_queue(self, queue_attr)

            # Submit the original function to the queue
            # We need to bind 'self' to the function
            bound_func = functools.partial(f, self)

            # Submit returns a coroutine, we wrap it in ensure_future to get a Task immediately
            # This allows fire-and-forget usage while still being awaitable
            return asyncio.ensure_future(
                _make_queued_call(queue, bound_func, args, kwargs, timeout)
            )

        return wrapper

    # If func is None, decorator was called with parentheses: @queued() or @queued(timeout=5)
    if func is None:
        return decorator

    # If func is provided, decorator was called without parentheses: @queued
    return decorator(func)


async def _make_queued_call(
    queue: CommandQueue,
    func: Callable,
    args: tuple,
    kwargs: dict,
    timeout: Optional[float],
) -> Any:
    """
    Helper to submit a call to the queue and await its result.

    This allows the decorator to return a Task immediately while
    the actual queue submission happens asynchronously.
    """
    # queue.submit returns a Task, await it to get the actual result
    return await (await queue.submit(func, *args, timeout=timeout, **kwargs))


def _get_or_create_queue(instance: Any, queue_attr: str) -> CommandQueue:
    """
    Get or create a CommandQueue for the given instance.

    Uses a weak reference registry to track queues per instance,
    allowing automatic cleanup when instances are garbage collected.
    """
    # Check if instance already has a queue
    if hasattr(instance, queue_attr):
        return getattr(instance, queue_attr)

    # Check weak reference registry
    if instance in _queue_registry:
        return _queue_registry[instance]

    # Create a new queue
    queue = CommandQueue()

    # Store in both the instance and the registry
    setattr(instance, queue_attr, queue)
    _queue_registry[instance] = queue

    # Auto-start the queue
    asyncio.create_task(queue.start())

    return queue


def queued_class(
    timeout: Optional[float] = None,
    queue_attr: str = "_command_queue",
    exclude: Optional[list[str]] = None,
) -> Callable[[type[T]], type[T]]:
    """
    Class decorator to route all async methods through a command queue.

    Automatically applies the @queued decorator to all async methods
    in the class (except those in the exclude list).

    Args:
        timeout: Optional default timeout for all methods.
        queue_attr: Attribute name to store the queue instance.
        exclude: List of method names to exclude from queuing.

    Returns:
        Decorated class with queued methods.

    Example:
        >>> @queued_class(timeout=10.0)
        ... class TaskProcessor:
        ...     async def task1(self, x: int) -> int:
        ...         return x * 2
        ...     async def task2(self, s: str) -> str:
        ...         return s.upper()
        ...
        >>> processor = TaskProcessor()
        >>> result = await processor.task1(42)  # Automatically queued
    """
    exclude_set = set(exclude or [])
    # Always exclude special methods
    exclude_set.update(["__init__", "__del__", "__new__", "__aenter__", "__aexit__"])

    def decorator(cls: type[T]) -> type[T]:
        # Find all async methods
        for attr_name in dir(cls):
            if attr_name in exclude_set or attr_name.startswith("_"):
                continue

            attr = getattr(cls, attr_name)

            # Check if it's an async method
            if inspect.iscoroutinefunction(attr):
                # Apply the queued decorator
                queued_method = queued(timeout=timeout, queue_attr=queue_attr)(attr)
                setattr(cls, attr_name, queued_method)

        # Add public methods for queue control
        async def stop_queue(self, wait: bool = True) -> None:
            """
            Stop the command queue.

            Args:
                wait: If True, wait for all queued commands to complete.
                     If False, stop immediately without waiting.
            """
            queue = _get_or_create_queue(self, queue_attr)
            await queue.stop(wait=wait)

        async def stop_queue_now(self) -> int:
            """
            Stop the queue immediately and cancel all pending and executing tasks.

            Returns:
                Number of tasks cancelled.
            """
            queue = _get_or_create_queue(self, queue_attr)
            cancelled = await queue.cancel_all()
            await queue.stop(wait=False)
            return cancelled

        cls.stop_queue = stop_queue  # type: ignore
        cls.stop_queue_now = stop_queue_now  # type: ignore

        # Add context manager support for cleanup
        original_aenter = getattr(cls, "__aenter__", None)
        original_aexit = getattr(cls, "__aexit__", None)

        async def __aenter__(self):
            if original_aenter:
                await original_aenter(self)
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            # Stop the queue gracefully
            queue = _get_or_create_queue(self, queue_attr)
            await queue.stop(wait=True)

            if original_aexit:
                return await original_aexit(self, exc_type, exc_val, exc_tb)
            return False

        cls.__aenter__ = __aenter__  # type: ignore
        cls.__aexit__ = __aexit__  # type: ignore

        return cls

    return decorator
