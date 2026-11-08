# PlaylistTransfer

App de escritorio (Windows) para pasar playlists entre servicios de música: hoy Tidal ⇄ YouTube Music; después Spotify.
Proyecto open source pensado para GitHub. El plan completo, con decisiones y fases, está en `SPEC.md`: leelo antes de cambiar algo grande.

## Estado actual
- F0 aprobada (`prototype/index.html` es la referencia visual). **F1 hecha**: motor en `app/core/`, conectores en `app/connectors/`, login en `app/auth/`. **F2 terminada** (probada por el usuario con cuentas reales el 2026-11-08): ventana con pywebview (`app/main.py`, `app/api.py`, `app/jobs.py`, `app/core/transfer.py`, `app/ui/`). **F3 terminada** (`app/storage/export.py`, umbrales en `app/storage/prefs.py`). **F4 terminada**: repo **privado** On33-3/PlaylistTransfer, Releases v0.1.0 y v0.2.0. **F5 (Spotify) en curso**: conector, login y configuración hechos; falta la prueba del usuario con Premium (plan en `docs/plan-f5-spotify.md`). Publicar versiones, hacer público el repo o subir a VirusTotal: solo con el OK explícito del usuario. El historial se descarga como `.txt` liviano (Historial › Descargar historial), nunca solo. Los "?" nunca se agregan solos: se deciden al final.
- Idiomas: **ningún texto visible va escrito en el código**. La UI usa `t("clave")` / `data-i18n`, Python usa `tr("clave")` (`from app.i18n import t as tr`: `t` suele ser variable de bucle). Las claves viven en `app/locales/es.json` y `en.json`, con las mismas claves en los dos (`tests/test_i18n.py`). Los tests corren en castellano (fixture `spanish_by_default`).
- Diseño: usá las variables de `:root` (`--h-lg` 42, `--h-md` 36, `--h-sm` 28, `--r-field`, `--gap-actions`) y las clases `.btn` / `.btn.md`; nada de `style="..."` en el HTML. `tests/test_ui_design.py` mide todos los controles y `tests/test_ui_stress.py` prueba datos extremos en la ventana mínima.
- Simplicidad (pedido del usuario): no sumar casillas ni opciones a Transferir; las decisiones van al final, sobre el resultado.
- El script de consola original (`legacy/`) **se borró** el 2026-11-08 con el OK del usuario. Lo que decidía quedó en `tests/data/legacy_parity.json` (paridad de repetidos) y en la columna `puntaje` de los reportes (`tools/check_reports.py`). Los archivos de sesión viejos en la raíz (`tidal_session.json`, `browser.json`, `.yt-login/`) ya no los usa nada; la app guarda los suyos en `%LOCALAPPDATA%\PlaylistTransfer\`.
- UI: sin frameworks ni recursos externos (sin Google Fonts: funciona sin internet y no avisa a nadie). pywebview publica a la página todo atributo público de `Api`: el estado va siempre con `_` (hay un test).
- `app/core/matching.py` parte de la lógica del script, con reglas nuevas (SPEC, "Reglas del matching"). Cada regla tiene su escenario en `tests/test_scenarios.py`: si encontrás un caso nuevo, agregalo ahí primero. Al cambiar el matching, corré también `py tools/check_reports.py` (compara contra tus reportes reales, locales): ninguna coincidencia real debería empeorar.
- Una misma canción puede tenerla varios artistas: nunca se reemplaza la versión de un artista por la de otro sin preguntar (`alternatives()`).
- Primer uso: nada viene con el repo ni con la app; historial, sesiones y preferencias se crean en `%LOCALAPPDATA%\PlaylistTransfer\` recién cuando hacen falta (SPEC, "Primer uso"). Los datos de ejemplo (prototipo, tests) son inventados o canciones conocidas, nunca el historial del usuario.
- Los conectores no imprimen ni llaman a `sys.exit`: lanzan `ConnectorError` (mensajes para el usuario). `remove()` exige `confirmed=True`. `connect(browser, cancel)` corta con `Cancelled` si se cancela.
- Servicios: **un solo lugar**, `app/connectors/registry.py`. La UI no tiene lista propia: los pide con `api.services()` (hay un test).
- Entorno del usuario: Windows 11, Python 3.14 (`py`), PowerShell. Navegadores: Vivaldi (predeterminado), Brave, Chrome (del trabajo: **no usarlo al probar**) y Edge. Eso es solo de su PC: la app propone el predeterminado de Windows de cada persona, sin excluir ninguno (hay test).

## Reglas sin excepción
- **Nunca** commitear credenciales ni datos personales: `browser.json`, `oauth.json`, `tidal_session.json`, `spotify_session.json`, `spotify_app.json`, `.yt-login/`, `preferencias.json`, `reporte_*.csv`, `.env`. Verificá `.gitignore` antes de cada commit.
- **Nunca** quitar temas de una playlist sin una confirmación explícita del usuario en la UI.
- Antes de decir que algo funciona, ejecutalo (tests o el comando real) y mostrá la salida.

## Comandos
- Dependencias de desarrollo: `py -m pip install -r requirements-dev.txt`
- Tests: `py -m pytest -q` (incluye la UI en Edge headless; capturas con `PT_SCREENSHOTS=<carpeta>`)
- Paquete: `py -m pip install -r requirements-build.txt` y `py packaging/build.py` → `dist/PlaylistTransfer-<versión>-windows.zip`. Probar el `.exe`: `PT_SMOKE=1` y código de salida 0. Capturas del README: `py tools/readme_screenshots.py` (datos falsos).
- App: `py -m app` (o doble clic en `Abrir PlaylistTransfer.bat`). `PT_DEBUG=1` abre las herramientas de desarrollo; `PT_SMOKE=1` abre, espera la UI y cierra. Log en `%LOCALAPPDATA%\PlaylistTransfer\app.log`.
- Matching contra tus reportes reales: `py tools/check_reports.py`

## Cosas que no se deducen leyendo el código (aprendidas a los golpes)
- **Sin shebang** en los `.py`: el launcher `py` de Windows lo respeta y abre otro Python, que no tiene las librerías instaladas.
- **ytmusicapi** solo reconoce una sesión de navegador si los headers incluyen `authorization: SAPISIDHASH …`. Sin ese header la toma como OAuth y falla con "oauth_credentials not provided". Ver `sapisid_hash()` y `build_browser_json()`.
- **Vivaldi no deja que Playwright cree pestañas** ("Target.createTarget: Failed to open a new tab"). Solución en uso: lanzar el navegador elegido como proceso común con `--remote-debugging-port` y `--user-data-dir` propio, abierto en la URL de login, y leer las cookies con `Storage.getCookies` mediante un cliente CDP mínimo (`class CDP`). No manipular pestañas.
- Chrome y sus derivados no permiten automatizar el perfil normal del usuario: siempre se usa un perfil aparte en `.yt-login/<navegador>`.
- Detección de navegadores: registro de Windows, en `StartMenuInternet` (instalados) y `UrlAssociations\https\UserChoice` o `UserChoiceLatest` (predeterminado).
- `tidalapi.login_oauth_simple(fn)` llama a `fn` con un texto que contiene el link; se abre con el navegador elegido.
- YouTube Music: para quitar temas hace falta el `setVideoId` de cada aparición, no solo el `videoId`.
- Versiones probadas: tidalapi 0.8.11, ytmusicapi 1.12.3.
- **Tidal login:** no usar `login_oauth_simple` (deja un hilo de `ThreadPoolExecutor` esperando hasta 5 min aunque se cancele). Se usa `get_link_login()` + `process_link_login(link, until_expiry=False)` en un bucle que mira el aviso de cancelar.
- **Spotify (2026):** el dueño de la app necesita Premium (comprobado el 2026-11-08 con una cuenta Free: el panel deja entrar y crear apps, pero el casillero "Web API" sale deshabilitado) y hay máximo 5 usuarios por app → cada usuario crea la suya. Prohibido `localhost`: `http://127.0.0.1`. Endpoints `/items` (no `/tracks`), `POST /me/playlists`, campos `items`/`item` en vez de `tracks`/`track`. Búsqueda: máximo 10. Solo se leen playlists propias o colaborativas. Quitar es por URI y saca **todas** las copias. El refresh token vence a los 6 meses (`invalid_grant`). `external_ids` (ISRC) volvió en marzo de 2026.
