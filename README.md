# Audio Mixer

Cargá audios (notas de voz de WhatsApp `.opus`, mp3, m4a, wav, ogg, flac y hasta videos),
elegí fragmentos de cada uno, ordenalos y exportá un único audio continuo.

## Funciones

- Editor de onda con zoom: marcás con el mouse la parte que querés y la agregás a la mezcla.
- Mezcla por bloques: arrastrar para ordenar, recortar bordes, cortar en dos, duplicar, deshacer/rehacer.
- Volumen por fragmento, pausa entre partes, fundido y «igualar volúmenes» entre grabaciones.
- **Efectos por fragmento** (tecla `E`): ecualizador (graves/medios/agudos), tono más grave/agudo,
  velocidad, quitar ruido y zumbido, y efectos especiales (eco, catedral, teléfono, radio, robot,
  alien, coro, 8-bit, al revés…).
- Exporta a WAV, MP3, M4A, OGG Opus y FLAC.
- Proyectos `.amix` para seguir después.

## Instalación

Requiere Python 3.10+.

```
pip install -r requirements.txt
python audio_mixer.py
```

En Windows también se puede abrir con `Audio Mixer.bat`.

No hace falta instalar ffmpeg aparte: viene con `imageio-ffmpeg`.

## Atajos

| Tecla | Acción |
|---|---|
| Espacio | Escuchar selección |
| Enter | Agregar selección a la mezcla |
| Ctrl+Espacio | Escuchar la mezcla |
| S | Cortar bloque en la línea amarilla |
| E | Efectos del fragmento elegido |
| Supr | Borrar fragmento |
| Ctrl+Z / Ctrl+Y | Deshacer / rehacer |
| Ctrl+S | Guardar proyecto |
| Ctrl+E | Exportar audio |
| F1 | Ayuda |
