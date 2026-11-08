# SPEC — PlaylistTransfer (app de escritorio)

Estado: **F0, F1 y F2 terminadas** (F2 probada por el usuario el 2026-11-08: dos transferencias reales de Tidal a YouTube Music y dos limpiezas de repetidos desde la ventana, sin errores en el log). **F3 terminada** (el usuario probó "Exportar a CSV" desde la ventana; el CSV con su historial real se verificó en su Excel 16). **F4 terminada** (2026-11-08): repo privado On33-3/PlaylistTransfer, Release v0.1.0 compilada por GitHub Actions; el zip descargado del Release corre en la PC del usuario sin Python en el PATH. Pendiente: el usuario sube el zip a VirusTotal (no hay clave de API). El repo es privado por decisión del usuario: hacerlo público es un paso aparte. **Antes de F5/F6, mejoras de la app en 3 etapas** (pedido del usuario, de a poco y sin romper): 1) idiomas castellano/inglés — hecha; 2) que los cuadrados de colores informen — hecha como primer acercamiento: leyenda en Inicio e Historial, cada cuadrado dice qué tema es y su estado al pasar el mouse, "+N" si hay más de 60, y en el mosaico grande un clic lleva a ese tema en la lista (si no alcanza, el plan B son contadores con texto); 3) armonizar las pantallas — hecha: un sistema de tamaños (botones de 42 / 36 / 28 px, campos de 36 y 42 en filas de acciones, un radio para campos, misma separación de las filas de botones y mismo estilo de cajas), sin estilos sueltos en el HTML; `tests/test_ui_design.py` falla si un control se sale del sistema. Cualquier decisión que cambie el alcance, el costo o el riesgo se consulta con el usuario; las decisiones menores se anotan acá en "Decisiones tomadas". **v0.2.0 publicada** (2026-11-08) con esas mejoras. **F5 en curso** (plan e investigación en `docs/plan-f5-spotify.md`): refactor previo hecho (registro de servicios en `app/connectors/registry.py`, conectar cancelable), conector de Spotify con PKCE y la configuración en Ajustes hechos y probados contra una API falsa; falta la prueba del usuario con una cuenta Premium real (criterio de terminado). `legacy/` se borró con el OK del usuario: lo que decidía quedó congelado en `tests/data/legacy_parity.json` y en la columna `puntaje` de los reportes que usa `tools/check_reports.py`.

## 1. Objetivo
Una mini app de escritorio para Windows, **minimalista y linda (colores pastel)**, que:
1. Transfiera playlists de Tidal a YouTube Music y viceversa, con una arquitectura de **conectores** que permita sumar Spotify y otros servicios sin tocar el resto.
2. Tenga un menú claro y simple, y **muestre en todo momento qué está pasando**: progreso y resultado de cada tema en vivo.
3. Guarde un **historial** de lo transferido.
4. Muestre el **estado de conexión** de cada servicio: `Tidal ✓`, `YouTube Music ✓`, `Spotify —`.
5. Se pueda descargar desde **GitHub Releases** como `.exe`, compilado automáticamente con GitHub Actions. Más adelante, firmado con SignPath Foundation (gratis para open source).

## 2. Decisiones tomadas
| Tema | Decisión | Motivo |
|---|---|---|
| UI | **pywebview**: ventana nativa con la UI en HTML, CSS y JS | Usa el WebView2 (Edge) que ya trae Windows. Permite el diseño pastel con CSS. Python queda de backend |
| Motor | Reutilizar la lógica de `transfer_playlist.py` (matching, `same_song`, `find_duplicates`, logins) | Ya está probada con cuentas reales |
| Login de YouTube Music | Navegador elegido por el usuario, lanzado como proceso con depuración remota y cookies leídas por CDP | Es lo único que funcionó con Vivaldi. No depende de Playwright |
| Playwright | **Quitar** como dependencia (opcional, solo como respaldo si hiciera falta) | Achica el `.exe` y menos piezas que puedan fallar |
| Datos del usuario | `%LOCALAPPDATA%\PlaylistTransfer\` (sesiones, preferencias, historial, perfiles de navegador) | Un `.exe` no debe escribir en su propia carpeta. Además separa los datos del repo |
| Historial | SQLite (stdlib) | Sin dependencias; fácil de consultar y filtrar |
| Licencia | MIT (verificar compatibilidad: tidalapi LGPL-3.0, ytmusicapi MIT, pywebview BSD) | Requisito para SignPath: todo OSI |
| Empaquetado | PyInstaller en modo **onedir** (carpeta en un zip), sin UPX y con datos de versión. Evaluar Nuitka si los antivirus lo marcan | El modo onefile y UPX disparan más falsos positivos |
| Distribución | Repo público en GitHub, con build en GitHub Actions (`windows-latest`) y publicación en Releases | Gratis; build verificable, que SignPath necesita |
| Dependencias (F1) | `requirements.txt` con versiones fijas (tidalapi 0.8.11, ytmusicapi 1.12.3); `requirements-dev.txt` suma pytest; `legacy/requirements.txt` conserva el original (con playwright) para el script de consola | Ver riesgo de librerías no oficiales |
| Conectores (F1) | `add(id, songs)` recibe `Song`; `remove(id, items, confirmed=True)` recibe `Duplicate` (posición + tema) y se niega sin confirmación; errores como `ConnectorError` en vez de `sys.exit` | Defensa extra para la regla de no quitar temas sin confirmar; la UI puede mostrar los errores |
| Cambios de comportamiento (F1) | YouTube Music: si un `add` no da `STATUS_SUCCEEDED`, el conector lanza error (el script solo avisaba). `remove` se niega si a algún tema le falta `setVideoId` (el script lo salteaba en silencio) | Que nunca parezca hecho algo que no se hizo |
| OAuth de YouTube (F1) | La app no usa `oauth.json` (sí el script de consola) | El login por navegador es el que funciona; menos caminos que mantener |
| Firefox (F1) | Lectura de cookies en `auth/firefox.py` | No es CDP; se separa de `cdp_login.py` |
| Reglas del matching (F1) | Sobre la suma ponderada del script: **otra canción** → tope 0.50, no encontrado (otro artista, título que no se parece, cover/karaoke/tributo, otra parte o secuela, reprise, "Intro"/"Interlude" de otro álbum). **Otra versión** de la misma canción (en vivo, acústica, remix, sped up, slowed, instrumental, demo, Taylor's Version) → tope 0.70, dudoso. **Varios artistas**: alcanza con que coincida uno, aunque un servicio los escriba en un solo texto ("Queen & David Bowie", "x", "feat.", "y"). Catálogo completo en `tests/test_scenarios.py` | Con el script, un cover que nombraba al original, un karaoke, "One" vs "One Tree Hill", Pt. 1 vs Pt. 2 y "Sunshine" vs "Sunkissed" daban OK; "Intro" de dos álbumes y el "(Reprise)" se tomaban como repetidos. Con el motor viejo fallan 37 de 71 escenarios. Verificado con `py tools/check_reports.py`: ninguna de las 60 coincidencias reales cambia de categoría |
| La misma canción por otro artista (F1) | Nunca se agrega sola. Si no está la versión del artista buscado, `alternatives()` devuelve las de otros artistas (sin covers caseros ni karaoke) y la UI las ofrece | Una misma canción puede ser cantada por varios artistas: cuál querés lo decide el usuario |
| Historial (F1) | `storage/history.py`: guarda origen, destino, cantidades y cada tema **con su artista**. Solo operaciones reales: las pruebas sin cambios (dry-run) no se registran | El historial es de lo que pasó de verdad |
| Historial descargable (mejoras) | **Reemplaza al CSV para Excel.** Historial › "Descargar historial" guarda un `.txt` (UTF-8, títulos y listas estilo Markdown): una persona lo lee con doble clic en el Bloc de notas y una IA lo entiende sin preparación. Pesa poco (5 operaciones reales: 5,3 KB). Respeta el filtro (Todo / Transferencias / Repetidos) y el idioma. Siempre a pedido, con "Guardar como". Ya no hay reporte por transferencia | Pedido del usuario: simplicidad. El CSV obligaba a resolver separadores por región, fechas `####` y fórmulas, y la casilla agregaba ruido a Transferir; lo mismo ya está en el Historial |
| Umbrales (F3) | Editables en Ajustes. "Sin dudar" entre 0.71 y 0.99 y "para revisar" desde 0.51, al menos 0.05 por debajo. Si el archivo tiene valores fuera de rango, se usan los de fábrica | Por debajo de esos límites, las otras versiones (tope 0.70) pasarían como OK o las canciones de otro artista (tope 0.50) como dudosas |
| tidalapi y la LGPL (F4) | En el paquete, tidalapi va como archivos `.py` sueltos en `_internal\tidalapi` (`module_collection_mode` en el `.spec`), no dentro del ejecutable. `build.py` corta si no quedó así | La LGPL-3.0 pide que el usuario pueda reemplazar la librería. La app sigue siendo MIT |
| Paquete (F4) | `packaging/build.py` hace todo (ícono, datos de versión, PyInstaller, licencias de terceros, LÉEME, zip, SHA-256) y es lo mismo en la PC y en GitHub Actions. Corta si encuentra archivos personales. Verificado: 723 módulos dentro del `.exe` sin rutas de la PC que compiló | Un solo camino de build; nada de la carpeta de datos del usuario entra al paquete |
| GitHub Actions (F4) | Tests en cada push. Con un tag `vX.Y.Z`: verifica que coincida con `app/__init__.py`, compila en `windows-latest`, abre el `.exe` sin Python (`PT_SMOKE=1`) y publica con `gh release`. Solo acciones de GitHub (`actions/*`), permisos de solo lectura salvo el paso de publicar | Menos piezas de terceros que puedan cambiar; el build es verificable (lo pide SignPath) |
| Nombres de los estados (mejoras) | Dicen lo que pasó: ✓ **Agregado**, ? **Agregado, puede ser otra versión**, ≈ **Ya estaba**, ✗ **No encontrado**, − **Quitado (repetido)**. En una prueba sin cambios: "Se agregaría". En el resumen, los dudosos van dentro de los agregados: "2 agregados (1 puede ser otra versión)". No se usa "Duplicado" para ≈ porque en la app significa lo que se quita en Quitar repetidos | Antes decía "Para revisar", pero no había nada que revisar: los ? ya se agregaban. Paso 2 hecho, y simplificado a pedido del usuario: **los ? nunca se agregan solos** (sin casilla). Quedan como "No agregado, puede ser otra versión" con "+ Agregar igual" por tema y "Agregar los N…"; antes de agregar se vuelve a leer el destino para no duplicar. El historial guarda cuántos quedaron sin agregar (columna `skipped`, con migración para historiales anteriores) |
| Números en el título (estrés) | Si los dos títulos tienen números (sin contar años 1900-2099 ni lo que va entre paréntesis) y no coinciden, son obras distintas; lo mismo con los movimientos ("I.", "II.") | La prueba de estrés mostró que "Gymnopédie No. 1" y "No. 3" se tomaban como repetidos (se habría quitado una) y "Sonata No. 14" se agregaba por "No. 8". Límite conocido: "Song 2" vs "Song" |
| Datos extremos (estrés) | `tests/test_ui_stress.py`: 300 playlists, nombres larguísimos, sin espacios, japonés y emojis, 241 temas, ventana mínima y normal; falla si algo se sale de la pantalla. Listas de Transferir con la misma altura, nombres cortados con "…" (completos al pasar el mouse), buscador en las dos si alguna tiene más de 8, "Crear una playlist nueva" fija arriba; mosaico más denso con más de 90 y de 300 temas | Pedido del usuario: que el diseño no se rompa por exceso |
| Idiomas (mejoras 1) | Castellano e inglés, elegibles en Ajustes. Todos los textos en `app/locales/<idioma>.json` ("ui" para la ventana, "py" para errores, CSV y nombres de archivo); ni la UI ni Python tienen texto suelto (hay tests). Sin elegir: castellano para quien ya usaba la app, el idioma de Windows para una instalación nueva. Cambiar de idioma recarga la ventana y no se permite con una tarea en curso | Pedido del usuario: evitar el spanglish. Un solo lugar por idioma; un test exige las mismas claves en los dos |
| Spotify | Conector con OAuth PKCE. El usuario carga su propio Client ID (app gratuita en el dashboard de Spotify) | Restricciones 2026: el dueño de la app necesita Premium, hasta 5 usuarios por app, búsqueda de máximo 10 resultados |
| Spotify: cómo (F5) | Cliente propio con `requests` (sin spotipy); login PKCE con un servidor en `127.0.0.1` y puerto libre (Redirect URI registrada sin puerto: `http://127.0.0.1/callback`). Client ID en `spotify_app.json` (queda al desconectar), tokens en `spotify_session.json`. Estado nuevo `setup` ("sin configurar"), que Inicio no reclama: Spotify es opcional | Lo exige Spotify (sin `localhost`, Premium); menos dependencias; probado con una API falsa que sigue las reglas de 2026 |
| Spotify: quitar repetidos (F5) | Spotify quita por URI y saca todas las copias. Para una copia exacta: se quitan todas y se vuelve a agregar la que queda en su posición; después se relee y, si falta algo, se agrega al final y se avisa. El diálogo de confirmación lo avisa antes ("va a figurar como agregada hoy") | Aprobado por el usuario el 2026-11-08 |

## 3. Arquitectura
```
PlaylistTransfer/
├── app/
│   ├── main.py                 # arranca pywebview y expone la API a JS
│   ├── api.py                  # puente JS ⇄ Python (métodos que llama la UI)
│   ├── jobs.py                 # tareas largas en segundo plano + cola de eventos de progreso
│   ├── core/
│   │   ├── models.py           # Song, PlaylistInfo, TransferResult
│   │   ├── matching.py         # norm, score, best_match, same_song, find_duplicates
│   │   └── transfer.py         # orquesta: leer origen → buscar → deduplicar → agregar
│   ├── connectors/
│   │   ├── base.py             # interfaz Connector (ver abajo)
│   │   ├── tidal.py
│   │   ├── ytmusic.py
│   │   └── spotify.py          # fase F5
│   ├── auth/
│   │   ├── browsers.py         # detectar navegadores instalados y el predeterminado
│   │   └── cdp_login.py        # cliente CDP mínimo y login con el navegador elegido
│   ├── storage/
│   │   ├── paths.py            # %LOCALAPPDATA%\PlaylistTransfer
│   │   ├── prefs.py
│   │   └── history.py          # SQLite
│   └── ui/                     # index.html, styles.css, app.js (sin frameworks pesados)
├── tests/                      # pytest: matching, transfer con conectores falsos, history, browsers
├── legacy/transfer_playlist.py # script de consola original (referencia)
├── .github/workflows/build.yml
├── LICENSE, README.md, SPEC.md, CLAUDE.md
```

**Interfaz `Connector`** (todos los servicios la cumplen):
`name`, `status() -> connected | expired | disconnected`, `connect(browser)`, `disconnect()`, `list_playlists()`, `read(playlist_id)`, `search(song)`, `create(name, desc)`, `add(playlist_id, songs)`, `remove(playlist_id, items)`, `url(playlist_id)`.

**Progreso en vivo:** las tareas corren en un hilo (`app/jobs.py`, una por vez). Cada evento (`source`, `searching`, `track`, `already`, `target`, `login`) va a una cola, y la UI la lee cada ~200 ms con `api.poll(job, desde)`. Se eligió leer la cola en vez de `window.evaluate_js` porque no depende del hilo de la ventana y se puede probar sin ventana. La ventana nunca se congela. Nada se modifica hasta terminar todas las búsquedas: cancelar en ese momento no deja playlists a medias.

**Pruebas de la UI:** `tests/test_ui.py` abre `app/ui` en Edge (el mismo motor que WebView2) con Playwright, conectada a la API real con servicios falsos. `PT_SCREENSHOTS=<carpeta>` guarda capturas. `PT_SMOKE=1 py -m app` abre la ventana real, espera a que la UI avise que cargó y la cierra.

## 4. Pantallas (UI)
1. **Inicio:** fichas de estado por servicio (`Tidal ✓ conectado`, `YouTube Music ⚠ venció`, `Spotify — sin conectar`), cada una con su botón Conectar o Reconectar. Debajo, tres acciones grandes: **Transferir**, **Quitar repetidos**, **Historial**.
2. **Transferir:** de qué servicio a cuál, la playlist de origen y los destinos (una o varias con casillas, o una nueva con su nombre). Botón *Probar sin cambiar nada* (dry-run) y botón *Transferir*. Durante la transferencia: barra de progreso y una lista en vivo con ✓, ?, ✗ y ≈ (otra versión ya estaba). Al final, un resumen y un link a la playlist. En los ✗ que tengan la canción por otros artistas: "No está la versión de Jeff Buckley. ¿Agregar la de Leonard Cohen?", con una casilla destildada.
3. **Quitar repetidos:** servicio, playlist, lista de repetidos con "queda / se quita". Casillas tildadas por defecto para la misma grabación y **destildadas para otra versión** (en vivo, remix...: `Duplicate.kind`). Confirmación explícita antes de quitar.
4. **Historial:** lista por fecha (origen → destino, cantidades, mosaico de colores) con el detalle de cada tema, filtro (Todo / Transferencias / Repetidos) y "Descargar historial" (un .txt liviano).
5. **Ajustes:** navegador para iniciar sesión, desconectar servicios, abrir la carpeta de datos, umbrales de coincidencia (avanzado) y "Acerca de" (versión y licencias).

**Estilo:** minimalista, mucho espacio en blanco, bordes redondeados, sombras suaves, tipografía del sistema (Segoe UI Variable) y animaciones sutiles.

Paleta pastel (revisada en F0: el crema se cambió por lila neblina para no caer en el estilo genérico):
| Uso | Color |
|---|---|
| Fondo | `#F3F1FA` (lila neblina) |
| Superficie / tarjetas | `#FFFFFF` |
| Primario (botones) | `#CDB4DB` (lavanda) |
| Acento | `#BDE0FE` (celeste) |
| OK ✓ | `#B9FBC0` (menta) |
| Dudoso ? | `#FFD6A5` (durazno) |
| No encontrado ✗ | `#FFADAD` (rosa) |
| Otra versión ≈ | `#E2ECE9` (gris verdoso) |
| Texto | `#3D3A4B` |

### Primer uso y datos de cada persona
Quien descarga el repo o el `.exe` arranca de cero: **ni el repo ni la app traen el historial, las sesiones ni las preferencias de nadie**. Todo se crea en `%LOCALAPPDATA%\PlaylistTransfer\` de esa persona, recién cuando hace falta. Los datos de ejemplo del prototipo son inventados.

| Escenario | Qué pasa | Test |
|---|---|---|
| Primera vez que se abre | Inicio: los tres servicios "sin conectar". Historial: estado vacío ("Todavía no hiciste transferencias. Cuando hagas la primera, aparece acá") con un botón a Transferir. Abrir no crea historial, sesiones ni preferencias (solo `app.log` y la caché de la ventana, `webview/`) | `test_first_run.py` |
| Primer login | Se pregunta el navegador (se propone el predeterminado de Windows, sea cual sea; si el elegido se desinstaló, se vuelve a proponer) y se guarda en preferencias | `test_auth.py`, `test_first_run.py` |
| Primera transferencia real | Se crea `historial.sqlite` y se guarda la operación con cada tema y su artista. Un dry-run no crea nada | `test_first_run.py` |
| Preferencias dañadas | Se usan los valores por defecto; el archivo no se pisa | `test_first_run.py` |
| Historial dañado | Se aparta como `historial.dañado-<fecha>.sqlite` (nunca se borra) y se empieza uno nuevo; la UI avisa | `test_first_run.py` |
| Sesión vencida | El servicio aparece "⚠ venció" con el botón Reconectar | `test_connectors.py` |
| El repo | `.gitignore` excluye credenciales, reportes y bases de datos; un test falla si alguno llega a estar versionado. El `.exe` (F4) no empaqueta nada de la carpeta de datos | `test_first_run.py` |

## 5. Fases y definición de terminado
Cada fase cierra con evidencia: comandos ejecutados con su salida y capturas de la UI cuando corresponda.

- **F0 · Prototipo visual.** HTML estático con las 5 pantallas y datos de ejemplo, que se abre en el navegador.
  *Terminado cuando:* el usuario aprueba el diseño y la paleta.
- **F1 · Base del proyecto.** `git init`, estructura de carpetas, `.gitignore` revisado, `legacy/`, extracción de `core/matching.py` y de los conectores desde el script, y tests con pytest. Los tests incluyen los casos reales del usuario: los repetidos del video (Virtual Insanity ×3, Stuck In The Middle, Come and Get Your Love, Mr. Blue Sky, Take On Me, Satisfaction (Mono)) y los negativos (Spirit In The Sky ≠ Marcy, Laufey ≠ Good Kid "From The Start", Sunshine ≠ Sunkissed).
  *Terminado cuando:* `py -m pytest -q` pasa y `py legacy/transfer_playlist.py` sigue funcionando.
- **F2 · App con ventana.** Inicio con estados, Transferir con progreso en vivo y Quitar repetidos con confirmación, todo con los conectores de Tidal y YouTube Music. Datos en `%LOCALAPPDATA%`.
  *Terminado cuando:* el usuario hace una transferencia real y una limpieza real desde la ventana, sin usar la consola.
- **F3 · Historial y Ajustes.**
  *Terminado cuando:* después de dos transferencias, el historial muestra ambas con su detalle y la exportación a CSV abre bien en Excel. *(Cumplido el 2026-11-08; después, a pedido del usuario, el CSV se reemplazó por un .txt liviano.)*
- **F4 · Empaquetado y GitHub.** LICENSE, README para usuarios con capturas, workflow de GitHub Actions que compila el `.exe` (onedir y zip) y lo publica en Releases. Revisión del zip en VirusTotal.
  *Terminado cuando:* el zip descargado del Release corre en la PC del usuario sin Python instalado en el PATH.
- **F5 · Conector de Spotify.** PKCE, Client ID cargado por el usuario y las mismas pantallas, sin cambios en `core/`.
  *Terminado cuando:* funciona una transferencia de Tidal a Spotify con una playlist real.
- **F6 · (Opcional) Firma con SignPath Foundation.** Política de firma en el README, MFA y aprobación manual de releases.
- **F7 · Donaciones (paso final).** `.github/FUNDING.yml` con Ko-fi (PayPal, para el exterior) y Cafecito (Mercado Pago, para Argentina), más una sección discreta "Apoyá el proyecto" en el README y un link en Acerca de. GitHub Sponsors solo si hay una cuenta en un banco argentino a nombre del usuario (GitHub exige que la residencia y el banco coincidan; la cuenta de EE. UU. de ARQ no sirve). Los aportes grandes por transferencia (ARQ) se coordinan por privado: nunca publicar datos bancarios. Antes de activar, consultar con la contadora cómo declarar las donaciones.

## 6. Riesgos y mitigación
| Riesgo | Mitigación |
|---|---|
| Las librerías no oficiales (tidalapi, ytmusicapi) se rompen si el servicio cambia algo | Fijar versiones en `requirements.txt`; tests con conectores falsos; mensajes de error claros con la sugerencia de actualizar |
| Antivirus o SmartScreen marcan el `.exe` | Modo onedir sin UPX, datos de versión, envío a Microsoft para análisis y, más adelante, SignPath |
| Google bloquea el login en una ventana automatizada | El navegador se lanza como proceso común (CDP), sin flags de automatización. Queda como respaldo el método manual con headers |
| Se quitan temas por error al limpiar repetidos | Confirmación explícita, se conserva el primero y el historial registra lo quitado |
| Filtrar credenciales en el repo | `.gitignore` y datos en `%LOCALAPPDATA%`, fuera del repo. Revisar `git status` antes de cada commit |
