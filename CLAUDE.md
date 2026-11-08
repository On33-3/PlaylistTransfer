# PlaylistTransfer

App de escritorio (Windows) para pasar playlists entre servicios de música: hoy Tidal ⇄ YouTube Music; después Spotify.
Proyecto open source pensado para GitHub. El plan completo, con decisiones y fases, está en `SPEC.md`: leelo antes de cambiar algo grande.

## Estado actual
- F0 aprobada (`prototype/index.html` es la referencia visual). **F1 hecha**: motor en `app/core/`, conectores en `app/connectors/`, login en `app/auth/`, tests en `tests/`.
- `legacy/transfer_playlist.py` es la **versión de consola que ya funciona** (probada con cuentas reales) y la referencia del motor. No la rompas hasta que la app la reemplace (F2). Se corre desde la raíz, así sigue usando las sesiones guardadas ahí.
- `app/core/matching.py` es la lógica del script más un cambio a propósito: artista y título son requisitos en `score` (ver SPEC). `test_same_results_as_legacy_script` verifica que no haya otras diferencias. Si cambiás el matching, revisalo contra los reportes reales (`reporte_*.csv`, locales) y actualizá ese test.
- Primer uso: nada viene con el repo ni con la app; historial, sesiones y preferencias se crean en `%LOCALAPPDATA%\PlaylistTransfer\` recién cuando hacen falta (SPEC, "Primer uso"). Los datos de ejemplo (prototipo, tests) son inventados o canciones conocidas, nunca el historial del usuario.
- Los conectores no imprimen ni llaman a `sys.exit`: lanzan `ConnectorError` (mensajes para el usuario). `remove()` exige `confirmed=True`.
- Entorno del usuario: Windows 11, Python 3.14 (`py`), PowerShell. Navegadores: Vivaldi (predeterminado), Brave, Chrome (del trabajo: **no usarlo por defecto**) y Edge.

## Reglas sin excepción
- **Nunca** commitear credenciales ni datos personales: `browser.json`, `oauth.json`, `tidal_session.json`, `.yt-login/`, `preferencias.json`, `reporte_*.csv`, `.env`. Verificá `.gitignore` antes de cada commit.
- **Nunca** quitar temas de una playlist sin una confirmación explícita del usuario en la UI.
- Antes de decir que algo funciona, ejecutalo (tests o el comando real) y mostrá la salida.

## Comandos
- Dependencias de desarrollo: `py -m pip install -r requirements-dev.txt`
- Tests: `py -m pytest -q`
- Script de consola (referencia): `py legacy/transfer_playlist.py` (o doble clic en `Transferir playlists.bat`)

## Cosas que no se deducen leyendo el código (aprendidas a los golpes)
- **Sin shebang** en los `.py`: el launcher `py` de Windows lo respeta y abre otro Python, que no tiene las librerías instaladas.
- **ytmusicapi** solo reconoce una sesión de navegador si los headers incluyen `authorization: SAPISIDHASH …`. Sin ese header la toma como OAuth y falla con "oauth_credentials not provided". Ver `sapisid_hash()` y `build_browser_json()`.
- **Vivaldi no deja que Playwright cree pestañas** ("Target.createTarget: Failed to open a new tab"). Solución en uso: lanzar el navegador elegido como proceso común con `--remote-debugging-port` y `--user-data-dir` propio, abierto en la URL de login, y leer las cookies con `Storage.getCookies` mediante un cliente CDP mínimo (`class CDP`). No manipular pestañas.
- Chrome y sus derivados no permiten automatizar el perfil normal del usuario: siempre se usa un perfil aparte en `.yt-login/<navegador>`.
- Detección de navegadores: registro de Windows, en `StartMenuInternet` (instalados) y `UrlAssociations\https\UserChoice` o `UserChoiceLatest` (predeterminado).
- `tidalapi.login_oauth_simple(fn)` llama a `fn` con un texto que contiene el link; se abre con el navegador elegido.
- YouTube Music: para quitar temas hace falta el `setVideoId` de cada aparición, no solo el `videoId`.
- Versiones probadas: tidalapi 0.8.11, ytmusicapi 1.12.3.
