from datetime import date, timedelta

def resolve_date_reference(ref, today: date):
    ''' ref is a dictionary that is returned by DateReference in extraction.py
    The model outputs a dictionary where "kind" is the key to figure out the date
    of transaction '''
    
    if ref["kind"] == "unstated":
        return today

    if ref["kind"] == "today":
        return today

    if ref["kind"] == "days_ago":
        return today - timedelta(days=ref["days_ago"])

    # if none of the above conditions matched, then return None
    return None 