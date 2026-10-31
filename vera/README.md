# VERA: transformador de voz para SEÑAL

Convierte tus tomas actuadas (voz masculina) en la voz femenina de VERA,
conservando timing, pausas e intención. La duración del archivo no cambia nunca.

## Cómo se abre
Doble clic en **`abrir_vera.bat`**. La primera vez instala todo solo (`instalar.bat`).
Usa Python 3.12 en un entorno virtual propio (`.venv`), así no toca nada más de la PC.

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

### ¿Por qué −23 LUFS?
Es el estándar EBU R128 para diálogo y está a un dB de la recomendación de Sony (ASWG‑R001)
para juegos de consola/PC (−24 LUFS). Con todas las frases al mismo nivel, la voz de VERA
suena pareja de una frase a otra y queda margen para que el motor del juego la suba, la
baje o la mezcle con música y efectos sin saturar. El techo de −1 dBTP evita distorsión
al convertir a OGG/MP3. Si el juego es para celular, −18 LUFS es lo habitual.

## Estructura
```
app.py            ventana
audio_io.py       leer/guardar WAV, dispositivos, reproductor A/B
post.py           cadena de post-proceso
engines/          motores intercambiables (base.py = contrato común)
  praat_gender.py
  world_vocoder.py
  rvc_applio.py   lugar reservado para RVC (ver RVC.md)
pruebas/          lo que guardes en la fase 1
errores.log       si algo falla, el detalle queda acá
```
