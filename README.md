# PlaylistTransfer

Pasa temas de **Tidal → YouTube Music** y de **YouTube Music → Tidal** desde tu compu, sin páginas externas.
Elegís la playlist de origen y la de destino (una nueva, una existente o varias), y los temas que ya están en el destino no se repiten.

## Cómo usarlo

**Doble clic en `Transferir playlists.bat`.** Nada más.

1. **La primera vez** instala lo necesario solo (tarda alrededor de un minuto).
2. **La primera vez** te pregunta en qué navegador abrir las ventanas para iniciar sesión (Enter = el predeterminado). Lo recuerda para las próximas veces:
   ```
   ¿En qué navegador abro las ventanas para iniciar sesión (Tidal y YouTube Music)?
     1. Vivaldi (predeterminado)
     2. Google Chrome
     3. Microsoft Edge
   ```
3. Te pregunta qué querés hacer:
   ```
   1. Pasar temas de Tidal a YouTube Music
   2. Pasar temas de YouTube Music a Tidal
   3. Cambiar el navegador (ahora: Vivaldi)
   4. Quitar temas repetidos de una playlist
   ```
4. **Solo la primera vez**, conecta tus cuentas en ese navegador:
   - **Tidal:** se abre la página de autorización en tu navegador de siempre. Aceptá y volvé a la ventana negra.
   - **YouTube Music:**
     - **Vivaldi, Chrome, Edge, Brave u Opera:** abre una ventana de ese navegador con un perfil aparte, solo para este script (los navegadores basados en Chrome no dejan que un programa use tu perfil normal). Iniciá sesión con tu cuenta de Google y la ventana se cierra sola.
     - **Firefox:** si ya tenés YouTube abierto con tu cuenta, toma esa sesión sin abrir nada.

   Después queda guardado y ya no te lo pide más.
5. Elegís la playlist de **origen** de la lista.
6. Elegís el **destino**:
   ```
   ¿A qué playlist de YouTube Music los agrego?
     0. Crear una playlist nueva
     1. Rock  (42 temas)
     2. Para correr  (18 temas)
     (Podés elegir varias separadas por coma, por ejemplo: 2,5)
   ```
7. Busca los temas, los agrega al final de cada playlist elegida y te muestra el resultado de cada tema:
   - `✓` coincidencia clara.
   - `?` se agregó, pero conviene revisarlo (puede ser una versión en vivo, un remix o un video).
   - `✗` no se encontró; buscalo a mano.

   El detalle queda en `reporte_<fecha>.csv`, que podés abrir con Excel.

### Repetidos y otras versiones
El script compara **por canción**, no solo por el ID exacto: mismo artista y mismo título base, sin contar lo que cambia entre versiones, como `(Official Video)`, `(Remastered 2009)`, `(Mono)`, `(Deluxe Edition)`, `feat. X`, otro álbum, otra portada u otra duración.
- **Al transferir:** si en el destino ya está otra versión de la canción, no la agrega y te avisa con `≈` cuál encontró.
- **Opción 4 del menú:** busca repetidos dentro de una playlist (de Tidal o de YouTube Music) y te muestra cuáles quitaría. Queda la que estaba primero en la playlist. **No quita nada hasta que confirmes con `s`.**

Solo aparecen playlists tuyas: las de otros usuarios que guardaste no se pueden modificar. "Me gusta" de YouTube Music tampoco aparece.

## Si algo falla
| Problema | Qué hacer |
|---|---|
| "No encontré Python" | Instalá Python desde https://www.python.org/downloads/ (marcá "Add python.exe to PATH") y volvé a abrir el `.bat`. |
| Google dice "Este navegador o esta app no son seguros" | Usá el método manual: abrí PowerShell en la carpeta y corré `py legacy/transfer_playlist.py setup-yt --manual` (pasos abajo). |
| Querés cambiar el navegador | Opción **3** del menú principal. |
| Querés conectar otra cuenta de Google | Borrá la carpeta `.yt-login` y el archivo `browser.json`, y volvé a abrir el `.bat`. |
| "No pude abrir el navegador" | Cerrá todas las ventanas de ese navegador y probá de nuevo, o elegí otro con la opción **3** del menú. |
| Querés conectar otra cuenta de Tidal | Borrá `tidal_session.json` y volvé a abrir el `.bat`. |
| Un error raro después de meses | Actualizá las librerías: `py -m pip install -U -r legacy/requirements.txt`. |

### Método manual para YouTube Music (solo si falla el automático)
1. Abrí https://music.youtube.com con tu sesión iniciada, apretá F12 → pestaña **Network** y filtrá por `browse`.
2. Hacé clic en cualquier sección de la página y elegí una petición **POST** a `browse`.
3. Copiá sus **Request Headers** (en Firefox: clic derecho → Copy Value → Copy Request Headers).
4. En PowerShell: `py legacy/transfer_playlist.py setup-yt --manual`. Pegá los headers y apretá Enter, Ctrl+Z y Enter.

## Uso avanzado (por consola)
Abrí PowerShell en la carpeta (en el Explorador: clic derecho → "Abrir en Terminal").

```powershell
py legacy/transfer_playlist.py                     # lo mismo que el .bat: todo con menús
py legacy/transfer_playlist.py tidal2yt            # salta la primera pregunta
py legacy/transfer_playlist.py tidal2yt https://tidal.com/playlist/XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX --to "https://music.youtube.com/playlist?list=XXXX" --dry-run
py legacy/transfer_playlist.py yt2tidal "https://music.youtube.com/playlist?list=XXXX" --new --name "Desde YouTube"
```
Poné las URLs de YouTube entre comillas: el `&` rompe el comando en PowerShell.

| Opción | Qué hace |
|---|---|
| *(URL de origen)* | Opcional. Si no la pasás, elegís de una lista |
| `--to URL_O_ID` | Agrega a esa playlist existente. Se puede repetir para varias |
| `--new` | Crea una playlist nueva, sin mostrar el menú de destino |
| `--name "Nombre"` | Nombre de la playlist nueva (por defecto, el mismo del origen) |
| `--dry-run` | Solo busca, genera el reporte y dice cuántos temas agregaría; no modifica nada |
| `--public` | (YouTube Music) la playlist nueva se crea pública; por defecto es privada |

Al principio de `legacy/transfer_playlist.py` podés ajustar `SCORE_OK` (0.75) y `SCORE_MIN` (0.60), los umbrales de coincidencia.

## Privacidad
- Todo corre en tu compu. Las sesiones quedan guardadas en esta carpeta, en `tidal_session.json`, `browser.json` y la carpeta `.yt-login` (perfiles de navegador solo para este script). Si usás Firefox, el script lee tus cookies de YouTube de tu perfil, solo en tu compu, para no pedirte que inicies sesión de nuevo.
- **No compartas esos archivos**: equivalen a tener tus cuentas abiertas. El `.gitignore` ya los excluye por si subís la carpeta a GitHub.
- `tidalapi` y `ytmusicapi` no son librerías oficiales. Para uso personal funcionan bien, pero si Tidal o Google cambian algo pueden fallar hasta que las actualicen.
