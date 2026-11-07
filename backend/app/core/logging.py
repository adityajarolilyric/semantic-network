import json
import logging
import traceback
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "time": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        for field in (
            "request_id",
            "method",
            "path",
            "status",
            "duration_ms",
            "error_type",
        ):
            if hasattr(record, field):
                entry[field] = getattr(record, field)
        if record.exc_info:
            entry["error_type"] = record.exc_info[0].__name__
            entry["traceback"] = traceback.format_tb(record.exc_info[2])
        return json.dumps(entry)


def configure_logging():
    logger = logging.getLogger("metadata")
    if not any(
        getattr(handler, "metadata_handler", False) for handler in logger.handlers
    ):
        handler = logging.StreamHandler()
        handler.metadata_handler = True
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
