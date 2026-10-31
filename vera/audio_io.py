"""Lectura/escritura de WAV, lista de dispositivos y reproductor A/B."""
import threading

import numpy as np
import sounddevice as sd
import soundfile as sf

from engines.base import resample

SR = 48000
FADE_S = 0.012  # fundido al cambiar A/B, evita el "click"


def load_audio(path):
    """Lee un archivo de audio, lo pasa a mono y a 48 kHz. Devuelve (audio, sr_original)."""
    data, sr = sf.read(path, always_2d=True, dtype="float64")
    mono = data.mean(axis=1)
    return resample(mono, sr, SR), sr


def save_wav(path, audio, sr=SR):
    """WAV 24 bit. Recorta a [-1, 1] por seguridad."""
    sf.write(path, np.clip(audio, -1.0, 1.0), sr, subtype="PCM_24")


def list_devices(kind):
    """kind = 'output' o 'input'. Devuelve [(indice, etiqueta)] y el índice por defecto."""
    apis = sd.query_hostapis()
    key = "max_output_channels" if kind == "output" else "max_input_channels"
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d[key] > 0:
            out.append((i, f"{d['name']}  [{apis[d['hostapi']]['name']}]"))
    default = sd.default.device[1 if kind == "output" else 0]
    return out, default


class ABPlayer:
    """Reproduce el original (A) o el procesado (B) y permite cambiar entre ambos
    sin cortar: sigue en el mismo punto de la frase."""

    def __init__(self):
        self.lock = threading.Lock()
        self.device = None
        self.loop = False
        self.which = "B"
        self.pos = 0
        self.stream = None
        self.stream_sr = SR
        self._tracks = {"A": None, "B": None}
        self._prepared = {}
        self._fade_from = None
        self._fade_left = 0

    # ---- configuración ----
    def set_device(self, device):
        self.stop()
        self.device = device

    def set_tracks(self, a, b):
        with self.lock:
            n = max(len(a), len(b))
            self._tracks = {"A": np.pad(a, (0, n - len(a))), "B": np.pad(b, (0, n - len(b)))}
            self._prepare()

    def _prepare(self):
        self._prepared = {k: (resample(v, SR, self.stream_sr).astype(np.float32) if v is not None else None)
                          for k, v in self._tracks.items()}
        self.pos = min(self.pos, len(self._prepared["A"]) - 1) if self._prepared["A"] is not None else 0

    # ---- control ----
    @property
    def playing(self):
        return self.stream is not None and self.stream.active

    def position_s(self):
        return self.pos / self.stream_sr

    def length_s(self):
        a = self._prepared.get("A")
        return 0.0 if a is None else len(a) / self.stream_sr

    def play(self, which):
        self.stop()
        if self._tracks["A"] is None:
            return
        self.which = which
        self._open_stream()
        with self.lock:
            self.pos = 0
            self._fade_left = 0
        self.stream.start()

    def switch(self, which):
        """Cambia A/B. Si está sonando, sigue desde el mismo punto."""
        if not self.playing:
            self.play(which)
            return
        with self.lock:
            if which != self.which:
                self._fade_from = self.which
                self._fade_left = int(FADE_S * self.stream_sr)
                self.which = which

    def stop(self):
        if self.stream is not None:
            try:
                self.stream.abort()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    def _open_stream(self):
        info = sd.query_devices(self.device, "output")
        channels = min(2, info["max_output_channels"])
        try:
            sr = SR
            stream = sd.OutputStream(device=self.device, samplerate=sr, channels=channels,
                                     dtype="float32", callback=self._callback)
        except Exception:
            # Algunos dispositivos (WASAPI) solo aceptan su propia frecuencia.
            sr = int(info["default_samplerate"])
            stream = sd.OutputStream(device=self.device, samplerate=sr, channels=channels,
                                     dtype="float32", callback=self._callback)
        with self.lock:
            if sr != self.stream_sr:
                self.stream_sr = sr
                self._prepare()
        self.stream = stream

    def _callback(self, outdata, frames, time_info, status):
        with self.lock:
            cur = self._prepared[self.which]
            n = len(cur)
            idx = self.pos + np.arange(frames)
            if self.loop:
                idx %= n
            valid = idx < n
            out = np.zeros(frames, np.float32)
            out[valid] = cur[idx[valid]]
            if self._fade_left > 0:
                total = int(FADE_S * self.stream_sr)
                old = np.zeros(frames, np.float32)
                old[valid] = self._prepared[self._fade_from][idx[valid]]
                k = min(frames, self._fade_left)
                ramp = np.ones(frames, np.float32)
                ramp[:k] = (total - self._fade_left + np.arange(k)) / total
                out = out * ramp + old * (1 - ramp)
                self._fade_left -= k
            self.pos += frames
            if self.loop:
                self.pos %= n
            outdata[:] = out[:, None]
            finished = not self.loop and self.pos >= n
        if finished:
            raise sd.CallbackStop


class Recorder:
    """Graba del micrófono (Fase 2). Guarda bloques en memoria y mide el nivel en vivo."""

    MAX_S = 600  # tope de seguridad: 10 minutos por toma

    def __init__(self):
        self.device = None
        self.stream = None
        self.stream_sr = SR
        self._blocks = []
        self._frames = 0
        self.level_db = -120.0   # nivel del último bloque (para el vúmetro)
        self.peak_hold_db = -120.0  # pico más alto de la toma (para avisar si saturó)
        self.lock = threading.Lock()

    @property
    def recording(self):
        return self.stream is not None and self.stream.active

    def elapsed_s(self):
        return self._frames / self.stream_sr

    def start(self, device):
        self.stop()
        self.device = device
        self._blocks, self._frames = [], 0
        self.level_db = self.peak_hold_db = -120.0
        info = sd.query_devices(device, "input")
        try:
            sr = SR
            stream = sd.InputStream(device=device, samplerate=sr, channels=1, dtype="float32",
                                    callback=self._callback)
        except Exception:
            # Igual que en la salida: algunos micrófonos (WASAPI) solo aceptan su frecuencia.
            sr = int(info["default_samplerate"])
            stream = sd.InputStream(device=device, samplerate=sr, channels=1, dtype="float32",
                                    callback=self._callback)
        self.stream_sr = sr
        self.stream = stream
        stream.start()

    def _callback(self, indata, frames, time_info, status):
        block = indata[:, 0].copy()
        with self.lock:
            self._blocks.append(block)
            self._frames += frames
        peak = float(np.max(np.abs(block))) if len(block) else 0.0
        self.level_db = 20 * np.log10(peak + 1e-9)
        self.peak_hold_db = max(self.peak_hold_db, self.level_db)
        if self._frames >= self.MAX_S * self.stream_sr:
            raise sd.CallbackStop

    def stop(self):
        """Para la grabación y devuelve el audio a 48 kHz (o None si no había nada)."""
        if self.stream is None:
            return None
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass
        self.stream = None
        with self.lock:
            blocks, self._blocks = self._blocks, []
        if not blocks:
            return None
        audio = np.concatenate(blocks).astype(np.float64)
        return resample(audio, self.stream_sr, SR)
