"""
Entry point for Render Cron Job.

This script closes any open time records from previous days by setting their
check_out to a plausible time based on each employee's usual shift duration
(falling back to 23:59:59 of the record date when no estimate is possible).
It is safe to run after midnight because it does not close today's records.

On Mondays in the app timezone, it also auto-fills the previous completed
week for active employees using historical/category work patterns.
"""

import os
import sys

# Al ejecutar `python tasks/run_auto_close.py`, Python pone tasks/ en sys.path
# (no la raíz del repo), así que `from main import app` falla con
# ModuleNotFoundError. Añadimos la raíz del proyecto para poder importarlo.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app
from tasks.scheduler import run_scheduled_auto_tasks


if __name__ == "__main__":
    run_scheduled_auto_tasks(app=app)
