"""
Timezone utilities for TimeTracker application.
Handles conversion to/from Madrid timezone (Europe/Madrid).
"""

import pytz
from datetime import datetime


# Madrid timezone
MADRID_TZ = pytz.timezone('Europe/Madrid')


def get_madrid_now():
    """Get current datetime in Madrid timezone."""
    return datetime.now(MADRID_TZ)


def localize_to_madrid(dt):
    """
    Convert a naive datetime to Madrid timezone.
    If datetime is already timezone-aware, convert it to Madrid timezone.
    
    Args:
        dt: datetime object (naive or timezone-aware)
    
    Returns:
        datetime object in Madrid timezone
    """
    if dt is None:
        return None
    
    if dt.tzinfo is None:
        # Naive datetime - assume it's in Madrid timezone
        return MADRID_TZ.localize(dt)
    else:
        # Already timezone-aware - convert to Madrid
        return dt.astimezone(MADRID_TZ)


def convert_to_madrid(dt):
    """
    Convert datetime to Madrid timezone for display.
    Handles both naive and timezone-aware datetimes.
    
    Args:
        dt: datetime object
    
    Returns:
        datetime object in Madrid timezone
    """
    return localize_to_madrid(dt)