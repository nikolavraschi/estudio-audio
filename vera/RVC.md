# Cómo se conectaría un motor RVC (no implementado todavía)

RVC convierte tu voz usando un **modelo entrenado** con grabaciones de una voz femenina real
(con permiso de esa persona). Suele ser el salto de calidad más grande, pero exige un modelo
`.pth` (y opcionalmente un `.index`).

## Dónde va
`engines/rvc_applio.py` ya tiene la clase `RvcApplioEngine` con sus sliders
(transposición, index rate, protect). Hoy aparece en el menú como “próximamente” y no se puede usar.

Todo motor cumple el contrato de `engines/base.py`:
```python
def process(self, audio, sr, params) -> audio   # mono float, misma duración exacta
```
La interfaz, el A/B, los presets y el lote funcionan igual con cualquier motor.

## Plan de conexión con Applio
1. Instalar Applio aparte (tiene su propio Python; no se mezcla con este `.venv`).
2. En `process()`:
   - guardar `audio` en un WAV temporal,
   - ejecutar por línea de comandos, por ejemplo:
     ```
     <applio>\env\python.exe <applio>\core.py infer --input_path in.wav --output_path out.wav
         --pth_path VERA.pth --index_path VERA.index --pitch 12 --index_rate 0.75 --protect 0.33
     ```
     (los nombres exactos de las opciones dependen de la versión de Applio: se verifican al conectarlo),
   - leer `out.wav`, pasarlo a 48 kHz y devolverlo con `match_length(y, len(audio))`.
3. Agregar en la configuración la ruta de Applio y del modelo, y que `is_available()`
   devuelva `True` cuando existan.

## GPU AMD
La RX 5500 XT no tiene CUDA. Opciones: Applio por **CPU** (lento pero sirve para lote nocturno),
DirectML si la versión de Applio lo soporta, o procesar en **Google Colab** y traer los WAV.
En ese último caso el motor solo copiaría archivos a/desde una carpeta sincronizada.
