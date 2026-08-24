def load_rows(client):
    try:
        return client.fetch_rows()
    except Exception:
        return []
