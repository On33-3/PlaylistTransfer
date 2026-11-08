# PlaylistTransfer

Una app de escritorio para Windows que **pasa tus playlists entre Tidal, YouTube Music y Spotify** y **quita temas repetidos**. Gratis, open source y sin cuentas propias: todo pasa en tu compu.

![Inicio](docs/img/inicio.png)

## Descargar

1. Entrá a [**Releases**](../../releases/latest) y bajá `PlaylistTransfer-<versión>-windows.zip`.
2. Descomprimilo donde quieras (por ejemplo, en Documentos).
3. Abrí `PlaylistTransfer.exe`. No hace falta instalar Python.

> **Windows protegió su PC:** aparece porque el programa todavía no está firmado digitalmente. Tocá **Más información → Ejecutar de todas formas**. Si querés verificar el archivo, cada release trae su código SHA-256.

Funciona en Windows 10 y 11.

## Qué hace

**Transferir.** Elegís una playlist de origen y una o varias de destino (o una nueva). Ves cada tema en vivo: ✓ agregado, ? puede ser otra versión (decidís vos al final), ✗ no encontrado y ≈ ya estaba. Con *Probar sin cambiar nada* ves el resultado antes de tocar tus playlists.

![Transferir](docs/img/transferir.png)

**Quitar repetidos.** Encuentra la misma canción dos veces aunque sea otro video, un remaster u otra portada. Las versiones en vivo o remix aparecen sin tildar, y nada se quita sin que lo confirmes.

![Quitar repetidos](docs/img/repetidos.png)

**Historial.** Cada transferencia y cada limpieza quedan registradas con el detalle de cada tema. Si querés, lo descargás como un archivo de texto liviano, fácil de leer o de pasarle a una IA.

![Historial](docs/img/historial.png)

## Spotify

Spotify es opcional y pide dos cosas (las exige Spotify desde 2026, no la app):

1. **Spotify Premium.**
2. **Tu propia app de Spotify**, gratis: en **Ajustes › Spotify › Configurar** la app te guía paso por paso, con un botón para cada página y para copiar cada dato que hay que pegar. La usan solo vos y hasta 5 personas que agregues.

> **Todavía en prueba:** la conexión con Spotify está probada contra una simulación de su API, pero nadie la probó aún con una cuenta Premium real. Si la probás, contanos cómo te fue (y si algún nombre de botón de los pasos no coincide con lo que ves en Spotify).

Detalles: Spotify solo deja leer tus playlists y las colaborativas; cada 6 meses pide volver a conectar. Para quitar un repetido exacto, Spotify saca todas las copias, así que la app vuelve a poner la que queda en su lugar (aparece como agregada de nuevo) y lo verifica. Entre Tidal y Spotify las coincidencias suelen ser exactas (las dos usan el código ISRC de cada grabación).

## Cómo decide si es "la misma canción"

- Tiene que coincidir **el artista**. Una canción puede tenerla más de un artista: si buscás el *Hallelujah* de Jeff Buckley y solo está el de Leonard Cohen, no se agrega solo; la app te lo ofrece y decidís vos.
- Covers, karaokes y tributos no cuentan como el original.
- Partes y secuelas son temas distintos (*Pt. 1* ≠ *Pt. 2*, *The Unforgiven* ≠ *The Unforgiven II*), y un *Intro* de otro álbum también.
- Una versión en vivo, acústica o remix del mismo tema queda marcada con **?** y **no se agrega sola**: al final decidís con "+ Agregar igual".

Los umbrales se pueden ajustar en **Ajustes › Coincidencias**.

La app está en **castellano e inglés** (Ajustes › Idioma). La primera vez usa el idioma de Windows. / *The app is available in Spanish and English (Settings › Language).*

## Privacidad

- Iniciás sesión en tu propio navegador. Tus sesiones, preferencias e historial se guardan **solo en tu compu**, en `%LOCALAPPDATA%\PlaylistTransfer`. Para borrar todo, cerrá la app y borrá esa carpeta.
- La app no tiene servidores ni estadísticas: solo habla con Tidal, YouTube Music y Spotify.
- Usa librerías **no oficiales** ([tidalapi](https://github.com/tamland/python-tidal) y [ytmusicapi](https://github.com/sigma67/ytmusicapi)). Si un servicio cambia algo, puede dejar de andar hasta que se actualicen. Spotify usa su API oficial, con la app que creás vos.

No está afiliado a Tidal, Google ni Spotify.

## Para desarrollar

Necesitás Windows y Python 3.14.

```powershell
py -m pip install -r requirements-dev.txt
py -m pytest -q                 # tests (los de la UI necesitan playwright y Edge)
py -m app                       # abre la app
py packaging/build.py           # arma dist/PlaylistTransfer-<versión>-windows.zip
```

- `app/core/`: el motor (coincidencias, repetidos, transferencias). Cada regla tiene su escenario en `tests/test_scenarios.py`.
- `app/connectors/`: un conector por servicio, con la misma interfaz. Todos se declaran en `registry.py`: de ahí salen la API y la ventana.
- `app/ui/`: la interfaz (HTML, CSS y JS, sin frameworks), dentro de una ventana de [pywebview](https://pywebview.flowrl.com/).
- [`SPEC.md`](SPEC.md): el plan, las fases y por qué se decidió cada cosa.
- Publicar una versión: subí el número en `app/__init__.py` y creá el tag `vX.Y.Z`. GitHub Actions corre los tests, compila el `.exe`, prueba que abra y lo publica en Releases.

## Licencia

[MIT](LICENSE). Las librerías incluidas tienen sus propias licencias (ver `THIRD-PARTY-NOTICES.txt` dentro del zip). tidalapi es LGPL-3.0: en la app va como archivos `.py` sueltos en `_internal\tidalapi`, así se puede reemplazar por otra versión.
