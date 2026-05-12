# HA Stream Cinema Kodi plugin

Jednoduchy Kodi klient pro aplikaci ve slozce `ha-app`.

## Instalace

1. Slozku `plugin.video.ha-stream-cinema` zabal do ZIPu nebo ji zkopiruj do Kodi `addons`.
2. V Kodi otevri nastaveni doplnku.
3. Do `URL HA Stream Cinema API` zadej adresu API, typicky:

```text
http://IP_ADRESA_HOME_ASSISTANTU:8765
```

Plugin pouziva tyto endpointy z HA aplikace:

- `GET /api/catalog`
- `GET /api/media/{media_id}`
- `GET /api/file_link/{provider}:{ident}`

Filmy se oteviraji na seznam ulozenych streamu. Serialy se prochazi pres serie, dily a potom streamy.
