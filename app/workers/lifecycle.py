"""Worker lifecycle and signal handling."""

import signal
from types import FrameType

from app.core.logging import logger


class LifecycleManager:
    """Manages worker state and handles OS termination signals."""

    def __init__(self) -> None:
        self.should_stop: bool = False
        self._installed: bool = False

    def install_signal_handlers(self) -> None:
        """Register signal handlers for graceful shutdown on SIGINT and SIGTERM."""
        if self._installed:
            return

        def _handler(signum: int, frame: FrameType | None) -> None:
            sig_name = signal.Signals(signum).name
            logger.info(
                f"Received signal {sig_name}; initiating graceful worker shutdown",
                extra={"event": "worker_shutting_down", "signal": sig_name},
            )
            self.should_stop = True

        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, _handler)
        self._installed = True

    def request_stop(self) -> None:
        """Manually trigger worker shutdown."""
        self.should_stop = True


lifecycle = LifecycleManager()
