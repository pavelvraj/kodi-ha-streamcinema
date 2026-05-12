# Kodi HA Stream Cinema

Kodi plugin a online Kodi repository pro HA Stream Cinema.

## Online instalace do Kodi

1. Stahni repository ZIP:

```text
https://raw.githubusercontent.com/pavelvraj/kodi-ha-streamcinema/main/zips/repository.pavelvraj.kodi-ha-streamcinema/repository.pavelvraj.kodi-ha-streamcinema-0.1.0.zip
```

2. V Kodi otevri `Add-ons` -> `Install from zip file` a nainstaluj repository.
3. Potom otevri `Install from repository` -> `Pavel Vraj Kodi Add-ons` -> `Video add-ons` -> `HA Stream Cinema`.
4. V nastaveni doplnku nastav URL HA Stream Cinema API, typicky:

```text
http://IP_ADRESA_HOME_ASSISTANTU:8765
```

## Aktualizace

Kodi nacita metadata z:

```text
https://raw.githubusercontent.com/pavelvraj/kodi-ha-streamcinema/main/addons.xml
```

Pri zvyseni verze v `plugin.video.ha-stream-cinema/addon.xml`, pregenerovani ZIPu a `addons.xml.md5` Kodi nabidne aktualizaci automaticky.
