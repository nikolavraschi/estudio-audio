# Motor RVC (Applio) — Fase 5

RVC convierte tu voz usando un **modelo entrenado** con grabaciones de una voz femenina real
(con permiso de esa persona). Suele ser el salto de calidad más grande, pero exige un modelo
`.pth` (y opcionalmente un `.index`).

## 1. Instalar Applio (aparte)
Applio tiene su propio Python y no se mezcla con el `.venv` del estudio.
Instalalo siguiendo su página (https://applio.org). Queda una carpeta con `core.py`
y, en Windows, `env\python.exe`.

## 2. Conectarlo en VERA
1. Botón **Configurar RVC…** (arriba a la derecha, en *2. Preset, motor y voz*).
2. Elegí:
   - **Carpeta de Applio**: la que tiene `core.py`.
   - **Modelo (.pth)** y, si lo tenés, el **índice (.index)**.
   - *Python de Applio*: dejalo vacío; VERA busca `env\python.exe` solo. Solo hace falta si
     Applio está instalado de otra forma.
   - *Detector de tono*: `rmvpe` es el recomendado.
3. **Revisar** muestra si falta algo. **Guardar** lo deja en `config_local.json`
   (son rutas de tu PC, por eso no se sube a git).
4. Elegí el motor **RVC (Applio)**, ajustá y apretá **Procesar** (no se reprocesa solo porque tarda).

## Sliders
| Slider | Inicial | Qué hace |
|---|---|---|
| Transposición | +12 | semitonos; hombre → mujer suele ser +12 (si el modelo ya es agudo, +8 a +10) |
| Index rate | 0.75 | más = más parecido al timbre del modelo, pero puede sumar artefactos |
| Protect | 0.33 | más bajo protege más consonantes y respiración |

El post-proceso, el A/B, los presets, el lote y la tecla `V` del Mixer funcionan igual con RVC.
La duración del resultado se recorta/rellena para que sea **idéntica** a la del original.

## Cómo lo llama VERA
```
<applio>\env\python.exe <applio>\core.py infer --input_path entrada.wav --output_path salida.wav
    --pth_path VERA.pth --index_path VERA.index --pitch 12 --index_rate 0.75 --protect 0.33
    --f0_method rmvpe --export_format WAV
```
Si tu versión de Applio pide otras opciones, agregalas en **Opciones extra** (por ejemplo
`--split_audio True`). Si Applio falla, el mensaje de error y el comando exacto quedan en `errores.log`.

## GPU AMD
La RX 5500 XT no tiene CUDA. Opciones: Applio por **CPU** (lento pero sirve para un lote nocturno),
DirectML si la versión de Applio lo soporta, o procesar en **Google Colab** y traer los WAV.
Para lotes largos conviene dejar la pestaña **Lote** trabajando con un preset de RVC.
