"""Console logging for the app's own loggers.

Uvicorn configures only its own loggers, so without this the app's INFO records, such
as the Ollama token-drift line, go nowhere. Third-party loggers are left alone.
"""

import logging

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(level: str) -> None:
    logger = logging.getLogger("app")
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(handler)
