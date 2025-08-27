#!/bin/bash
echo "==> Starting TimeTracker application..."
echo "==> Python path: $(which python)"
echo "==> Python version: $(python --version)"
echo "==> Current directory: $(pwd)"
echo "==> Checking for gunicorn..."

# Try different ways to find gunicorn
if command -v gunicorn &> /dev/null; then
    echo "==> Found gunicorn at: $(which gunicorn)"
    gunicorn --worker-class sync -w 4 -b 0.0.0.0:$PORT main:app
elif python -m gunicorn --version &> /dev/null; then
    echo "==> Using python -m gunicorn"
    python -m gunicorn --worker-class sync -w 4 -b 0.0.0.0:$PORT main:app
elif [ -f ".venv/bin/gunicorn" ]; then
    echo "==> Using .venv/bin/gunicorn"
    .venv/bin/gunicorn --worker-class sync -w 4 -b 0.0.0.0:$PORT main:app
else
    echo "==> Gunicorn not found, trying to run with Flask directly"
    python main.py
fi