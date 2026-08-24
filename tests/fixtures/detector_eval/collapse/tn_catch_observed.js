export function loadRows(client) {
  try {
    return client.fetchRows();
  } catch (error) {
    console.warn("row fetch failed", error);
    throw error;
  }
}
