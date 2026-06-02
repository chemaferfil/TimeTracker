"""
Entry point for Render Cron Job.

This script closes any open time records from previous days by setting their
check_out to 23:59:59 of their respective dates. It is safe to run after
midnight because it does not close today's records.

On Mondays in the app timezone, it also auto-fills the previous completed
week for active employees using historical/category work patterns.
"""

from main import app
from tasks.scheduler import run_scheduled_auto_tasks


if __name__ == "__main__":
    run_scheduled_auto_tasks(app=app)
