# ============================================================
# bot/config/logging_config.py
# claude code changed: new file — fixes architecture audit finding L5.
# bot_runner.py's setup_logging() previously hardcoded "trades.log" as a
# bare string inside the function body — a relative path whose actual
# location on disk depended on whatever directory the bot happened to be
# launched from. Pulling it out to a single named constant here doesn't
# change behaviour (still a relative path, still "trades.log" by default)
# but gives it one place to override instead of editing inside a function.
#
# Not wired into Django's own LOGGING setting deliberately: bot_runner.py
# is a standalone script with its own manual RotatingFileHandler setup,
# not a Django request-handling process. Since bot_runner.py now calls
# django.setup() (see its own header comment), defining Django's LOGGING
# dict here would make dictConfig() apply during setup() AND THEN
# setup_logging() would add its own handlers on top — duplicate handlers,
# duplicate log lines. Keeping this separate avoids that.
# ============================================================

# Path to the live bot's rotating log file. Relative paths resolve
# against the current working directory the bot was launched from.
LOG_FILE_PATH = "trades.log"

# Rotate after this many bytes
LOG_MAX_BYTES = 5 * 1024 * 1024   # 5MB

# How many rotated backups to keep
LOG_BACKUP_COUNT = 3
