"""
Queued - A generic async command queue with decorator support.
"""

from kalinka_queued.queue import CommandQueue, Command
from kalinka_queued.decorators import queued, queued_class

__version__ = "0.1.0"
__all__ = ["CommandQueue", "Command", "queued", "queued_class"]
