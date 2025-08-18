# gunicorn.conf.py
import eventlet
eventlet.monkey_patch()

bind = "0.0.0.0:$PORT"
worker_class = "eventlet"
workers = 1
timeout = 120
keepalive = 2
max_requests = 1000
max_requests_jitter = 100
preload_app = False
