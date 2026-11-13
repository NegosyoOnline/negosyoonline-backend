LAST_OK_KEY = "file_backup:last_ok"  # cache: epoch seconds of the last fully good run
RUNNING_KEY = "file_backup:running"
DAY_KEY = "file_backup:day:{date}"
RUN_LOCK_SECONDS = 2 * 3600
DAY_KEPT_SECONDS = 2 * 24 * 3600
MAX_CHAIN = 12
CHAIN_COUNTDOWN = 60
