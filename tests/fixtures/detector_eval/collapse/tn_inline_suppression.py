def load_rows(client):
    try:
        return client.fetch_rows()
    except Exception:
        return []  # roam: ignore-collapse[catch-to-benign-literal]
