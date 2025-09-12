# wsgi.py - Entry point for production deployment with eventlet
import eventlet
# Patch all modules before importing anything else
eventlet.monkey_patch(thread=True, socket=True, select=True, time=True, os=True)

import os
import sys

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import app with error handling
try:
    from main import app
    print("Application imported successfully", flush=True)
except Exception as e:
    print(f"Error importing main app: {e}", flush=True)
    # Create minimal fallback app
    from flask import Flask
    app = Flask(__name__)
    
    @app.route('/')
    def health_check():
        return "App is starting...", 200

if __name__ == "__main__":
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
