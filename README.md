# Kodi HA Stream Cinema

Kodi plugin a online Kodi repository pro HA Stream Cinema.

## Online instalace do Kodi

1. V Kodi otevri `Settings` -> `File manager` -> `Add source`.
2. Jako URL zdroje zadej:

```text
https://pavelvraj.github.io/kodi-ha-streamcinema/
```

3. Zdroj pojmenuj treba `Kodi HA Stream Cinema`.
4. Otevri `Add-ons` -> `Install from zip file`.
5. Vyber zdroj `Kodi HA Stream Cinema`.
6. Vyber `repository.pavelvraj.kodi-ha-streamcinema-0.1.2.zip`.
7. Potom otevri `Install from repository` -> `Pavel Vraj Kodi Add-ons` -> `Video add-ons` -> `HA Stream Cinema`.
8. V nastaveni doplnku nastav URL HA Stream Cinema API, typicky:

```text
http://IP_ADRESA_HOME_ASSISTANTU:8765
```

Primy GitHub web `https://github.com/pavelvraj/kodi-ha-streamcinema` zustava zdrojovy repozitar. Pro Kodi source je vhodny GitHub Pages odkaz vyse, protoze poskytuje jednoduchou stranku se ZIPem.

## Aktualizace

Kodi nacita metadata z:

```text
https://pavelvraj.github.io/kodi-ha-streamcinema/addons.xml
```

Pri zvyseni verze v `plugin.video.ha-stream-cinema/addon.xml`, pregenerovani ZIPu a `addons.xml.md5` Kodi nabidne aktualizaci automaticky.
