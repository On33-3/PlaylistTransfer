# PlaylistTransfer

App de escritorio (Windows) para pasar playlists entre servicios de música: hoy Tidal ⇄ YouTube Music; después Spotify.
Proyecto open source pensado para GitHub. El plan completo, con decisiones y fases, está en `SPEC.md`: leelo antes de cambiar algo grande.

## Estado actual
- F0 aprobada (`prototype/index.html` es la referencia visual). **F1 hecha**: motor en `app/core/`, conectores en `app/connectors/`, login en `app/auth/`. **F2 terminada** (probada por el usuario con cuentas reales el 2026-11-08): ventana con pywebview (`app/main.py`, `app/api.py`, `app/jobs.py`, `app/core/transfer.py`, `app/ui/`). **F3 terminada** (`app/storage/export.py`, umbrales en `app/storage/prefs.py`). **F4 terminada**: repo **privado** On33-3/PlaylistTransfer, Release v0.1.0. Publicar versiones, hacer público el repo o subir a VirusTotal: solo con el OK explícito del usuario. Los reportes CSV nunca se generan solos: solo a pedido (casilla en Transferir o botón en Historial).
- CSV para Excel: no cambies el formato de la fecha (`2026-11-08 · 01:00`) ni el separador por región sin volver a probar en Excel (SPEC, "Formato del CSV").
- `legacy/transfer_playlist.py` es la **versión de consola que ya funciona** (probada con cuentas reales, también en dry-run el 2026-11-08) y la referencia del motor. Ya no hace falta para usar la app; se conserva como referencia hasta que el usuario decida borrarla. Se corre desde la raíz, así sigue usando las sesiones guardadas ahí. La app nueva **no** usa esas sesiones: guarda las suyas en `%LOCALAPPDATA%\PlaylistTransfer\`.
- UI: sin frameworks ni recursos externos (sin Google Fonts: funciona sin internet y no avisa a nadie). pywebview publica a la página todo atributo público de `Api`: el estado va siempre con `_` (hay un test).
- `app/core/matching.py` parte de la lógica del script, con reglas nuevas (SPEC, "Reglas del matching"). Cada regla tiene su escenario en `tests/test_scenarios.py`: si encontrás un caso nuevo, agregalo ahí primero. Al cambiar el matching, corré también `py tools/check_reports.py` (compara contra tus reportes reales, locales): ninguna coincidencia real debería empeorar.
- Una misma canción puede tenerla varios artistas: nunca se reemplaza la versión de un artista por la de otro sin preguntar (`alternatives()`).
- Primer uso: nada viene con el repo ni con la app; historial, sesiones y preferencias se crean en `%LOCALAPPDATA%\PlaylistTransfer\` recién cuando hacen falta (SPEC, "Primer uso"). Los datos de ejemplo (prototipo, tests) son inventados o canciones conocidas, nunca el historial del usuario.
- Los conectores no imprimen ni llaman a `sys.exit`: lanzan `ConnectorError` (mensajes para el usuario). `remove()` exige `confirmed=True`.
- Entorno del usuario: Windows 11, Python 3.14 (`py`), PowerShell. Navegadores: Vivaldi (predeterminado), Brave, Chrome (del trabajo: **no usarlo por defecto**) y Edge.

## Reglas sin excepción
- **Nunca** commitear credenciales ni datos personales: `browser.json`, `oauth.json`, `tidal_session.json`, `.yt-login/`, `preferencias.json`, `reporte_*.csv`, `.env`. Verificá `.gitignore` antes de cada commit.
- **Nunca** quitar temas de una playlist sin una confirmación explícita del usuario en la UI.
- Antes de decir que algo funciona, ejecutalo (tests o el comando real) y mostrá la salida.

## Comandos
- Dependencias de desarrollo: `py -m pip install -r requirements-dev.txt`
- Tests: `py -m pytest -q` (incluye la UI en Edge headless; capturas con `PT_SCREENSHOTS=<carpeta>`)
- Paquete: `py -m pip install -r requirements-build.txt` y `py packaging/build.py` → `dist/PlaylistTransfer-<versión>-windows.zip`. Probar el `.exe`: `PT_SMOKE=1` y código de salida 0. Capturas del README: `py tools/readme_screenshots.py` (datos falsos).
- App: `py -m app` (o doble clic en `Abrir PlaylistTransfer.bat`). `PT_DEBUG=1` abre las herramientas de desarrollo; `PT_SMOKE=1` abre, espera la UI y cierra. Log en `%LOCALAPPDATA%\PlaylistTransfer\app.log`.
- Matching contra tus reportes reales: `py tools/check_reports.py`
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
