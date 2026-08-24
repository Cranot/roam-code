export function loadRows(client) {
  try {
    return client.fetchRows();
  } catch (error) {
    return [];
  }
}
