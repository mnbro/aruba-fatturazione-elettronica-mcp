# Aruba Fatturazione Elettronica MCP Read-only

This project exposes Aruba Fatturazione Elettronica API GET operations as MCP tools. Business operations are strictly read-only.

Official documentation checked:

- English: https://fatturazioneelettronica.aruba.it/apidoc/docs_EN.html
- Italian: https://fatturazioneelettronica.aruba.it/apidoc/docs.html

The implementation follows the v1 read-only endpoint surface. Aruba's v2 documentation exists separately and is not implemented here.

## Guarantees

- No invoice upload tools.
- No signed invoice upload tools.
- No business POST/PUT/PATCH/DELETE tools.
- No `sendEsitoCommittente` POST tool.
- Auth POST is limited to Aruba's required signin and refresh lifecycle.
