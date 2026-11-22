# Loaded by gunicorn from the working directory (the repo root); the start command
# is unchanged. Masks the Messenger verify token in the access log (config/access_log.py).
from config.access_log import MaskingLogger

logger_class = MaskingLogger
