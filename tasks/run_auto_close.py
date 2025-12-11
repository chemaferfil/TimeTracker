"""
Entry point for Render Cron Job.

This script closes any open time records from previous days by setting their
check_out to 23:59:59 of their respective dates. It is safe to run after
midnight because it does not close today's records.
"""

from main import app
from tasks.scheduler import auto_close_open_records


if __name__ == "__main__":
    auto_close_open_records(include_today=False, app=app)

