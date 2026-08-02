"""Generated Protobuf bindings for the renderer protocol.

``renderer_pb2.py`` is committed, not built: wheels build in minimal
containers where running protoc is fragile. Regenerate after editing
``packages/kalinka-renderer/proto/.../renderer.proto`` with ``make proto``.
"""

from . import renderer_pb2

__all__ = ["renderer_pb2"]
