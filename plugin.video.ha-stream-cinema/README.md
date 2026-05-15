# HA Stream Cinema Kodi plugin

Jednoduchy Kodi klient pro aplikaci ve slozce `ha-app`.

## Instalace

1. Slozku `plugin.video.ha-stream-cinema` zabal do ZIPu nebo ji zkopiruj do Kodi `addons`.
2. V Kodi otevri nastaveni doplnku.
3. Do `URL` zadej adresu Home Assistantu s HASC aplikaci, typicky:

```text
http://IP_ADRESA_HOME_ASSISTANTU:8765
```

Plugin automaticky pouzije cestu `/api`, takze muzes zadat i adresu koncici `/api`.
V nastaveni lze zvolit vychozi akci pro streamy (`Stahnout`, `Prehrat`, `Zeptat se`), slozku pro stahovani a cetnost notifikaci prubehu.

Plugin pouziva tyto endpointy z HA aplikace:

- `GET /api/catalog`
- `GET /api/media/{media_id}`
- `GET /api/file_link/{provider}:{ident}`

Filmy a dily serialu po otevreni rovnou nabidnou dialog s dostupnymi streamy a jejich parametry. Katalog lze prochazet i podle zanru a prave bezici stahovani lze z doplnku zrusit.
