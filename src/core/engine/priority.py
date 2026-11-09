"""Who goes first on the model server inside one Beacon process.

The language model is the shared resource. A question is interactive and documentation is background work, so:
documentation waits between model calls while a question is being answered, and only one project is documented at
a time, so one project cannot keep the GPU busy for everyone. A model call already in progress is not interrupted.
"""

import threading
from contextlib import contextmanager

# A question that never ends (a hung server) must not stall documentation forever.
MAX_WAIT_SECONDS = 600


class ModelPriority:
    def __init__(self):
        self._active_queries = 0
        self._changed = threading.Condition()
        self._documentation = threading.Lock()

    @contextmanager
    def query(self):
        with self._changed:
            self._active_queries += 1
        try:
            yield
        finally:
            with self._changed:
                self._active_queries -= 1
                self._changed.notify_all()

    def wait_for_queries(self, timeout: float = MAX_WAIT_SECONDS) -> bool:
        """Blocks while a question is being answered. False if it gave up after `timeout` seconds."""
        with self._changed:
            return self._changed.wait_for(lambda: self._active_queries == 0, timeout)

    @contextmanager
    def documentation(self):
        with self._documentation:
            yield

    @property
    def active_queries(self) -> int:
        with self._changed:
            return self._active_queries


priority = ModelPriority()
