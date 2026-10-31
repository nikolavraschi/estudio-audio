# VERA: transformador de voz para SEÑAL

Convierte tus tomas actuadas (voz masculina) en la voz femenina de VERA,
conservando timing, pausas e intención. La duración del archivo no cambia nunca.

## Cómo se abre
Doble clic en **`abrir_estudio.bat`** (carpeta de arriba) y elegí *VERA*, o directamente en
**`abrir_vera.bat`**. La primera vez instala todo solo (`instalar.bat` de la carpeta de arriba).
Usa Python 3.12 en un entorno virtual (`.venv`) compartido con Audio Mixer, así no toca nada más de la PC.

## Fase 1: probar un WAV ya grabado
1. **Abrir WAV…** muestra la duración y *tu tono mediano* (útil para elegir el tono de VERA).
2. **Motor**:
   - *Praat – Change gender*: PSOLA + formantes. Rápido y natural, respeta bien la respiración.
   - *WORLD vocoder*: separa tono, formantes y aire. A veces más limpio en cambios grandes,
     a veces algo “zumbón”. Es más lento (unos 2 s cada 6 s de audio).
3. **Sliders** (valores iniciales hombre → mujer):
   - Formantes 1.17 (prueba entre 1.12 y 1.22; más de 1.25 suena a nena o a dibujito).
   - Tono mediano 210 Hz.
   - Rango de entonación 1.10.
4. **Post-proceso** (todo se puede apagar):
   | Etapa | Valor | Para qué |
   |---|---|---|
   | Pasa altos | 90 Hz | quita retumbe, golpes al micrófono y zumbido grave |
   | EQ presencia | +2 dB a 3 kHz | claridad / inteligibilidad |
   | De-esser | desde 6 kHz, máx. 6 dB | las eses quedan muy filosas después de subir formantes |
   | Compresión | 2:1, suave | empareja sílabas sin aplastar la actuación |
   | Loudness | **−23 LUFS** (pico máx. −1 dBTP) | ver abajo |
5. **A / B**: con *Repetir* activado, apretá las teclas **A** y **B** mientras suena:
   cambia al instante en el mismo punto de la frase. *Igualar volumen A/B* hace que ambos
   suenen igual de fuerte (si no, el más fuerte siempre “parece mejor”).
6. **Guardar procesado…** guarda un WAV 48 kHz / 24 bit en `pruebas/`. El original nunca se pisa.
7. **Mandar a Audio Mixer** guarda el resultado en `pruebas/` y lo abre en el editor para
   cortarlo, mezclarlo con otras tomas o sumarle efectos.

## Fase 2: grabar con el micrófono
En *1. Archivo o micrófono* elegí el micrófono y apretá **● Grabar** (o la tecla **R**).
Mientras grabás, la barrita muestra el nivel: que llegue a ¾ en las partes fuertes.
Al parar, la toma cruda se guarda sola en `tomas/` (nunca se borra) y se transforma al instante,
lista para comparar con A / B. Si la toma saturó, VERA avisa.

## Fase 3: presets
Un preset guarda **motor + sliders + post-proceso** en `presets/<nombre>.json`.
- Elegilo en la lista *Preset* y se carga todo.
- **Guardar como…** guarda lo que tenés ahora (si ya existe, pregunta antes de reemplazar).
- Vienen dos: *VERA base* (Praat) y *VERA WORLD*.
Los presets también se usan en el lote y en Audio Mixer (tecla `V` sobre un fragmento).

## Fase 4: lote (carpeta entera)
Pestaña **Lote**: elegí la carpeta con las tomas, la de salida (por defecto `salida/`) y el preset.
- Procesa .wav, .flac, .aiff y .ogg, con subcarpetas si querés, y respeta esa estructura en la salida.
- Los originales nunca se pisan. Se puede *saltear lo ya procesado* para retomar un lote.
- Verifica que cada archivo dure exactamente lo mismo que el original; si no, lo marca como error.
- Deja un **`informe.csv`** (se abre con Excel) con duración, LUFS, pico y estado de cada archivo.
- Un archivo roto no frena el lote: queda marcado en el informe y el detalle va a `errores.log`.

## Fase 5: RVC (Applio)
Motor *RVC (Applio)*: convierte con un modelo de voz entrenado. Ver **RVC.md** para instalarlo y
configurarlo (botón **Configurar RVC…**). Como es lento, no se reprocesa solo al mover un slider:
apretá **Procesar**.

### ¿Por qué −23 LUFS?
Es el estándar EBU R128 para diálogo y está a un dB de la recomendación de Sony (ASWG‑R001)
para juegos de consola/PC (−24 LUFS). Con todas las frases al mismo nivel, la voz de VERA
suena pareja de una frase a otra y queda margen para que el motor del juego la suba, la
baje o la mezcle con música y efectos sin saturar. El techo de −1 dBTP evita distorsión
al convertir a OGG/MP3. Si el juego es para celular, −18 LUFS es lo habitual.

## Estructura
```
app.py            ventana (pestañas Probar voz y Lote)
audio_io.py       leer/guardar WAV, dispositivos, reproductor A/B, grabador
post.py           cadena de post-proceso
preset_store.py   presets (leer/guardar) y transform(): motor + post en un paso
batch.py          procesamiento en lote + informe.csv
engines/          motores intercambiables (base.py = contrato común)
  praat_gender.py
  world_vocoder.py
  rvc_applio.py   RVC vía Applio (ver RVC.md)
presets/          tus presets (.json)
pruebas/          lo que guardes en Probar voz
tomas/            grabaciones crudas del micrófono
salida/           resultado del lote
config_local.json rutas de Applio y del modelo (es de tu PC: no se sube a git)
errores.log       si algo falla, el detalle queda acá
```
