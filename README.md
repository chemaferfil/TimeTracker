# TimeTracker

A Flask-based time tracking application with real-time capabilities using SocketIO and eventlet.

## Features

- User authentication and authorization
- Time tracking for employees
- Admin dashboard for user management
- Real-time updates using WebSockets
- Export functionality to Excel/PDF
- Multi-user support with role-based access

## Technology Stack

- **Backend**: Flask, Flask-SocketIO, SQLAlchemy
- **Database**: PostgreSQL (production), SQLite (development)
- **Real-time**: Socket.IO with eventlet
- **Frontend**: HTML, Tailwind CSS, JavaScript
- **Deployment**: Gunicorn with eventlet workers

## Installation and Setup

### Prerequisites

- Python 3.8+
- Virtual environment (recommended)

### Local Development

1. Clone the repository:
```bash
git clone https://github.com/chemaferfil/TimeTracker.git
cd TimeTracker
```

2. Create and activate virtual environment:
```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Set up environment variables (optional):
```bash
# Create .env file
DATABASE_URL=your_database_url_here
```

5. Initialize the database:
```bash
python main.py
```

The application will be available at http://localhost:5000

## Production Deployment

### Render.com

This application is configured to deploy on Render.com with the following setup:

1. **Entry Point**: Use `wsgi.py` as the entry point
2. **Build Command**: `pip install -r requirements.txt`
3. **Start Command**: `gunicorn --worker-class eventlet -w 1 -b 0.0.0.0:$PORT wsgi:app --timeout 120`

### Key Configuration Files

- `wsgi.py`: Production entry point with proper eventlet initialization
- `Procfile`: For Heroku-style deployments
- `gunicorn.conf.py`: Gunicorn configuration
- `requirements.txt`: Python dependencies

## Eventlet Integration Fix

This project had issues with eventlet monkey patching when deployed with gunicorn. The following changes were made to resolve the issues:

### Problem
When using `gunicorn --worker-class eventlet` with Flask-SocketIO, the application was throwing errors:
- "eventlet.monkey_patch() must be called before importing other modules"
- "Working outside of application context"
- Threading lock errors with SQLAlchemy

### Solution

1. **Separated Application Creation**: Created `wsgi.py` as a dedicated entry point that properly initializes eventlet before importing the Flask app.

2. **Modified Database Configuration**: Added eventlet-compatible SQLAlchemy settings:
   ```python
   app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
       "pool_pre_ping": True,
       "pool_recycle": 300,
       "pool_timeout": 20,
       "max_overflow": 0,
       "poolclass": NullPool if os.getenv('DYNO') or os.getenv('RENDER') else None
   }
   ```

3. **Proper Monkey Patching**: Ensured eventlet.monkey_patch() is called with all necessary parameters:
   ```python
   import eventlet
   eventlet.monkey_patch(thread=True, socket=True, select=True, time=True, os=True)
   ```

4. **Database Initialization**: Moved database initialization to a separate function to avoid execution during import:
   ```python
   def initialize_app():
       with app.app_context():
           # Database setup code here
   ```

## Project Structure

```
TimeTracker/
├── main.py              # Main Flask application
├── wsgi.py             # Production entry point
├── requirements.txt    # Python dependencies
├── Procfile           # Deployment configuration
├── gunicorn.conf.py   # Gunicorn settings
├── models/            # Database models
├── routes/            # Flask blueprints
├── static/            # Static files (CSS, JS, images)
├── src/               # Frontend source
└── templates/         # Jinja2 templates
```

## API Endpoints

- `GET /`: Welcome page
- `GET /login`: Login form
- `POST /login`: User authentication
- `GET /logout`: Logout user
- `/time/*`: Time tracking endpoints
- `/admin/*`: Admin dashboard endpoints
- `/export/*`: Export functionality

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test locally
5. Submit a pull request

## License

This project is licensed under the MIT License.

## Troubleshooting

### Common Issues

1. **Module Import Errors**: Make sure you're using the correct Python interpreter from the virtual environment
2. **Database Connection Issues**: Check your DATABASE_URL environment variable
3. **Eventlet Errors**: Ensure you're using the `wsgi.py` entry point for production deployments

### Development

For local development, run:
```bash
python main.py
```

For production testing with gunicorn:
```bash
gunicorn --worker-class eventlet -w 1 -b 127.0.0.1:5000 wsgi:app
```
