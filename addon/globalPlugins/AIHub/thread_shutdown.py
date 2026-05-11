"""Unified cooperative shutdown for AI-Hub worker threads (conversation, tools, recording)."""
import threading
from typing import Any

# Join timeout in seconds; 0 means do not block the UI waiting for thread exit.
DEFAULT_JOIN_TIMEOUT = 0.0


def stop_worker_thread(worker: Any, *, join_timeout: float = DEFAULT_JOIN_TIMEOUT) -> None:
	"""Signal ``worker`` to finish (``stop()`` or ``abort()``), then ``join``.

	Use this for ``CompletionThread``, ``RecordThread``, plain ``threading.Thread``, or any
	object that exposes ``stop`` / ``abort`` plus ``join`` when it is a ``threading.Thread``.
	Errors from stop/abort/join are swallowed so shutdown paths stay robust.
	"""
	if worker is None:
		return
	if hasattr(worker, "stop"):
		try:
			worker.stop()
		except Exception:
			pass
	elif hasattr(worker, "abort"):
		try:
			worker.abort()
		except Exception:
			pass
	if isinstance(worker, threading.Thread):
		try:
			worker.join(timeout=join_timeout)
		except Exception:
			pass
