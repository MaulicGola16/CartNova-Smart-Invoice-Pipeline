import logging


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


def log_invoice(logger: logging.Logger, invoice_id: str, message: str, level: str = "info"):
    text = f"[invoice_id={invoice_id}] {message}"
    getattr(logger, level.lower(), logger.info)(text)