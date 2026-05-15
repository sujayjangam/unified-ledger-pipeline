from datetime import datetime, timedelta, timezone

# 🌐 Define Singapore Timezone (UTC+8)
SG_TZ = timezone(timedelta(hours=8))

def get_sgt_now():
    """Returns the current timezone-aware datetime in Singapore."""
    return datetime.now(SG_TZ)