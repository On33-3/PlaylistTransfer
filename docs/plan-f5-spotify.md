# F5 · Spotify: investigación y plan

Fecha: 2026-11-08. Estado: **investigación, sin cambios en el código**. Para ejecutar en la próxima sesión.

## Resumen

- **Es viable**, pero Spotify cambió mucho en 2026 y eso condiciona el diseño. Lo más importante:
  1. **Premium obligatorio** para el dueño de la app de desarrollador, y **máximo 5 usuarios** por app (modo desarrollo). El modo sin límites solo se da a empresas con 250 mil usuarios activos por mes. Para PlaylistTransfer eso significa que **cada persona crea su propia app gratuita en Spotify, con su cuenta Premium**, y carga su Client ID (lo que ya decía la SPEC).
  2. **Quitar un tema por posición ya no existe**: Spotify quita *todas* las copias de una canción. "Quitar repetidos" en Spotify necesita una estrategia propia (abajo).
  3. Los tokens vencen a los **6 meses** sí o sí: hay que reconectar (la app ya tiene el estado "⚠ venció").
- **Recomendación técnica:** hablar con la API directamente con `requests` (ya viene con la app) en vez de usar `spotipy`. Son ~9 llamadas y así seguimos los cambios de 2026 sin depender de una librería que va atrás.
- **Refactor previo (sin cambiar el comportamiento):** hoy la lista de servicios está escrita a mano en Python y en JavaScript, y "conectar" asume dos formas de login. Antes de sumar Spotify conviene un **registro de servicios** único del que salga todo lo demás.
- **Esfuerzo estimado:** 4 etapas de trabajo (≈ 3–4 sesiones como las de hoy) + tu prueba con una cuenta real.
- **Bloqueante a confirmar:** ¿tenés **Spotify Premium**? Sin Premium no se puede probar con una cuenta real (es el criterio de terminado de la F5).

## 1. Qué cambió en Spotify (verificado en la documentación oficial)

| Tema | Situación en noviembre de 2026 | Impacto |
|---|---|---|
| Modo desarrollo | Hasta **5 usuarios**, cada uno agregado a la lista de la app; el dueño necesita **Premium** activo; si vence, la app deja de andar | Cada usuario crea su app (Client ID propio). La UI tiene que explicarlo antes de que empiece |
| Modo extendido | Solo organizaciones, 250k usuarios activos/mes, revisión de hasta 6 semanas | No aplica a un proyecto open source chico |
| Redirect URI | Prohibido `localhost`; solo `http://127.0.0.1` (o `[::1]`) para apps locales; el puerto se puede elegir al autorizar si se registra la URI sin puerto | Servidor local mínimo en `127.0.0.1` para recibir el código |
| Playlists | `/tracks` pasó a `/items` (leer, agregar, quitar, reordenar). `POST /me/playlists` para crear. **Solo se leen los temas de playlists propias o colaborativas** (403 si no) | Origen: solo playlists propias/colaborativas (igual que Tidal hoy) |
| Leer temas | `GET /playlists/{id}/items`, hasta 50 por pedido, incluye `duration_ms`, artistas e **ISRC** (`external_ids`, que se iba a quitar y se repuso en marzo) | Coincidencias exactas por ISRC con Tidal |
| Agregar | `POST /playlists/{id}/items`, hasta 100 por pedido, con `position` opcional | Sin problema |
| Quitar | `DELETE /playlists/{id}/items` con `items: [{uri}]`; **sin posiciones** | Quita todas las copias de esa URI (ver §4) |
| Buscar | Máximo **10** resultados por búsqueda (antes 50); filtros `isrc:`, `track:`, `artist:` | Nos alcanza: hoy pedimos 10. Primero `isrc:`, después texto |
| Otros quitados | `GET /users/{id}`, `GET /users/{id}/playlists`, lotes de temas, `popularity` | No los usamos |
| Tokens | Refresh token vence a los **6 meses** desde la primera autorización (no se renueva al refrescar); vencido → `400 invalid_grant` | Estado "venció" → Reconectar |
| Límites | 429 con `Retry-After`; en modo desarrollo, cuota compartida por cuenta (`QUOTA_EXCEEDED`) | Respetar `Retry-After`; pausar entre búsquedas como hoy |

## 2. Cómo integrarlo (parte funcional)

### Librería: `requests` directo, no `spotipy`

| | `spotipy` 2.26 (mar-2026) | Cliente propio con `requests` |
|---|---|---|
| Endpoints `/items` | Sí | Sí |
| Límite de búsqueda de 10 | No lo menciona | Lo manejamos nosotros |
| PKCE con `127.0.0.1` y puerto dinámico | Su propio servidor y archivo de caché | Hecho a medida (~80 líneas, solo librería estándar) |
| Dependencia nueva en el `.exe` | Sí | No (`requests` ya viene con tidalapi y ytmusicapi) |
| Tests | Hay que simular su capa | Inyectamos un transporte falso, como ya hacemos |

### Conector `app/connectors/spotify.py` (misma interfaz que los otros)

- `status()`: hay Client ID → hay token → `GET /me` responde. Token vencido (`invalid_grant`) → `expired`. Sin Client ID → nuevo estado `setup` ("falta configurar").
- `connect(browser)`: PKCE. Genera `code_verifier`/`challenge`, abre un servidor en `127.0.0.1:<puerto libre>`, abre `accounts.spotify.com/authorize` en el **navegador elegido** (como Tidal), espera el código (con tiempo límite y que se pueda cancelar), lo cambia por tokens y los guarda en `%LOCALAPPDATA%\PlaylistTransfer\spotify_session.json` (en `.gitignore`).
- `list_playlists()`: `GET /me/playlists` paginado; solo las propias o colaborativas (el resto no se puede leer ni modificar).
- `read(id)`: `GET /playlists/{id}/items?fields=...` paginado de a 50, con ISRC y duración; los archivos locales (`is_local`) se leen pero se marcan como no transferibles.
- `search(song)`: 1) `isrc:XXXX` si el tema trae ISRC (de Tidal siempre); 2) `track:"…" artist:"…"`; 3) título solo. Corta apenas hay coincidencia OK (como hoy).
- `create`, `add` (lotes de 100), `url` (`https://open.spotify.com/playlist/{id}`).
- Errores claros (en los dos idiomas): 403 "no estás en la lista de usuarios de tu app o tu Premium venció", 429 "Spotify pidió esperar", etc.
- Alcances pedidos: `playlist-read-private playlist-read-collaborative playlist-modify-private playlist-modify-public`.

### Coincidencias

Spotify da **ISRC** igual que Tidal: Tidal ⇄ Spotify va a ser casi siempre exacto. Con YouTube Music (sin ISRC) se usa el motor actual (artista, título, versiones, música clásica…), que ya tiene 95 escenarios de prueba. No hace falta tocar `core/matching.py` (lo pedía la SPEC: "sin cambios en core/").

## 3. Refactor previo (para que quede limpio sin romper nada)

Lo que encontré en el código:

| Hoy | Problema |
|---|---|
| `SERVICES` en `app/api.py` y `NAMES` + `USABLE` en `app/ui/app.js` | La lista de servicios está escrita a mano en 2 lugares |
| `Api.create()` arma Tidal y YouTube Music a mano | Sumar uno implica tocar la API |
| `open_url` acepta solo links de Tidal y YouTube Music | Falta Spotify (y el link al panel de desarrolladores) |
| `connect` supone "abrir navegador y esperar"; no se puede cancelar | Spotify necesita un paso previo (Client ID) y conviene poder cancelar cualquier login |
| `app/api.py` (480 líneas) mezcla transferir, repetidos, historial y ajustes | Difícil de seguir |
| `app/ui/app.js` (733 líneas) en un solo archivo | Ídem |
| `legacy/` y `tools/check_reports.py` dependen del script viejo | Ya no se usa para la app |

Propuesta, en orden y **sin cambiar el comportamiento** (los 284 tests y las capturas tienen que dar igual):

1. **Registro de servicios** (`app/connectors/registry.py`): por servicio, la clave, el nombre, cómo se conecta (link, navegador o PKCE), si necesita configuración, los dominios de sus links y si está disponible. `Api.create`, `status()`, `open_url` y la UI salen de ahí. La UI deja de tener `NAMES`/`USABLE`: los pide a `status()`.
2. **Rutas para 3 servicios:** la frase de Inicio y Repetidos ya muestran Spotify; habilitarlo cuando esté disponible. Cualquier par origen → destino (6 combinaciones).
3. **Conectar en dos pasos y cancelable:** `connect` emite eventos (como ya hace la transferencia) y se puede cancelar. Paso de configuración para los servicios que lo piden.
4. **Ordenar la API:** sacar de `api.py` la lógica de transferencias y repetidos a funciones del motor (`core/transfer.py`, un `core/dedupe.py`); `api.py` queda como un puente fino.
5. **Ordenar la UI:** partir `app.js` en varios scripts por pantalla (`i18n.js`, `common.js`, `inicio.js`, `transferir.js`, `repetidos.js`, `historial.js`, `ajustes.js`). **Comprobado hoy:** los módulos de JavaScript (`type="module"`) no cargan desde un archivo local en WebView2; los scripts comunes sí. Se usan scripts comunes.
6. **Quitar `legacy/`** (con tu OK): congelar en un archivo de datos los resultados del script viejo que usan los tests de paridad y `check_reports`, y borrar el script, su `.bat` y `requirements`.

## 4. Quitar repetidos en Spotify (la parte delicada)

Spotify quita por URI y **saca todas las copias**. Dos casos:

| Caso | Qué pasa | Cómo lo resolvemos |
|---|---|---|
| La copia es **otra grabación** (otra URI: remaster, versión del video…) | Quitar esa URI no toca la que queda | Directo, como hoy |
| Es **exactamente el mismo tema dos veces** (misma URI) | Quitarla borraría también la que queda | Quitar la URI y **volver a agregar una** en la posición de la que queda (`POST /items` con `position`). Después se relee la playlist para verificar; si faltara, se vuelve a agregar y se avisa |

Seguridad: se mantiene todo lo que ya hay (confirmación en el diálogo, se relee la playlist antes y no se toca nada si cambió). Detalle visible: ese tema vuelve con fecha de "agregado" nueva. Propongo avisarlo en el diálogo **solo** cuando hay casos así.

## 5. Diseño (UX/UI), manteniendo la simplicidad

- **Ajustes › Servicios › Spotify:** estado "falta configurar" y botón **Configurar**, que abre un panel en la misma fila (sin ventanas nuevas) con 3 pasos cortos:
  1. "Necesitás **Spotify Premium** (lo exige Spotify)." Botón **Abrir el panel de Spotify**.
  2. "Creá una app (nombre a elección), marcá **Web API** y pegá esta dirección de retorno:" `http://127.0.0.1/callback` con botón **Copiar**.
  3. "Pegá el **Client ID**:" campo con validación (32 caracteres) → **Conectar**.
  Después queda como Tidal: "conectado" / "venció" / Desconectar. Un link "¿Cómo era?" vuelve a mostrar los pasos.
- **Fichas de arriba:** Spotify pasa de "próximamente" a "sin configurar" / "sin conectar" / "✓".
- **Transferir:** sin cambios de pantalla; Spotify aparece en las listas de origen y destino. Nota corta si una playlist de Spotify no es tuya ("Spotify solo deja leer tus playlists y las colaborativas").
- **Errores en lenguaje simple:** Premium vencido, no estás en la lista de usuarios de tu app, Spotify pidió esperar.
- **Sin cuenta Premium:** la app sigue funcionando entre Tidal y YouTube Music; Spotify queda en "sin configurar".
- Todo en castellano e inglés (los tests de idioma ya lo exigen).

## 6. Plan de ejecución

| Etapa | Qué | Cómo sabemos que terminó |
|---|---|---|
| 0 | (Opcional) Publicar **v0.2.0** con las mejoras de hoy | Release en GitHub; el `.exe` abre |
| 1 | Refactor 1–3 (registro, rutas, conectar cancelable) | Los 284 tests pasan; capturas iguales; app real abre |
| 2 | Conector de Spotify + PKCE + servidor local, con transporte falso | Tests nuevos: login, paginado, búsqueda por ISRC, lotes, 429, token vencido, repetidos con la misma URI |
| 3 | Configuración y estados en Ajustes, textos ES/EN | Tests de la UI (Edge) y de diseño; capturas revisadas |
| 4 | **Tu prueba real**: crear la app en Spotify, conectar, Tidal → Spotify y una limpieza | Criterio de la F5 en la SPEC |
| 5 | Refactor 4–6 (API, app.js, legacy) y documentación | Tests verdes; README con Spotify; v0.3.0 |

Riesgos y cómo los cubrimos:

| Riesgo | Mitigación |
|---|---|
| El puerto dinámico en `127.0.0.1` no funciona como dice la doc | Plan B: puerto fijo (por ejemplo 8973) registrado tal cual; se prueba en la etapa 4 |
| No sé si el dueño de la app se tiene que agregar a sí mismo a la lista de usuarios | Se ve en la etapa 4; el paso 2 de la guía lo indica si hace falta |
| Spotify vuelve a cambiar la API | Cliente propio y chico; tests con transporte falso; mensajes que sugieren actualizar |
| Quitar + volver a agregar falla a mitad de camino | Verificación al releer, reintento y aviso con el nombre del tema |
| Sin Premium para probar | Etapas 1–3 se pueden hacer igual; la 4 queda pendiente |

## 7. Preguntas para vos

1. ¿Tenés **Spotify Premium** para la prueba real?
2. ¿Te parece bien la estrategia de §4 (quitar y volver a agregar) para los repetidos exactos?
3. ¿Borramos `legacy/` en la etapa 5?
4. ¿Publicamos la v0.2.0 antes de empezar?

## Fuentes

- [Quota modes](https://developer.spotify.com/documentation/web-api/concepts/quota-modes) (oficial)
- [Redirect URIs](https://developer.spotify.com/documentation/web-api/concepts/redirect_uri) (oficial)
- [February 2026 migration guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide) y [changelog de febrero](https://developer.spotify.com/documentation/web-api/references/changes/february-2026) (oficial)
- [Changelog de marzo de 2026](https://developer.spotify.com/documentation/web-api/references/changes/march-2026) (oficial: se repone `external_ids`)
- Referencias oficiales: [Remove playlist items](https://developer.spotify.com/documentation/web-api/reference/remove-items-playlist), [Add items](https://developer.spotify.com/documentation/web-api/reference/add-items-to-playlist), [Get playlist items](https://developer.spotify.com/documentation/web-api/reference/get-playlists-items), [Search](https://developer.spotify.com/documentation/web-api/reference/search)
- [Introducing refresh token expiration](https://developer.spotify.com/blog/2026-06-18-refresh-token-expiration) (blog oficial, 18-jun-2026)
- [Changelog de spotipy](https://github.com/spotipy-dev/spotipy/blob/master/CHANGELOG.md) (2.26.0, 3-mar-2026)
- Contexto (secundario): [TechCrunch, feb-2026](https://techcrunch.com/2026/02/06/spotify-changes-developer-mode-api-to-require-premium-accounts-limits-test-users/), reportes de desarrolladores sobre posiciones al quitar: [comunidad de Spotify](https://community.spotify.com/t5/Spotify-for-Developers/Positions-field-in-JSON-body-is-ignored-when-removing-tracks/td-p/6044424), [spotipy #1098](https://github.com/spotipy-dev/spotipy/issues/1098)
