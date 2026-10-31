# Estudio de audio

Dos herramientas en un solo lugar, con una sola instalación:

| Carpeta | App | Para qué |
|---|---|---|
| `vera/` | **VERA** | Transforma tus tomas (voz masculina) en la voz femenina de VERA para *SEÑAL*, sin cambiar la duración. |
| `mixer/` | **Audio Mixer** | Corta fragmentos de varios audios, los ordena, les aplica efectos y exporta un solo audio. |

## Cómo se abre (Windows)
Doble clic en **`abrir_estudio.bat`** y elegí qué app abrir (podés tener las dos abiertas).
La primera vez corre **`instalar.bat`**, que crea un entorno `.venv` con Python 3.12 e instala todo.
Si no tenés Python 3.12: abrí una terminal y escribí `py install 3.12`.

También siguen andando los accesos de cada app: `vera\abrir_vera.bat` y `mixer\Audio Mixer.bat`.

## Cómo se conectan
- En **VERA**, el botón **Mandar a Audio Mixer** guarda la toma transformada y la abre en el Mixer.
- En **Audio Mixer**, clic derecho sobre un fragmento → **🎙 Voz VERA…** (o la tecla `V`):
  lo transforma con un preset de VERA. Se crea un audio nuevo «(VERA)» y el original no se toca
  (Ctrl+Z para volver).
- Menú **Estudio → Abrir VERA** desde el Mixer.

## Fases de VERA
1. Probar un WAV con A/B y post-proceso.
2. Grabar con el micrófono y transformar al instante.
3. Presets guardables (motor + sliders + post-proceso).
4. Lote: una carpeta entera, con verificación de duración e `informe.csv`.
5. RVC conectado a Applio (ver `vera/RVC.md`).

Detalles en `vera/README.md` y `mixer/README.md`.

## Estructura
```
abrir_estudio.bat   abre el lanzador
instalar.bat        crea .venv e instala requirements.txt
estudio.py          ventana de inicio
requirements.txt    librerías de las dos apps
vera/               transformador de voz
mixer/              editor de fragmentos
```

## Historial
Este repo une `modificador-voces` (ahora `vera/`) y `audio-mixer` (ahora `mixer/`)
conservando el historial de commits de los dos.
