from datetime import datetime, timedelta, timezone

# 🌐 Define Singapore Timezone (UTC+8)
SG_TZ = timezone(timedelta(hours=8))

def get_sgt_now():
    """Returns the current timezone-aware datetime in Singapore."""
    return datetime.now(SG_TZ)

def get_week_start(now):
    """Returns the datetime for the Monday of now's week."""
    return now - timedelta(days=now.weekday())

def get_month_start(now):
    """Returns the datetime for the 1st of now's month."""
    return now.replace(day=1)