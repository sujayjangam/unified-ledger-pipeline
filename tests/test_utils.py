from app.services.utils import get_sgt_now

def test_get_sgt_now_is_timezone_aware_at_utc_plus_8():
    now = get_sgt_now()
    assert now.tzinfo is not None
    assert now.utcoffset().total_seconds() == 8 * 60 * 60
