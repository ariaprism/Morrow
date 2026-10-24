"""Production WSGI entrypoint. Run exactly one worker, without preload or reload."""

from app import app, start_night_scheduler

# app:app alone does not start the reminder/proactive/backup scheduler.
# Gunicorn imports this module inside its single worker, after forking.
start_night_scheduler()
