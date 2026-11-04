import json


def log(event, **fields):
    # Never log dataset contents, credentials, request bodies, or signed URLs.
    print(json.dumps({"event": event, **fields}, separators=(",", ":")), flush=True)
