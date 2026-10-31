"""
Audio Mixer — cargá audios (WhatsApp .opus, mp3, m4a, wav, videos...), elegí
fragmentos de cada uno, ordenalos y exportá un único audio continuo.
"""
import json
import math
import os
import subprocess
import sys
import threading

import imageio_ffmpeg
import numpy as np
import sounddevice as sd
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QColor, QFont, QIcon, QKeySequence, QPainter, QPalette,
                           QPen, QPixmap, QPolygonF)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
                               QDoubleSpinBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea,
                               QScrollBar, QSlider, QSpinBox, QSplitter, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

SR = 44100
CH = 2
BLOCK = 64                      # muestras por bloque del envolvente precalculado
TARGET_RMS = 10 ** (-18 / 20)   # nivel objetivo al "igualar volúmenes"
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
NOWIN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
PALETTE = ["#4fc3f7", "#ffb74d", "#81c784", "#f06292", "#ba68c8",
           "#fff176", "#4db6ac", "#e57373", "#9575cd", "#aed581"]
AUDIO_FILTER = ("Audio y video (*.opus *.ogg *.oga *.m4a *.mp3 *.wav *.aac *.flac *.wma *.amr "
                "*.3gp *.mp4 *.webm *.mkv *.mov *.caf *.aif *.aiff *.weba);;Todos los archivos (*.*)")

BG = QColor("#1b1d23")
PANEL = QColor("#23262e")
RULER = QColor("#2b2f39")
TEXT = QColor("#d7dae0")
DIM = QColor("#7d8590")


def fmt_time(sec, decimals=True):
    sec = max(0.0, sec)
    m = int(sec // 60)
    s = sec - m * 60
    return f"{m}:{s:05.2f}" if decimals else f"{m}:{int(s):02d}"


def decode(path):
    cmd = [FFMPEG, "-v", "error", "-nostdin", "-i", path, "-vn",
           "-f", "f32le", "-ac", str(CH), "-ar", str(SR), "-"]
    p = subprocess.run(cmd, capture_output=True, creationflags=NOWIN)
    if p.returncode != 0 or not p.stdout:
        raise RuntimeError(p.stderr.decode("utf-8", "replace").strip() or "No se pudo leer el audio")
    a = np.frombuffer(p.stdout, dtype=np.float32)
    return a[: len(a) // CH * CH].reshape(-1, CH).copy()


def export_audio(path, buf):
    ext = os.path.splitext(path)[1].lower()
    codecs = {
        ".wav": ["-c:a", "pcm_s16le"],
        ".mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
        ".m4a": ["-c:a", "aac", "-b:a", "192k"],
        ".ogg": ["-c:a", "libopus", "-b:a", "128k", "-ar", "48000"],
        ".opus": ["-c:a", "libopus", "-b:a", "128k", "-ar", "48000"],
        ".flac": ["-c:a", "flac"],
    }
    cmd = [FFMPEG, "-y", "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", str(CH), "-i", "-",
           *codecs.get(ext, []), path]
    p = subprocess.run(cmd, input=np.ascontiguousarray(buf, np.float32).tobytes(),
                       capture_output=True, creationflags=NOWIN)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode("utf-8", "replace").strip())


# ----------------------------------------------------------------------------- modelo

class AudioSource:
    _count = 0

    def __init__(self, path, data, name=None):
        self.path = path
        self.data = data
        self.name = name or os.path.splitext(os.path.basename(path))[0]
        self.color = QColor(PALETTE[AudioSource._count % len(PALETTE)])
        AudioSource._count += 1
        self.mono = data.mean(axis=1)
        pad = (-len(self.mono)) % BLOCK
        blocks = np.pad(self.mono, (0, pad)).reshape(-1, BLOCK)
        self.bmin = blocks.min(axis=1)
        self.bmax = blocks.max(axis=1)
        self._norm = None
        peak = float(np.abs(self.mono).max()) if len(self.mono) else 0.0
        self.vis = 0.95 / peak if peak > 1e-4 else 1.0   # escala solo visual

    @property
    def length(self):
        return len(self.data)

    @property
    def duration(self):
        return len(self.data) / SR

    def envelope(self, s0, s1, bins):
        """(mins, maxs) de largo `bins` para el rango de muestras [s0, s1)."""
        bins = max(1, int(bins))
        edges = np.linspace(s0, s1, bins + 1)
        if (s1 - s0) / bins >= BLOCK * 2:
            amin, amax, scale = self.bmin, self.bmax, BLOCK
        else:
            amin, amax, scale = self.mono, self.mono, 1
        n = len(amin)
        mins = np.zeros(bins, np.float32)
        maxs = np.zeros(bins, np.float32)
        if n == 0:
            return mins, maxs
        idx = np.clip((edges / scale).astype(np.int64), 0, n - 1)
        rmin = np.minimum.reduceat(amin, idx)[:-1]
        rmax = np.maximum.reduceat(amax, idx)[:-1]
        valid = (edges[:-1] < self.length) & (edges[1:] > 0)
        mins[valid] = rmin[valid]
        maxs[valid] = rmax[valid]
        return mins, maxs

    def norm_gain(self):
        """Ganancia para llevar la voz (partes activas) a un nivel común."""
        if self._norm is None:
            win = int(0.05 * SR)
            k = len(self.mono) // win
            g = 1.0
            if k > 0:
                rms = np.sqrt((self.mono[: k * win].reshape(k, win) ** 2).mean(axis=1))
                active = rms[rms >= rms.max() * 0.1]
                loud = float(np.sqrt((active ** 2).mean())) if len(active) else 0.0
                peak = float(np.abs(self.data).max())
                if loud > 1e-5:
                    g = TARGET_RMS / loud
                    if peak > 0:
                        g = min(g, 0.98 / peak)
            self._norm = g
        return self._norm


# ----------------------------------------------------------------------------- efectos

FX_DEFAULT = {
    "bass": 0.0, "mid": 0.0, "treble": 0.0,   # ecualizador (dB)
    "pitch": 0.0,                             # tono (semitonos)
    "speed": 1.0,                             # velocidad (1 = normal)
    "natural": False,                         # conservar timbre al cambiar el tono
    "denoise": 0,                             # quitar ruido (0 = no, 1..10)
    "rumble": False,                          # quitar zumbido / ruido grave
    "effect": "",                             # efecto especial (clave de EFFECTS)
    "reverse": False,                         # al revés
}

# (clave, nombre visible, filtro ffmpeg, segundos de cola que agrega)
EFFECTS = [
    ("", "Ninguno", "", 0),
    ("eco", "Eco", "aecho=0.8:0.8:280|560:0.45|0.25", 1.2),
    ("catedral", "Catedral (reverberación)",
     "aecho=0.8:0.85:43|79|127|181|241|307:0.5|0.42|0.35|0.28|0.2|0.14", 1.5),
    ("telefono", "Teléfono", "highpass=f=400,lowpass=f=3200,acrusher=bits=12:mix=0.3", 0),
    ("radio", "Radio vieja",
     "highpass=f=600,lowpass=f=2800,acrusher=bits=8:mode=log:aa=1:mix=0.25,volume=1.5", 0),
    ("megafono", "Megáfono", "highpass=f=700,lowpass=f=3500,volume=8,asoftclip=type=tanh,volume=0.6", 0),
    ("agua", "Bajo el agua", "lowpass=f=450,lowpass=f=450,vibrato=f=1.5:d=0.3,volume=2", 0),
    ("robot", "Robot", "afftfilt=real='hypot(re,im)':imag='0':win_size=512:overlap=0.75", 0),
    ("susurro", "Susurro",
     "afftfilt=real='hypot(re,im)*cos((random(0)*2-1)*2*3.14)'"
     ":imag='hypot(re,im)*sin((random(1)*2-1)*2*3.14)':win_size=128:overlap=0.8", 0),
    ("alien", "Alien", "vibrato=f=8:d=0.7,aphaser=type=s:speed=2:decay=0.5", 0),
    ("coro", "Coro", "chorus=0.6:0.9:50|60|40:0.4|0.32|0.3:0.25|0.4|0.3:2|2.3|1.3", 0),
    ("jet", "Avión (flanger)", "flanger=delay=2:depth=6:speed=0.4:regen=40", 0),
    ("phaser", "Phaser", "aphaser=type=t:speed=0.6:decay=0.6", 0),
    ("tremolo", "Trémolo", "tremolo=f=6:d=0.7", 0),
    ("vibrato", "Vibrato", "vibrato=f=6:d=0.5", 0),
    ("8bit", "Videojuego 8-bit", "acrusher=bits=4:samples=6:mode=lin:mix=1,lowpass=f=6000", 0),
]
EFFECT_BY_KEY = {e[0]: e for e in EFFECTS}

_has_rubberband = None
_fx_cache = {}


def has_rubberband():
    global _has_rubberband
    if _has_rubberband is None:
        p = subprocess.run([FFMPEG, "-hide_banner", "-filters"], capture_output=True, creationflags=NOWIN)
        _has_rubberband = b" rubberband " in p.stdout
    return _has_rubberband


def clean_fx(fx):
    """Completa con valores por defecto y descarta claves desconocidas."""
    out = dict(FX_DEFAULT)
    for k, v in (fx or {}).items():
        if k in out:
            out[k] = type(FX_DEFAULT[k])(v)
    if out["effect"] not in EFFECT_BY_KEY:
        out["effect"] = ""
    return out


def fx_active(fx):
    return any(fx[k] != v for k, v in FX_DEFAULT.items())


def fx_filter(fx):
    """Cadena de filtros ffmpeg para los efectos dados ("" = sin efectos)."""
    chain = []
    if fx["rumble"]:
        chain.append("highpass=f=90")
    if fx["denoise"]:
        chain.append(f"afftdn=nr={4 * fx['denoise']}:nf=-40:tn=1")
    if fx["bass"]:
        chain.append(f"bass=g={fx['bass']:g}:f=120")
    if fx["mid"]:
        chain.append(f"equalizer=f=1000:t=q:w=0.8:g={fx['mid']:g}")
    if fx["treble"]:
        chain.append(f"treble=g={fx['treble']:g}:f=4000")
    ratio = 2 ** (fx["pitch"] / 12)
    speed = fx["speed"]
    if ratio != 1 or speed != 1:
        if has_rubberband():
            form = ":formant=preserved" if fx["natural"] else ""
            chain.append(f"rubberband=tempo={speed:g}:pitch={ratio:g}{form}")
        else:
            if ratio != 1:
                chain.append(f"asetrate={SR * ratio:.0f},aresample={SR}")
            chain.append(f"atempo={speed / ratio:g}")
    key, _, flt, tail = EFFECT_BY_KEY[fx["effect"]]
    if flt:
        if tail:
            chain.append(f"apad=pad_dur={tail:g}")
        chain.append(flt)
    if fx["reverse"]:
        chain.append("areverse")
    if max(fx["bass"], fx["mid"], fx["treble"]) > 0 or key:
        chain.append("alimiter=limit=0.97:level=0")   # evita saturar con los refuerzos
    return ",".join(chain)


def apply_fx(seg, fx):
    flt = fx_filter(fx)
    if not flt or len(seg) == 0:
        return seg
    cmd = [FFMPEG, "-v", "error", "-nostdin", "-f", "f32le", "-ar", str(SR), "-ac", str(CH), "-i", "-",
           "-af", flt, "-f", "f32le", "-ar", str(SR), "-ac", str(CH), "-"]
    p = subprocess.run(cmd, input=np.ascontiguousarray(seg, np.float32).tobytes(),
                       capture_output=True, creationflags=NOWIN)
    if p.returncode != 0:
        raise RuntimeError("No se pudo aplicar el efecto:\n" +
                           p.stderr.decode("utf-8", "replace").strip())
    a = np.frombuffer(p.stdout, dtype=np.float32)
    return a[: len(a) // CH * CH].reshape(-1, CH).copy()


class Fragment:
    def __init__(self, src, a, b, gain_db=0.0, fx=None):
        self.src, self.a, self.b, self.gain_db = src, int(a), int(b), float(gain_db)
        self.fx = clean_fx(fx)

    @property
    def length(self):
        return self.b - self.a

    @property
    def has_fx(self):
        return fx_active(self.fx)

    @property
    def out_length(self):
        """Duración aproximada (en muestras) después de los efectos."""
        tail = EFFECT_BY_KEY[self.fx["effect"]][3]
        return int(self.length / self.fx["speed"] + tail * SR)

    def fx_key(self):
        return tuple(sorted(self.fx.items()))

    def is_cached(self):
        return not self.has_fx or (id(self.src), self.a, self.b, self.fx_key()) in _fx_cache

    def audio(self):
        """Audio del fragmento con sus efectos aplicados (cacheado; no modificar)."""
        seg = self.src.data[self.a:self.b]
        if not self.has_fx:
            return seg
        key = (id(self.src), self.a, self.b, self.fx_key())
        if key not in _fx_cache:
            if len(_fx_cache) >= 40:
                _fx_cache.pop(next(iter(_fx_cache)))
            _fx_cache[key] = apply_fx(seg, self.fx)
        return _fx_cache[key]


def render_mix(frags, gap_ms, fade_ms, normalize):
    gap = int(gap_ms * SR / 1000)
    fade = int(fade_ms * SR / 1000)
    parts, offsets, pos = [], [], 0
    for i, f in enumerate(frags):
        g = 10 ** (f.gain_db / 20) * (f.src.norm_gain() if normalize else 1.0)
        seg = f.audio() * np.float32(g)
        n = len(seg)
        fl = min(fade, n // 2)
        if fl > 0:
            ramp = np.linspace(0, 1, fl, dtype=np.float32)[:, None]
            seg[:fl] *= ramp
            seg[n - fl:] *= ramp[::-1]
        if i > 0 and gap > 0:
            parts.append(np.zeros((gap, CH), np.float32))
            pos += gap
        offsets.append((f, pos, n))
        parts.append(seg)
        pos += n
    buf = np.concatenate(parts) if parts else np.zeros((0, CH), np.float32)
    np.clip(buf, -1, 1, out=buf)
    return buf, offsets


class Player:
    def __init__(self):
        self.stream = None
        self.buf = None
        self.pos = 0
        self.ctx = None
        self._done = True

    def play(self, buf, ctx):
        self.stop()
        if len(buf) == 0:
            return
        self.buf = np.ascontiguousarray(buf, np.float32)
        self.pos = 0
        self.ctx = ctx
        self._done = False
        self.stream = sd.OutputStream(samplerate=SR, channels=CH, dtype="float32",
                                      callback=self._cb, finished_callback=self._fin)
        self.stream.start()

    def _cb(self, out, frames, t, status):
        chunk = self.buf[self.pos:self.pos + frames]
        n = len(chunk)
        out[:n] = chunk
        self.pos += n
        if n < frames:
            out[n:] = 0
            raise sd.CallbackStop

    def _fin(self):
        self._done = True

    @property
    def playing(self):
        return self.stream is not None and not self._done

    def heard_pos(self):
        lat = self.stream.latency if self.stream else 0
        return max(0, self.pos - int(lat * SR))

    def stop(self):
        if self.stream is not None:
            try:
                self.stream.abort()
                self.stream.close()
            except Exception:
                pass
        self.stream = None
        self.ctx = None
        self._done = True


class Loader(QObject):
    done = Signal(str, object, str)

    def load(self, path):
        def run():
            try:
                self.done.emit(path, decode(path), "")
            except Exception as ex:
                self.done.emit(path, None, str(ex))
        threading.Thread(target=run, daemon=True).start()


def wave_polygon(mins, maxs, x0, cy, amp):
    xs = x0 + np.arange(len(maxs)) + 0.5
    top = cy - np.maximum(maxs, mins + 1.0 / max(amp, 1)) * amp
    bot = cy - mins * amp
    pts = [QPointF(x, y) for x, y in zip(xs, top)] + \
          [QPointF(x, y) for x, y in zip(xs[::-1], bot[::-1])]
    return QPolygonF(pts)


# ----------------------------------------------------------------------------- editor de onda

class WaveformEditor(QWidget):
    viewChanged = Signal()
    selectionChanged = Signal()

    RULER_H = 22

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(180)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)
        self.src = None
        self.view_start = 0.0
        self.view_len = 1.0
        self.cursor_pos = 0
        self.sel = None          # (a, b) en muestras
        self.playhead = None
        self.overlays = []       # [(numero, a, b, resaltado)]
        self._drag = None
        self._anchor = 0
        self._press_x = 0

    # --- conversión
    def s2x(self, s):
        return (s - self.view_start) / self.view_len * self.width()

    def x2s(self, x):
        s = self.view_start + x / max(1, self.width()) * self.view_len
        return int(min(max(0, s), self.src.length if self.src else 0))

    # --- vista
    def set_source(self, src):
        self.src = src
        self.sel = None
        self.cursor_pos = 0
        self.playhead = None
        self.zoom_fit()

    def zoom_fit(self):
        if self.src:
            self.view_start, self.view_len = 0.0, float(max(1, self.src.length))
        self.viewChanged.emit()
        self.update()

    def min_view(self):
        return min(self.src.length, 0.05 * SR) if self.src else 1

    def clamp_view(self):
        if not self.src:
            return
        self.view_len = min(max(self.view_len, self.min_view()), max(1, self.src.length))
        self.view_start = min(max(0.0, self.view_start), self.src.length - self.view_len)

    def set_view_start(self, s):
        self.view_start = s
        self.clamp_view()
        self.update()

    def zoom(self, factor, at_x=None):
        if not self.src:
            return
        at_x = self.width() / 2 if at_x is None else at_x
        anchor = self.view_start + at_x / max(1, self.width()) * self.view_len
        self.view_len *= factor
        self.clamp_view()
        self.view_start = anchor - at_x / max(1, self.width()) * self.view_len
        self.clamp_view()
        self.viewChanged.emit()
        self.update()

    def show_range(self, a, b):
        margin = (b - a) * 0.25 + 0.2 * SR
        self.view_start = a - margin
        self.view_len = (b - a) + 2 * margin
        self.clamp_view()
        self.viewChanged.emit()
        self.update()

    def set_selection(self, a, b):
        self.sel = (a, b) if b > a else None
        self.cursor_pos = a
        self.selectionChanged.emit()
        self.update()

    def follow(self, s):
        """Desplaza la vista si el cabezal de reproducción se sale."""
        if s > self.view_start + self.view_len or s < self.view_start:
            self.view_start = s - self.view_len * 0.05
            self.clamp_view()
            self.viewChanged.emit()

    # --- eventos
    def wheelEvent(self, e):
        if not self.src:
            return
        d = e.angleDelta().y() or e.angleDelta().x()
        if e.modifiers() & Qt.ShiftModifier:
            self.set_view_start(self.view_start - d / 120 * self.view_len * 0.1)
            self.viewChanged.emit()
        else:
            self.zoom(0.8 if d > 0 else 1.25, e.position().x())

    def _edge_at(self, x):
        if self.sel:
            if abs(x - self.s2x(self.sel[0])) < 6:
                return "a"
            if abs(x - self.s2x(self.sel[1])) < 6:
                return "b"
        return None

    def mousePressEvent(self, e):
        if not self.src or e.button() != Qt.LeftButton:
            return
        x = e.position().x()
        edge = self._edge_at(x)
        if edge:
            self._drag = "edge"
            self._anchor = self.sel[1] if edge == "a" else self.sel[0]
        else:
            self._drag = "new"
            self._anchor = self.x2s(x)
            self._press_x = x
            self.cursor_pos = self._anchor
            self.sel = None
            self.selectionChanged.emit()
        self.update()

    def mouseMoveEvent(self, e):
        if not self.src:
            return
        x = e.position().x()
        if self._drag:
            if self._drag == "edge" or abs(x - self._press_x) > 3:
                s = self.x2s(x)
                a, b = sorted((self._anchor, s))
                self.sel = (a, b) if b > a else None
                self.cursor_pos = a
                self.selectionChanged.emit()
                self.update()
        else:
            self.setCursor(Qt.SizeHorCursor if self._edge_at(x) else Qt.IBeamCursor)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), BG)
        if not self.src:
            p.setPen(DIM)
            p.drawText(self.rect(), Qt.AlignCenter,
                       "Arrastrá audios a la ventana o usá «＋ Agregar audios».\n"
                       "Después elegí uno en la lista y marcá con el mouse la parte que querés.")
            return
        rh = self.RULER_H
        p.fillRect(0, 0, w, rh, RULER)

        # regla de tiempo
        sec_per_px = self.view_len / SR / max(1, w)
        target = sec_per_px * 90
        steps = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600]
        step = next((s for s in steps if s >= target), 600)
        t0 = self.view_start / SR
        t1 = (self.view_start + self.view_len) / SR
        t = math.floor(t0 / step) * step
        f = QFont(self.font())
        f.setPointSizeF(8)
        p.setFont(f)
        while t <= t1 + step:
            x = self.s2x(t * SR)
            p.setPen(QColor(255, 255, 255, 18))
            p.drawLine(QPointF(x, rh), QPointF(x, h))
            p.setPen(DIM)
            p.drawLine(QPointF(x, rh - 6), QPointF(x, rh))
            p.drawText(QPointF(x + 3, rh - 8), fmt_time(t, step < 1))
            t += step

        # fragmentos ya usados de este audio
        for num, a, b, hi in self.overlays:
            x0, x1 = self.s2x(a), self.s2x(b)
            c = QColor(self.src.color)
            c.setAlpha(70 if hi else 28)
            p.fillRect(QRectF(x0, rh, x1 - x0, h - rh), c)
            c.setAlpha(230)
            p.fillRect(QRectF(x0, h - 6, x1 - x0, 6), c)
            p.setPen(c)
            p.drawText(QPointF(x0 + 4, h - 10), f"#{num}")

        # selección
        if self.sel:
            x0, x1 = self.s2x(self.sel[0]), self.s2x(self.sel[1])
            p.fillRect(QRectF(x0, rh, x1 - x0, h - rh), QColor(255, 255, 255, 38))
            p.setPen(QPen(QColor(255, 255, 255, 200), 1.5))
            p.drawLine(QPointF(x0, rh), QPointF(x0, h))
            p.drawLine(QPointF(x1, rh), QPointF(x1, h))
            p.fillRect(QRectF(x0, 0, x1 - x0, rh), QColor(255, 255, 255, 30))

        # onda
        cy = rh + (h - rh) / 2
        amp = (h - rh) / 2 - 6
        mins, maxs = self.src.envelope(self.view_start, self.view_start + self.view_len, w)
        p.setPen(Qt.NoPen)
        p.setBrush(self.src.color)
        p.drawPolygon(wave_polygon(mins * self.src.vis, maxs * self.src.vis, 0, cy, amp))
        p.setPen(QColor(255, 255, 255, 25))
        p.drawLine(QPointF(0, cy), QPointF(w, cy))

        # cursor y cabezal
        p.setPen(QPen(QColor("#ffd54f"), 1))
        x = self.s2x(self.cursor_pos)
        p.drawLine(QPointF(x, 0), QPointF(x, h))
        if self.playhead is not None:
            p.setPen(QPen(QColor("#ffffff"), 2))
            x = self.s2x(self.playhead)
            p.drawLine(QPointF(x, 0), QPointF(x, h))


# ----------------------------------------------------------------------------- secuencia (mezcla)

class SequenceView(QWidget):
    selected = Signal(int)
    activated = Signal(int)
    moved = Signal(int, int)
    deleteRequested = Signal()
    contextRequested = Signal(int, object)
    editStarted = Signal()       # antes de recortar un borde (para deshacer)
    edited = Signal()            # mientras se recorta un borde
    cutChanged = Signal()

    MIN_W = 80
    GAP = 4
    EDGE = 7
    MIN_LEN = int(0.05 * SR)

    def __init__(self, scroll):
        super().__init__()
        self.scroll = scroll
        self.frags = []
        self.rects = []
        self.sel = -1
        self.playhead = None     # (indice, fracción)
        self.cut = None          # (fragmento, muestra) = punto de corte marcado
        self.zoom = 1.0
        self.scale = 1.0         # px por muestra
        self._frozen = None
        self._press = None
        self._dragging = False
        self._drop = None
        self._trim = None        # (indice, "a"|"b", x inicial, valor inicial)
        self.setMinimumHeight(110)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)
        scroll.horizontalScrollBar().valueChanged.connect(self.update)

    def set_frags(self, frags):
        self.frags = frags
        if self.sel >= len(frags):
            self.sel = len(frags) - 1
        if self.cut and self.cut[0] not in frags:
            self.cut = None
        self.relayout()

    def relayout(self):
        vw = self.scroll.viewport().width() - 16
        total = sum(f.length for f in self.frags) or 1
        if self._frozen is not None:
            self.scale = self._frozen
        else:
            self.scale = max(vw - self.GAP * len(self.frags), 50) / total * self.zoom
        widths = [max(self.MIN_W, f.length * self.scale) for f in self.frags]
        need = int(sum(widths) + self.GAP * len(widths) + 16)
        self.setMinimumWidth(need if need > vw + 16 else 0)
        h = self.height()
        x = 8.0
        self.rects = []
        for wd in widths:
            self.rects.append(QRectF(x, 8, wd, h - 16))
            x += wd + self.GAP
        self.update()

    def resizeEvent(self, e):
        self.relayout()

    def set_zoom(self, z, at_x=None):
        z = min(max(1.0, z), 400.0)
        if z == self.zoom:
            return
        bar = self.scroll.horizontalScrollBar()
        vx = (at_x - bar.value()) if at_x is not None else self.scroll.viewport().width() / 2
        cx = bar.value() + vx
        old_w = max(1.0, self.rects[-1].right()) if self.rects else 1.0
        self.zoom = z
        self.relayout()
        new_w = max(1.0, self.rects[-1].right()) if self.rects else 1.0
        target = int(cx * new_w / old_w - vx)
        QTimer.singleShot(0, lambda: bar.setValue(target))

    def wheelEvent(self, e):
        d = e.angleDelta().y() or e.angleDelta().x()
        if e.modifiers() & Qt.ShiftModifier:
            bar = self.scroll.horizontalScrollBar()
            bar.setValue(bar.value() - int(d / 120 * self.scroll.viewport().width() * 0.15))
        elif d:
            self.set_zoom(self.zoom * (1.25 if d > 0 else 0.8), e.position().x())
        e.accept()

    def index_at(self, x):
        for i, r in enumerate(self.rects):
            if r.left() <= x <= r.right() + self.GAP:
                return i
        return -1

    def edge_at(self, x):
        for i, r in enumerate(self.rects):
            if r.width() <= 3 * self.EDGE:
                continue
            if abs(x - r.left()) <= self.EDGE:
                return i, "a"
            if abs(x - r.right()) <= self.EDGE:
                return i, "b"
        return None

    def sample_at(self, i, x):
        f, r = self.frags[i], self.rects[i]
        frac = min(max(0.0, (x - r.left()) / max(1.0, r.width())), 1.0)
        return int(f.a + frac * f.length)

    def drop_index(self, x):
        for i, r in enumerate(self.rects):
            if x < r.center().x():
                return i
        return len(self.rects)

    def mousePressEvent(self, e):
        x = e.position().x()
        i = self.index_at(x)
        if e.button() == Qt.RightButton:
            if i >= 0:
                self.sel = i
                if x <= self.rects[i].right():
                    self.cut = (self.frags[i], self.sample_at(i, x))
                    self.cutChanged.emit()
                self.selected.emit(i)
                self.update()
            self.contextRequested.emit(i, e.globalPosition().toPoint())
            return
        if e.button() != Qt.LeftButton:
            return
        edge = self.edge_at(x)
        if edge:
            j, side = edge
            f = self.frags[j]
            self.sel = j
            self.selected.emit(j)
            self.editStarted.emit()
            self._trim = (j, side, x, f.a if side == "a" else f.b)
            self._frozen = self.scale
            return
        self.sel = i
        self.selected.emit(i)
        self._press = (i, x)
        self._dragging = False
        self.update()

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if self._trim:
            j, side, x0, v0 = self._trim
            f = self.frags[j]
            v = int(v0 + (x - x0) / max(self.scale, 1e-9))
            if side == "a":
                f.a = min(max(0, v), f.b - self.MIN_LEN)
            else:
                f.b = max(min(f.src.length, v), f.a + self.MIN_LEN)
            if self.cut and self.cut[0] is f and not (f.a < self.cut[1] < f.b):
                self.cut = None
            self.relayout()
            self.edited.emit()
            return
        if self._press and self._press[0] >= 0:
            if abs(x - self._press[1]) > 6:
                self._dragging = True
                self.setCursor(Qt.ClosedHandCursor)
                self._drop = self.drop_index(x)
                self.update()
            return
        if not e.buttons():
            self.setCursor(Qt.SizeHorCursor if self.edge_at(x) else Qt.ArrowCursor)

    def mouseReleaseEvent(self, e):
        if self._trim:
            self._trim = None
            self._frozen = None
            self.relayout()
            self.edited.emit()
            return
        if self._press:
            i, x0 = self._press
            if self._dragging:
                dst = self._drop
                if dst is not None and dst not in (i, i + 1):
                    self.moved.emit(i, dst if dst < i else dst - 1)
            elif i >= 0 and x0 <= self.rects[i].right():
                self.cut = (self.frags[i], self.sample_at(i, x0))
                self.cutChanged.emit()
            else:
                self.cut = None
                self.cutChanged.emit()
        self._press = None
        self._dragging = False
        self._drop = None
        self.unsetCursor()
        self.update()

    def mouseDoubleClickEvent(self, e):
        i = self.index_at(e.position().x())
        if i >= 0:
            self.activated.emit(i)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.deleteRequested.emit()
        elif e.key() in (Qt.Key_Left, Qt.Key_Right) and self.sel >= 0:
            d = -1 if e.key() == Qt.Key_Left else 1
            if e.modifiers() & Qt.ControlModifier:
                j = self.sel + d
                if 0 <= j < len(self.frags):
                    self.moved.emit(self.sel, j)
            else:
                self.sel = min(max(0, self.sel + d), len(self.frags) - 1)
                self.selected.emit(self.sel)
                self.update()
        else:
            super().keyPressEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), PANEL)
        if not self.frags:
            p.setPen(DIM)
            p.drawText(self.rect(), Qt.AlignCenter,
                       "Acá se arma el audio final. Marcá una parte arriba y apretá Enter "
                       "(o «＋ Agregar a la mezcla»).")
            return
        f = QFont(self.font())
        f.setPointSizeF(8.5)
        p.setFont(f)
        fm = p.fontMetrics()
        vis_l = self.scroll.horizontalScrollBar().value()
        vis_r = vis_l + self.scroll.viewport().width()
        for i, (fr, r) in enumerate(zip(self.frags, self.rects)):
            if r.right() < vis_l or r.left() > vis_r:
                continue
            c = fr.src.color
            p.setPen(QPen(QColor("#ffffff") if i == self.sel else c, 2 if i == self.sel else 1))
            p.setBrush(c.darker(330))
            p.drawRoundedRect(r, 6, 6)
            wr = r.adjusted(3, 18, -3, -16)
            # sólo se dibuja la parte visible de la onda (con mucho zoom el bloque es enorme)
            x0 = max(wr.left(), vis_l)
            x1 = min(wr.right(), vis_r)
            if x1 > x0:
                s0 = fr.a + (x0 - wr.left()) / max(1.0, wr.width()) * fr.length
                s1 = fr.a + (x1 - wr.left()) / max(1.0, wr.width()) * fr.length
                mins, maxs = fr.src.envelope(s0, s1, int(x1 - x0))
                gain = 10 ** (fr.gain_db / 20) * fr.src.vis
                p.setPen(Qt.NoPen)
                p.setBrush(c)
                p.drawPolygon(wave_polygon(np.clip(mins * gain, -1, 1), np.clip(maxs * gain, -1, 1),
                                           x0, wr.center().y(), wr.height() / 2))
            tx = max(r.left(), vis_l) + 6
            tw = int(r.right() - tx - 4)
            if tw > 10:
                p.setPen(TEXT)
                p.drawText(QPointF(tx, r.top() + 13),
                           fm.elidedText(f"{i + 1}. {fr.src.name}", Qt.ElideRight, tw))
                p.setPen(DIM)
                info = f"{fr.out_length / SR:.2f} s" + (f"  {fr.gain_db:+.0f} dB" if fr.gain_db else "") \
                    + ("  🎛 FX" if fr.has_fx else "")
                p.drawText(QPointF(tx, r.bottom() - 4), fm.elidedText(info, Qt.ElideRight, tw))
        if self.cut and self.cut[0] in self.frags:
            fr, smp = self.cut
            r = self.rects[self.frags.index(fr)]
            x = r.left() + (smp - fr.a) / max(1, fr.length) * r.width()
            p.setPen(QPen(QColor("#ffd54f"), 2, Qt.DashLine))
            p.drawLine(QPointF(x, 2), QPointF(x, self.height() - 2))
            p.setPen(QColor("#ffd54f"))
            p.drawText(QPointF(x + 4, self.height() - 3), "✂")
        if self._dragging and self._drop is not None:
            x = (self.rects[self._drop].left() - self.GAP / 2 if self._drop < len(self.rects)
                 else self.rects[-1].right() + self.GAP / 2)
            p.setPen(QPen(QColor("#ffd54f"), 3))
            p.drawLine(QPointF(x, 2), QPointF(x, self.height() - 2))
        if self.playhead is not None:
            i, frac = self.playhead
            if 0 <= i < len(self.rects):
                r = self.rects[i]
                x = r.left() + frac * r.width()
                p.setPen(QPen(QColor("#ffffff"), 2))
                p.drawLine(QPointF(x, 2), QPointF(x, self.height() - 2))


# ----------------------------------------------------------------------------- ventana principal

def make_button(text, tip=None):
    b = QPushButton(text)
    b.setFocusPolicy(Qt.NoFocus)
    if tip:
        b.setToolTip(tip)
    return b


def color_icon(c):
    pm = QPixmap(14, 14)
    pm.fill(c)
    return QIcon(pm)


class FxDialog(QDialog):
    """Ecualizador y efectos de un fragmento, con escucha de prueba."""
    applyAll = Signal(dict)

    # (clave, etiqueta, mínimo, máximo, divisor del slider, formato)
    SLIDERS = [
        ("bass", "Graves", -15, 15, 1, "{:+.0f} dB"),
        ("mid", "Medios", -15, 15, 1, "{:+.0f} dB"),
        ("treble", "Agudos", -15, 15, 1, "{:+.0f} dB"),
        ("pitch", "Tono", -12, 12, 1, "{:+.0f} semitonos"),
        ("speed", "Velocidad", 50, 200, 100, "{:.0%}"),
        ("denoise", "Quitar ruido", 0, 10, 1, "{:.0f}"),
    ]
    PRESETS = [
        ("Voz más clara", dict(rumble=True, denoise=4, mid=2, treble=4)),
        ("Más grave", dict(pitch=-5)),
        ("Más agudo", dict(pitch=5)),
        ("Monstruo", dict(pitch=-9, bass=6, effect="catedral")),
        ("Ardilla", dict(pitch=9, speed=1.25)),
        ("Cámara lenta", dict(speed=0.6, pitch=-3)),
    ]

    def __init__(self, parent, frag, preview):
        super().__init__(parent)
        self.setWindowTitle(f"Efectos — {frag.src.name}")
        self.frag = frag
        self.preview = preview          # función(fx) que reproduce el fragmento con esos efectos
        self.fx = dict(frag.fx)
        self.sliders = {}
        self._loading = True
        lay = QVBoxLayout(self)

        pre = QHBoxLayout()
        pre.addWidget(QLabel("Rápidos:"))
        for name, fx in self.PRESETS:
            b = make_button(name)
            b.clicked.connect(lambda _=False, fx=fx: self.set_fx(fx))
            pre.addWidget(b)
        pre.addStretch()
        lay.addLayout(pre)

        groups = {"Ecualizador": ("bass", "mid", "treble"),
                  "Tono y velocidad": ("pitch", "speed"),
                  "Limpieza": ("denoise",)}
        spec = {s[0]: s for s in self.SLIDERS}
        for title, keys in groups.items():
            box = QGroupBox(title)
            grid = QGridLayout(box)
            for r, k in enumerate(keys):
                _, label, lo, hi, div, fmt = spec[k]
                sl = QSlider(Qt.Horizontal)
                sl.setRange(lo, hi)
                sl.setMinimumWidth(320)
                val = QLabel()
                val.setMinimumWidth(110)
                sl.valueChanged.connect(lambda v, k=k: self.on_slider(k, v))
                grid.addWidget(QLabel(label), r, 0)
                grid.addWidget(sl, r, 1)
                grid.addWidget(val, r, 2)
                self.sliders[k] = (sl, val, div, fmt)
            r = len(keys)
            if title == "Tono y velocidad":
                self.natural = QCheckBox("Voz natural (cambia el tono sin efecto «ardilla/monstruo»)")
                self.natural.toggled.connect(lambda v: self.on_value("natural", v))
                grid.addWidget(self.natural, r, 1, 1, 2)
            elif title == "Limpieza":
                self.rumble = QCheckBox("Quitar zumbido y ruidos graves (viento, golpes, motor)")
                self.rumble.toggled.connect(lambda v: self.on_value("rumble", v))
                grid.addWidget(self.rumble, r, 1, 1, 2)
            lay.addWidget(box)

        box = QGroupBox("Efectos especiales")
        row = QHBoxLayout(box)
        self.effect = QComboBox()
        for key, name, _, _ in EFFECTS:
            self.effect.addItem(name, key)
        self.effect.currentIndexChanged.connect(
            lambda i: self.on_value("effect", self.effect.itemData(i)))
        row.addWidget(self.effect, 1)
        self.reverse = QCheckBox("Al revés")
        self.reverse.toggled.connect(lambda v: self.on_value("reverse", v))
        row.addWidget(self.reverse)
        lay.addWidget(box)

        bot = QHBoxLayout()
        self.btn_play = make_button("▶ Probar", "Escuchar el fragmento con estos efectos "
                                    "(si está sonando, se actualiza al mover los controles)")
        self.btn_play.clicked.connect(self.play)
        bot.addWidget(self.btn_play)
        b = make_button("■ Detener")
        b.clicked.connect(parent.stop)
        bot.addWidget(b)
        b = make_button("↺ Sin efectos")
        b.clicked.connect(lambda: self.set_fx({}))
        bot.addWidget(b)
        bot.addStretch()
        b = make_button("Aplicar a todos", "Usar estos mismos efectos en todos los fragmentos de la mezcla")
        b.clicked.connect(lambda: (self.applyAll.emit(dict(self.fx)), self.accept()))
        bot.addWidget(b)
        b = make_button("Aceptar")
        b.setObjectName("primary")
        b.clicked.connect(self.accept)
        bot.addWidget(b)
        b = make_button("Cancelar")
        b.clicked.connect(self.reject)
        bot.addWidget(b)
        lay.addLayout(bot)

        self.replay = QTimer(self)
        self.replay.setSingleShot(True)
        self.replay.setInterval(350)
        self.replay.timeout.connect(self.play)
        self.load_ui()

    def set_fx(self, fx):
        self.fx = clean_fx(fx)
        self.load_ui()
        self.changed()

    def load_ui(self):
        self._loading = True
        for k, (sl, val, div, fmt) in self.sliders.items():
            sl.setValue(round(self.fx[k] * div))
            val.setText(fmt.format(self.fx[k]))
        self.natural.setChecked(self.fx["natural"])
        self.rumble.setChecked(self.fx["rumble"])
        self.reverse.setChecked(self.fx["reverse"])
        self.effect.setCurrentIndex(max(0, self.effect.findData(self.fx["effect"])))
        self._loading = False

    def on_slider(self, k, v):
        if self._loading:
            return
        sl, val, div, fmt = self.sliders[k]
        self.fx[k] = type(FX_DEFAULT[k])(v / div)
        val.setText(fmt.format(self.fx[k]))
        self.on_value(k, self.fx[k])

    def on_value(self, k, v):
        if self._loading:
            return
        self.fx[k] = v
        self.changed()

    def changed(self):
        if self.parent().player.playing:
            self.replay.start()

    def play(self):
        self.preview(dict(self.fx))



# ----------------------------------------------------------------------------- puente con VERA

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERA_DIR = os.path.join(ROOT_DIR, "vera")
VERA_FRAG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fragmentos_vera")
_vera = None


def vera_core():
    """Carga el núcleo de VERA (../vera) la primera vez que se usa.

    Si VERA o sus librerías no están, el Mixer funciona igual: solo falla esta opción.
    """
    global _vera
    if _vera is None:
        if not os.path.isdir(VERA_DIR):
            raise RuntimeError(f"No encuentro la carpeta de VERA:\n{VERA_DIR}")
        if VERA_DIR not in sys.path:
            sys.path.insert(0, VERA_DIR)
        import preset_store
        from engines.base import resample
        _vera = (preset_store, resample)
    return _vera


def vera_transform(seg, preset):
    """Pasa un fragmento estéreo 44.1 kHz por VERA (que trabaja en mono 48 kHz).

    Devuelve audio estéreo 44.1 kHz con exactamente el mismo largo.
    """
    preset_store, resample = vera_core()
    mono = seg.mean(axis=1).astype(np.float64)
    y, _ = preset_store.transform(resample(mono, SR, 48000), 48000, preset)
    y = resample(y, 48000, SR)[: len(mono)]
    y = np.pad(y, (0, len(mono) - len(y))).astype(np.float32)
    return np.repeat(y[:, None], CH, axis=1)


class VeraWorker(QObject):
    done = Signal(object, object, str, str)   # fragmento, audio, ruta, error

    def run(self, frag, preset, path):
        def work():
            try:
                import soundfile as sf
                data = vera_transform(frag.src.data[frag.a:frag.b], preset)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                sf.write(path, data, SR, subtype="PCM_24")
                self.done.emit(frag, data, path, "")
            except Exception as ex:
                self.done.emit(frag, None, path, f"{type(ex).__name__}: {ex}")
        threading.Thread(target=work, daemon=True).start()


def launch_vera():
    script = os.path.join(VERA_DIR, "app.py")
    if not os.path.isfile(script):
        raise RuntimeError(f"No encuentro VERA en {script}")
    exe = sys.executable
    if os.name == "nt" and os.path.isfile(os.path.join(os.path.dirname(exe), "pythonw.exe")):
        exe = os.path.join(os.path.dirname(exe), "pythonw.exe")
    subprocess.Popen([exe, script], cwd=VERA_DIR, creationflags=NOWIN)


class VeraDialog(QDialog):
    """Elegir con qué preset de VERA transformar el fragmento."""

    def __init__(self, parent, names):
        super().__init__(parent)
        self.setWindowTitle("Voz VERA")
        v = QVBoxLayout(self)
        v.addWidget(QLabel("Transforma la voz del fragmento con un preset de VERA.\n"
                           "Se crea un audio nuevo «(VERA)» y el fragmento pasa a usarlo;\n"
                           "el original queda intacto y se puede deshacer con Ctrl+Z."))
        row = QHBoxLayout()
        row.addWidget(QLabel("Preset:"))
        self.combo = QComboBox()
        self.combo.addItems(names)
        row.addWidget(self.combo, 1)
        v.addLayout(row)
        btns = QHBoxLayout()
        btns.addStretch(1)
        ok, cancel = QPushButton("Transformar"), QPushButton("Cancelar")
        ok.setObjectName("primary")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        v.addLayout(btns)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Audio Mixer")
        self.resize(1300, 800)
        self.setAcceptDrops(True)
        self.sources = []
        self.frags = []
        self.undo_stack, self.redo_stack = [], []
        self.project_path = None
        self.player = Player()
        self.loader = Loader()
        self.loader.done.connect(self.on_loaded)
        self.vera = VeraWorker()
        self.vera.done.connect(self.on_vera_done)
        self.vera_busy = False
        self.pending = 0
        self.mix_offsets = []

        # --- panel izquierdo: lista de audios
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(8, 8, 4, 8)
        title = QLabel("Audios")
        title.setObjectName("h")
        lv.addWidget(title)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Nombre", "Duración"])
        self.tree.setRootIsDecorated(False)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tree.setColumnWidth(0, 200)
        self.tree.currentItemChanged.connect(self.on_source_selected)
        self.tree.itemDoubleClicked.connect(lambda it, col: self.tree.editItem(it, 0))
        self.tree.itemChanged.connect(self.on_source_renamed)
        lv.addWidget(self.tree)
        row = QHBoxLayout()
        b = make_button("＋ Agregar audios")
        b.clicked.connect(self.add_files_dialog)
        row.addWidget(b)
        b = make_button("✕ Quitar")
        b.clicked.connect(self.remove_source)
        row.addWidget(b)
        lv.addLayout(row)
        hint = QLabel("Doble clic en un nombre para renombrarlo (ej: «Juan»).")
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        lv.addWidget(hint)

        # --- editor
        ed = QWidget()
        ev = QVBoxLayout(ed)
        ev.setContentsMargins(4, 8, 8, 4)
        self.src_title = QLabel("—")
        self.src_title.setObjectName("h")
        ev.addWidget(self.src_title)
        self.editor = WaveformEditor()
        self.editor.viewChanged.connect(self.sync_scrollbar)
        self.editor.selectionChanged.connect(self.update_sel_label)
        ev.addWidget(self.editor, 1)
        self.hscroll = QScrollBar(Qt.Horizontal)
        self.hscroll.valueChanged.connect(lambda v: self.editor.set_view_start(v * SR / 1000))
        ev.addWidget(self.hscroll)
        tr = QHBoxLayout()
        self.btn_play = make_button("▶ Escuchar  (Espacio)", "Reproduce la selección, o desde el cursor")
        self.btn_play.clicked.connect(self.toggle_play_editor)
        tr.addWidget(self.btn_play)
        b = make_button("■ Detener  (Esc)")
        b.clicked.connect(self.stop)
        tr.addWidget(b)
        self.btn_add = make_button("＋ Agregar a la mezcla  (Enter)")
        self.btn_add.setObjectName("primary")
        self.btn_add.clicked.connect(self.add_fragment)
        tr.addWidget(self.btn_add)
        self.btn_replace = make_button("⟳ Reemplazar fragmento elegido",
                                       "Cambia el fragmento seleccionado abajo por la selección actual")
        self.btn_replace.clicked.connect(self.replace_fragment)
        tr.addWidget(self.btn_replace)
        tr.addSpacing(12)
        self.sel_label = QLabel("")
        tr.addWidget(self.sel_label)
        tr.addStretch()
        for txt, fn in (("－", lambda: self.editor.zoom(1.5)), ("＋", lambda: self.editor.zoom(1 / 1.5)),
                        ("Ver todo", self.editor.zoom_fit)):
            b = make_button(txt, "Zoom (también con la rueda del mouse; Shift+rueda para desplazar)")
            b.clicked.connect(fn)
            tr.addWidget(b)
        ev.addLayout(tr)

        # --- secuencia
        sq = QWidget()
        sv = QVBoxLayout(sq)
        sv.setContentsMargins(4, 4, 8, 8)
        hr = QHBoxLayout()
        t = QLabel("Mezcla final")
        t.setObjectName("h")
        hr.addWidget(t)
        t = QLabel("arrastrá los bloques para ordenar · clic = marcar punto de corte · "
                   "arrastrá los bordes para recortar · doble clic = editar · Supr = borrar")
        t.setObjectName("hint")
        hr.addWidget(t)
        hr.addStretch()
        self.seq_scroll = QScrollArea()
        self.seq_scroll.setWidgetResizable(True)
        self.seq_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.seq_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.seq = SequenceView(self.seq_scroll)
        self.seq_scroll.setWidget(self.seq)
        self.btn_cut = make_button("✂ Cortar aquí  (S)",
                                   "Parte el bloque en dos en la línea amarilla (hacé clic en un bloque para marcarla)")
        self.btn_cut.clicked.connect(self.split_fragment)
        hr.addWidget(self.btn_cut)
        for txt, fn in (("－", lambda: self.seq.set_zoom(self.seq.zoom / 1.5)),
                        ("＋", lambda: self.seq.set_zoom(self.seq.zoom * 1.5)),
                        ("Ver todo", lambda: self.seq.set_zoom(1.0))):
            b = make_button(txt, "Zoom de la mezcla (también con la rueda del mouse; Shift+rueda para desplazar)")
            b.clicked.connect(fn)
            hr.addWidget(b)
        sv.addLayout(hr)
        self.seq.selected.connect(self.on_frag_selected)
        self.seq.activated.connect(self.edit_fragment)
        self.seq.moved.connect(self.move_fragment)
        self.seq.deleteRequested.connect(self.delete_fragment)
        self.seq.contextRequested.connect(self.frag_menu)
        self.seq.editStarted.connect(self.push_undo)
        self.seq.edited.connect(self.on_frag_trimmed)
        self.seq.cutChanged.connect(self.update_cut_state)
        sv.addWidget(self.seq_scroll, 1)
        cr = QHBoxLayout()
        self.btn_mix = make_button("▶ Escuchar mezcla  (Ctrl+Espacio)")
        self.btn_mix.setObjectName("primary")
        self.btn_mix.clicked.connect(self.toggle_play_mix)
        cr.addWidget(self.btn_mix)
        b = make_button("■ Detener")
        b.clicked.connect(self.stop)
        cr.addWidget(b)
        cr.addSpacing(16)
        cr.addWidget(QLabel("Volumen del fragmento:"))
        self.gain_spin = QDoubleSpinBox()
        self.gain_spin.setRange(-30, 30)
        self.gain_spin.setSingleStep(1)
        self.gain_spin.setSuffix(" dB")
        self.gain_spin.setEnabled(False)
        self.gain_spin.valueChanged.connect(self.on_gain_changed)
        cr.addWidget(self.gain_spin)
        self.btn_fx = make_button("🎛 Efectos…  (E)", "Ecualizador, tono, velocidad, quitar ruido y "
                                  "efectos especiales para el fragmento elegido")
        self.btn_fx.clicked.connect(lambda: self.open_fx(self.seq.sel))
        cr.addWidget(self.btn_fx)
        cr.addSpacing(16)
        cr.addWidget(QLabel("Pausa entre partes:"))
        self.gap_spin = QSpinBox()
        self.gap_spin.setRange(0, 5000)
        self.gap_spin.setSingleStep(50)
        self.gap_spin.setSuffix(" ms")
        cr.addWidget(self.gap_spin)
        cr.addWidget(QLabel("Fundido:"))
        self.fade_spin = QSpinBox()
        self.fade_spin.setRange(0, 1000)
        self.fade_spin.setValue(15)
        self.fade_spin.setSuffix(" ms")
        self.fade_spin.setToolTip("Suaviza los cortes para que no hagan «clic»")
        cr.addWidget(self.fade_spin)
        self.norm_check = QCheckBox("Igualar volúmenes")
        self.norm_check.setChecked(True)
        self.norm_check.setToolTip("Lleva cada grabación a un volumen parecido (útil si cada persona grabó distinto)")
        cr.addWidget(self.norm_check)
        cr.addStretch()
        self.total_label = QLabel("")
        cr.addWidget(self.total_label)
        b = make_button("💾 Exportar audio…")
        b.setObjectName("primary")
        b.clicked.connect(self.export_dialog)
        cr.addWidget(b)
        sv.addLayout(cr)

        right = QSplitter(Qt.Vertical)
        right.addWidget(ed)
        right.addWidget(sq)
        right.setSizes([520, 230])
        main = QSplitter(Qt.Horizontal)
        main.addWidget(left)
        main.addWidget(right)
        main.setSizes([300, 1000])
        self.setCentralWidget(main)

        self.build_menu()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(30)
        self.refresh()
        self.statusBar().showMessage("Listo. Arrastrá audios a la ventana para empezar.")

    # ------------------------------------------------------------------ menús / atajos
    def build_menu(self):
        def act(menu, text, fn, key=None):
            a = QAction(text, self)
            if key:
                a.setShortcut(QKeySequence(key))
            a.triggered.connect(fn)
            if menu is not None:
                menu.addAction(a)
            self.addAction(a)
            return a

        m = self.menuBar().addMenu("&Archivo")
        act(m, "Agregar audios…", self.add_files_dialog, "Ctrl+O")
        m.addSeparator()
        act(m, "Abrir proyecto…", self.open_project, "Ctrl+Shift+O")
        act(m, "Guardar proyecto", self.save_project, "Ctrl+S")
        act(m, "Guardar proyecto como…", lambda: self.save_project(True), "Ctrl+Shift+S")
        m.addSeparator()
        act(m, "Exportar audio…", self.export_dialog, "Ctrl+E")
        m.addSeparator()
        act(m, "Salir", self.close, "Ctrl+Q")
        m = self.menuBar().addMenu("&Editar")
        act(m, "Deshacer", self.undo, "Ctrl+Z")
        act(m, "Rehacer", self.redo, "Ctrl+Y")
        m.addSeparator()
        act(m, "Efectos del fragmento…", lambda: self.open_fx(self.seq.sel), "E")
        act(m, "Voz VERA del fragmento…", lambda: self.vera_fragment(self.seq.sel), "V")
        m = self.menuBar().addMenu("E&studio")
        act(m, "Abrir VERA (transformador de voz)", self.open_vera)
        m = self.menuBar().addMenu("A&yuda")
        act(m, "Cómo se usa", self.show_help, "F1")
        act(None, "play", self.toggle_play_editor, "Space")
        act(None, "playmix", self.toggle_play_mix, "Ctrl+Space")
        act(None, "add", self.add_fragment, "Return")
        act(None, "add2", self.add_fragment, "Enter")
        act(None, "stop", self.stop, "Esc")
        act(None, "cut", self.split_fragment, "S")

    def show_help(self):
        QMessageBox.information(self, "Cómo se usa", (
            "1. Arrastrá los audios a la ventana (o «＋ Agregar audios»). Sirven .opus de WhatsApp, "
            "mp3, m4a, wav, ogg, flac, y hasta videos.\n"
            "2. Elegí un audio en la lista. Con el mouse marcá la parte que querés (podés ajustar "
            "los bordes arrastrándolos).\n"
            "3. Espacio = escuchar la selección. Enter = agregarla a la mezcla.\n"
            "4. Abajo arrastrá los bloques para ordenarlos. Doble clic = volver a editar ese "
            "fragmento; Supr = borrarlo; Ctrl+←/→ = moverlo; clic derecho = más opciones.\n"
            "   Cortar en la mezcla: clic en un bloque marca una línea amarilla; S (o «✂ Cortar aquí») "
            "lo parte en dos. También podés arrastrar los bordes de un bloque para recortarlo.\n"
            "   Ctrl+Espacio con la línea amarilla puesta reproduce desde ahí.\n"
            "   E (o «🎛 Efectos…») = ecualizador, más grave/agudo, más rápido/lento, quitar ruido "
            "y efectos (eco, robot, teléfono...) para el fragmento elegido.\n"
            "   V (o «🎙 Voz VERA…») = transforma la voz del fragmento con un preset de VERA.\n"
            "5. Ctrl+Espacio = escuchar todo junto. «Exportar audio…» guarda el resultado "
            "(wav, mp3, m4a, ogg, flac).\n\n"
            "Rueda del mouse = zoom · Shift+rueda = desplazar · Ctrl+Z = deshacer · "
            "Ctrl+S = guardar proyecto para seguir después."))

    # ------------------------------------------------------------------ carga de audios
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        projects = [p for p in paths if p.lower().endswith(".amix")]
        if projects:
            self.load_project(projects[0])
        self.add_files([p for p in paths if not p.lower().endswith(".amix")])

    def add_files_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Agregar audios", "", AUDIO_FILTER)
        self.add_files(paths)

    def add_files(self, paths):
        for path in paths:
            if os.path.isdir(path):
                continue
            self.pending += 1
            self.loader.load(path)
        if self.pending:
            self.statusBar().showMessage(f"Cargando {self.pending} audio(s)…")

    def on_loaded(self, path, data, err):
        self.pending -= 1
        if err or data is None or len(data) == 0:
            QMessageBox.warning(self, "No se pudo abrir",
                                f"{os.path.basename(path)}\n\n{err or 'El archivo no tiene audio.'}")
        else:
            self.add_source(AudioSource(path, data))
        self.statusBar().showMessage(f"Cargando {self.pending} audio(s)…" if self.pending
                                     else f"{len(self.sources)} audio(s) cargados.")

    def add_source(self, src):
        self.sources.append(src)
        it = QTreeWidgetItem([src.name, fmt_time(src.duration)])
        it.setIcon(0, color_icon(src.color))
        it.setToolTip(0, src.path)
        it.setData(0, Qt.UserRole, src)
        it.setFlags(it.flags() | Qt.ItemIsEditable)
        self.tree.addTopLevelItem(it)
        if self.editor.src is None:
            self.tree.setCurrentItem(it)

    def item_for(self, src):
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.data(0, Qt.UserRole) is src:
                return it
        return None

    def on_source_selected(self, cur, prev):
        src = cur.data(0, Qt.UserRole) if cur else None
        if src is self.editor.src:
            return
        if self.player.ctx and self.player.ctx[0] == "src":
            self.stop()
        self.editor.set_source(src)
        self.refresh()

    def on_source_renamed(self, it, col):
        src = it.data(0, Qt.UserRole)
        if src and col == 0 and it.text(0).strip():
            src.name = it.text(0).strip()
            self.refresh()

    def remove_source(self):
        it = self.tree.currentItem()
        if not it:
            return
        src = it.data(0, Qt.UserRole)
        used = sum(1 for f in self.frags if f.src is src)
        if used and QMessageBox.question(
                self, "Quitar audio",
                f"«{src.name}» se usa en {used} fragmento(s) de la mezcla. ¿Quitarlo igual?") \
                != QMessageBox.Yes:
            return
        self.stop()
        if used:
            self.push_undo()
            self.frags[:] = [f for f in self.frags if f.src is not src]
        self.sources.remove(src)
        self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(it))
        if self.tree.topLevelItemCount() == 0:
            self.editor.set_source(None)
        self.refresh()

    # ------------------------------------------------------------------ reproducción
    def toggle_play_editor(self):
        if self.player.playing and self.player.ctx and self.player.ctx[0] == "src":
            self.stop()
            return
        src = self.editor.src
        if not src:
            return
        if self.editor.sel:
            a, b = self.editor.sel
        else:
            a, b = self.editor.cursor_pos, src.length
            if b - a < SR // 10:
                a = 0
        self.player.play(src.data[a:b], ("src", src, a))

    def toggle_play_mix(self, start_index=0):
        if self.player.playing and self.player.ctx and self.player.ctx[0] == "mix":
            self.stop()
            return
        cut = self.seq.cut
        if cut and cut[0] in self.frags:
            self.play_frags(self.frags, cut)      # desde la línea amarilla
        else:
            self.play_frags(self.frags[start_index or 0:])

    def play_frags(self, frags, from_cut=None):
        if not frags:
            return
        buf, offsets = self.render(frags)
        if buf is None:
            return
        base = 0
        if from_cut:
            for fr, start, n in offsets:
                if fr is from_cut[0]:
                    base = start + int((from_cut[1] - fr.a) / max(1, fr.length) * n)
        self.player.play(buf[base:], ("mix", offsets, base))

    def render(self, frags):
        """Arma la mezcla mostrando el error si algún efecto falla; (None, None) si falla."""
        slow = not all(f.is_cached() for f in frags)
        if slow:
            self.statusBar().showMessage("Aplicando efectos…")
            QApplication.setOverrideCursor(Qt.WaitCursor)
            QApplication.processEvents()
        try:
            return render_mix(frags, self.gap_spin.value(), self.fade_spin.value(),
                              self.norm_check.isChecked())
        except Exception as ex:
            QMessageBox.critical(self, "Error con los efectos", str(ex))
            return None, None
        finally:
            if slow:
                QApplication.restoreOverrideCursor()
                self.statusBar().clearMessage()

    def stop(self):
        self.player.stop()
        self.editor.playhead = None
        self.seq.playhead = None
        self.editor.update()
        self.seq.update()

    def tick(self):
        if self.player.stream is None:
            return
        if not self.player.playing:
            self.stop()
            return
        pos = self.player.heard_pos()
        kind = self.player.ctx[0]
        if kind == "src":
            _, src, off = self.player.ctx
            if src is self.editor.src:
                self.editor.playhead = off + pos
                self.editor.follow(off + pos)
                self.editor.update()
        else:
            self.seq.playhead = None
            pos += self.player.ctx[2]
            for fr, start, n in self.player.ctx[1]:
                if start <= pos < start + n and fr in self.frags:
                    i = self.frags.index(fr)
                    self.seq.playhead = (i, (pos - start) / n)
                    if i < len(self.seq.rects):
                        r = self.seq.rects[i]
                        self.seq_scroll.ensureVisible(int(r.left() + (pos - start) / n * r.width()), 0, 80, 0)
                    break
            self.seq.update()

    # ------------------------------------------------------------------ fragmentos
    def snapshot(self):
        return [(f.src, f.a, f.b, f.gain_db, dict(f.fx)) for f in self.frags]

    def push_undo(self):
        self.undo_stack.append(self.snapshot())
        self.undo_stack = self.undo_stack[-200:]
        self.redo_stack.clear()

    def restore(self, snap):
        self.frags[:] = [Fragment(*t) for t in snap if t[0] in self.sources]
        self.refresh()

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self.snapshot())
            self.restore(self.undo_stack.pop())

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self.snapshot())
            self.restore(self.redo_stack.pop())

    def add_fragment(self):
        src, sel = self.editor.src, self.editor.sel
        if not src:
            return
        if not sel:
            self.statusBar().showMessage("Primero marcá con el mouse la parte que querés agregar.", 4000)
            return
        self.push_undo()
        self.frags.append(Fragment(src, *sel))
        self.seq.sel = len(self.frags) - 1
        self.refresh()
        self.statusBar().showMessage(f"Agregado fragmento #{len(self.frags)} de «{src.name}».", 3000)

    def replace_fragment(self):
        i = self.seq.sel
        if not (0 <= i < len(self.frags)) or not self.editor.sel:
            return
        self.push_undo()
        f = self.frags[i]
        f.src = self.editor.src
        f.a, f.b = self.editor.sel
        self.refresh()

    def delete_fragment(self):
        i = self.seq.sel
        if 0 <= i < len(self.frags):
            self.push_undo()
            del self.frags[i]
            self.seq.sel = min(i, len(self.frags) - 1)
            self.refresh()

    def duplicate_fragment(self, i):
        f = self.frags[i]
        self.push_undo()
        self.frags.insert(i + 1, Fragment(f.src, f.a, f.b, f.gain_db, f.fx))
        self.seq.sel = i + 1
        self.refresh()

    def move_fragment(self, i, j):
        self.push_undo()
        f = self.frags.pop(i)
        self.frags.insert(j, f)
        self.seq.sel = j
        self.refresh()

    def edit_fragment(self, i):
        f = self.frags[i]
        it = self.item_for(f.src)
        if it:
            self.tree.setCurrentItem(it)
        self.editor.set_selection(f.a, f.b)
        self.editor.show_range(f.a, f.b)
        self.refresh()

    def on_frag_selected(self, i):
        self.refresh()

    def valid_cut(self):
        cut = self.seq.cut
        if not cut or cut[0] not in self.frags:
            return None
        f, s = cut
        m = SequenceView.MIN_LEN
        return (f, s) if f.a + m <= s <= f.b - m else None

    def split_fragment(self):
        cut = self.valid_cut()
        if not cut:
            self.statusBar().showMessage(
                "Hacé clic dentro de un bloque de la mezcla para marcar dónde cortar (línea amarilla).", 4000)
            return
        f, s = cut
        self.push_undo()
        i = self.frags.index(f)
        self.frags.insert(i + 1, Fragment(f.src, s, f.b, f.gain_db, f.fx))
        f.b = s
        self.seq.cut = None
        self.seq.sel = i
        self.refresh()
        self.statusBar().showMessage(f"Bloque {i + 1} cortado en dos.", 3000)

    def trim_at_cut(self, keep_after):
        """Borra la parte de antes (keep_after=True) o de después de la línea de corte."""
        cut = self.valid_cut()
        if not cut:
            return
        f, s = cut
        self.push_undo()
        if keep_after:
            f.a = s
        else:
            f.b = s
        self.seq.cut = None
        self.refresh()

    def on_frag_trimmed(self):
        self.refresh()
        i = self.seq.sel
        if 0 <= i < len(self.frags):
            self.statusBar().showMessage(f"Bloque {i + 1}: {self.frags[i].length / SR:.2f} s")

    def update_cut_state(self):
        self.btn_cut.setEnabled(self.valid_cut() is not None)
        cut = self.valid_cut()
        if cut:
            f, s = cut
            self.statusBar().showMessage(
                f"Punto de corte en «{f.src.name}» {fmt_time(s / SR)} — S para cortar, "
                f"Ctrl+Espacio para escuchar desde ahí.")

    def on_gain_changed(self, v):
        i = self.seq.sel
        if 0 <= i < len(self.frags):
            self.frags[i].gain_db = v
            self.seq.update()

    def open_fx(self, i):
        if not (0 <= i < len(self.frags)):
            self.statusBar().showMessage("Elegí primero un fragmento de la mezcla (abajo).", 4000)
            return
        f = self.frags[i]
        dlg = FxDialog(self, f, lambda fx: self.play_frags([Fragment(f.src, f.a, f.b, f.gain_db, fx)]))
        dlg.applyAll.connect(self.apply_fx_all)
        ok = dlg.exec()
        self.stop()
        if ok and dlg.fx != f.fx and f in self.frags:
            self.push_undo()
            f.fx = clean_fx(dlg.fx)
            self.refresh()

    def apply_fx_all(self, fx):
        self.push_undo()
        for f in self.frags:
            f.fx = clean_fx(fx)
        self.refresh()
        self.statusBar().showMessage(f"Efectos aplicados a {len(self.frags)} fragmento(s).", 4000)

    def frag_menu(self, i, pos):
        m = QMenu(self)
        if i >= 0:
            m.addAction("▶ Escuchar este fragmento", lambda: self.play_frags([self.frags[i]]))
            m.addAction("▶ Escuchar desde acá", lambda: self.play_frags(self.frags[i:]))
            m.addAction("✎ Editar (ver en la onda)", lambda: self.edit_fragment(i))
            m.addAction("🎛 Efectos y ecualizador…", lambda: self.open_fx(i))
            m.addAction("🎙 Voz VERA…", lambda: self.vera_fragment(i))
            m.addAction("⧉ Duplicar", lambda: self.duplicate_fragment(i))
            if self.valid_cut():
                m.addSeparator()
                m.addAction("✂ Cortar aquí", self.split_fragment)
                m.addAction("⇤ Borrar lo de antes de la línea", lambda: self.trim_at_cut(True))
                m.addAction("⇥ Borrar lo de después de la línea", lambda: self.trim_at_cut(False))
            m.addSeparator()
            m.addAction("✕ Eliminar", self.delete_fragment)
        if self.frags:
            m.addSeparator()
            m.addAction("Vaciar mezcla", self.clear_frags)
        if not m.isEmpty():
            m.exec(pos)

    def open_vera(self):
        try:
            launch_vera()
        except Exception as ex:
            QMessageBox.critical(self, "VERA", str(ex))

    def vera_fragment(self, i):
        if not (0 <= i < len(self.frags)):
            self.statusBar().showMessage("Elegí primero un fragmento de la mezcla (abajo).", 4000)
            return
        if self.vera_busy:
            self.statusBar().showMessage("VERA ya está transformando otro fragmento…", 4000)
            return
        try:
            preset_store = vera_core()[0]
            names = preset_store.list_presets()
        except Exception as ex:
            QMessageBox.critical(self, "VERA no disponible",
                                 f"No pude cargar VERA:\n{ex}\n\nCorré instalar.bat en la carpeta del estudio.")
            return
        if not names:
            QMessageBox.information(self, "VERA", "No hay presets. Creá uno en VERA con «Guardar como…».")
            return
        dlg = VeraDialog(self, names)
        if not dlg.exec():
            return
        name = dlg.combo.currentText()
        try:
            preset = preset_store.load_preset(name)
        except Exception as ex:
            QMessageBox.critical(self, "VERA", f"No pude leer el preset «{name}»:\n{ex}")
            return
        f = self.frags[i]
        safe = preset_store.safe_name(f"{f.src.name}_{fmt_time(f.a / SR).replace(':', 'm')}_{name}")
        path = os.path.join(VERA_FRAG_DIR, f"{safe}.wav")
        n = 2
        while os.path.exists(path):
            path = os.path.join(VERA_FRAG_DIR, f"{safe}_{n}.wav")
            n += 1
        self.vera_busy = True
        QApplication.setOverrideCursor(Qt.BusyCursor)
        self.statusBar().showMessage(f"VERA: transformando «{f.src.name}» con «{name}»…")
        self.vera.run(f, preset, path)

    def on_vera_done(self, frag, data, path, err):
        self.vera_busy = False
        QApplication.restoreOverrideCursor()
        if err:
            self.statusBar().clearMessage()
            QMessageBox.critical(self, "VERA", f"No se pudo transformar el fragmento:\n{err}")
            return
        src = AudioSource(path, data, f"{frag.src.name} (VERA)")
        self.add_source(src)
        if frag in self.frags:
            self.push_undo()
            j = self.frags.index(frag)
            self.frags[j] = Fragment(src, 0, len(data), frag.gain_db, frag.fx)
            self.refresh()
        self.statusBar().showMessage(f"Listo: el fragmento ahora usa «{src.name}» (Ctrl+Z para volver).", 6000)

    def clear_frags(self):
        self.push_undo()
        self.frags.clear()
        self.refresh()

    # ------------------------------------------------------------------ refresco de UI
    def refresh(self):
        src = self.editor.src
        self.src_title.setText(f"{src.name}   ·   {fmt_time(src.duration)}" if src else "Ningún audio elegido")
        i = self.seq.sel
        self.editor.overlays = [(k + 1, f.a, f.b, k == i) for k, f in enumerate(self.frags) if f.src is src]
        self.editor.update()
        self.seq.set_frags(self.frags)
        has_frag = 0 <= self.seq.sel < len(self.frags)
        self.gain_spin.blockSignals(True)
        self.gain_spin.setEnabled(has_frag)
        self.gain_spin.setValue(self.frags[self.seq.sel].gain_db if has_frag else 0)
        self.gain_spin.blockSignals(False)
        self.btn_fx.setEnabled(has_frag)
        self.btn_replace.setEnabled(has_frag and self.editor.sel is not None)
        self.btn_add.setEnabled(self.editor.sel is not None)
        total = sum(f.out_length for f in self.frags) / SR
        if len(self.frags) > 1:
            total += self.gap_spin.value() / 1000 * (len(self.frags) - 1)
        self.total_label.setText(f"{len(self.frags)} fragmento(s) · {fmt_time(total)}" if self.frags else "")
        self.btn_cut.setEnabled(self.valid_cut() is not None)
        self.update_sel_label()

    def update_sel_label(self):
        s = self.editor.sel
        if s:
            self.sel_label.setText(f"Selección: {fmt_time(s[0] / SR)} → {fmt_time(s[1] / SR)}  "
                                   f"({(s[1] - s[0]) / SR:.2f} s)")
        elif self.editor.src:
            self.sel_label.setText(f"Cursor: {fmt_time(self.editor.cursor_pos / SR)}")
        else:
            self.sel_label.setText("")
        has_frag = 0 <= self.seq.sel < len(self.frags)
        self.btn_replace.setEnabled(has_frag and s is not None)
        self.btn_add.setEnabled(s is not None)

    def sync_scrollbar(self):
        e = self.editor
        self.hscroll.blockSignals(True)
        if e.src:
            total = int(e.src.length * 1000 / SR)
            page = int(e.view_len * 1000 / SR)
            self.hscroll.setRange(0, max(0, total - page))
            self.hscroll.setPageStep(max(1, page))
            self.hscroll.setSingleStep(max(1, page // 20))
            self.hscroll.setValue(int(e.view_start * 1000 / SR))
        else:
            self.hscroll.setRange(0, 0)
        self.hscroll.blockSignals(False)

    # ------------------------------------------------------------------ exportar / proyecto
    def export_dialog(self):
        if not self.frags:
            QMessageBox.information(self, "Exportar", "La mezcla está vacía.")
            return
        base = os.path.splitext(self.project_path)[0] if self.project_path else "mezcla"
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar audio", base + ".wav",
            "WAV (*.wav);;MP3 (*.mp3);;M4A / AAC (*.m4a);;OGG Opus (*.ogg);;FLAC (*.flac)")
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".wav"
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            buf, _ = self.render(self.frags)
            if buf is None:
                QApplication.restoreOverrideCursor()
                return
            export_audio(path, buf)
        except Exception as ex:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Error al exportar", str(ex))
            return
        QApplication.restoreOverrideCursor()
        self.statusBar().showMessage(f"Exportado: {path}  ({fmt_time(len(buf) / SR)})", 8000)

    def save_project(self, save_as=False):
        path = self.project_path
        if save_as or not path:
            path, _ = QFileDialog.getSaveFileName(self, "Guardar proyecto", path or "proyecto.amix",
                                                  "Proyecto Audio Mixer (*.amix)")
            if not path:
                return
        data = {
            "version": 1,
            "sources": [{"path": s.path, "name": s.name} for s in self.sources],
            "fragments": [{"src": self.sources.index(f.src), "start": f.a, "end": f.b, "gain_db": f.gain_db,
                           "fx": {k: v for k, v in f.fx.items() if v != FX_DEFAULT[k]}}
                          for f in self.frags],
            "gap_ms": self.gap_spin.value(),
            "fade_ms": self.fade_spin.value(),
            "normalize": self.norm_check.isChecked(),
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        self.project_path = path
        self.setWindowTitle(f"Audio Mixer — {os.path.basename(path)}")
        self.statusBar().showMessage(f"Proyecto guardado: {path}", 5000)

    def open_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "Abrir proyecto", "", "Proyecto Audio Mixer (*.amix)")
        if path:
            self.load_project(path)

    def load_project(self, path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as ex:
            QMessageBox.critical(self, "Error", f"No se pudo abrir el proyecto:\n{ex}")
            return
        self.stop()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        srcs, missing = [], []
        for s in data.get("sources", []):
            try:
                srcs.append(AudioSource(s["path"], decode(s["path"]), s.get("name")))
            except Exception:
                srcs.append(None)
                missing.append(s["path"])
        QApplication.restoreOverrideCursor()
        self.sources.clear()
        self.tree.clear()
        self.editor.set_source(None)
        self.frags.clear()
        self.undo_stack.clear()
        self.redo_stack.clear()
        for s in srcs:
            if s:
                self.add_source(s)
        for f in data.get("fragments", []):
            s = srcs[f["src"]] if f["src"] < len(srcs) else None
            if s:
                self.frags.append(Fragment(s, f["start"], f["end"], f.get("gain_db", 0), f.get("fx")))
        self.gap_spin.setValue(data.get("gap_ms", 0))
        self.fade_spin.setValue(data.get("fade_ms", 15))
        self.norm_check.setChecked(data.get("normalize", True))
        self.project_path = path
        self.setWindowTitle(f"Audio Mixer — {os.path.basename(path)}")
        self.seq.sel = -1
        self.refresh()
        if missing:
            QMessageBox.warning(self, "Faltan audios", "No se encontraron:\n" + "\n".join(missing))

    def closeEvent(self, e):
        self.player.stop()
        super().closeEvent(e)


def dark_palette(app):
    app.setStyle("Fusion")
    p = QPalette()
    p.setColor(QPalette.Window, QColor("#2a2d35"))
    p.setColor(QPalette.WindowText, TEXT)
    p.setColor(QPalette.Base, QColor("#1f2128"))
    p.setColor(QPalette.AlternateBase, QColor("#262931"))
    p.setColor(QPalette.Text, TEXT)
    p.setColor(QPalette.Button, QColor("#353944"))
    p.setColor(QPalette.ButtonText, TEXT)
    p.setColor(QPalette.Highlight, QColor("#3d6fb6"))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ToolTipBase, QColor("#353944"))
    p.setColor(QPalette.ToolTipText, TEXT)
    p.setColor(QPalette.Disabled, QPalette.ButtonText, DIM)
    p.setColor(QPalette.Disabled, QPalette.Text, DIM)
    app.setPalette(p)
    app.setStyleSheet("""
        QLabel#h { font-weight: 600; font-size: 10.5pt; padding: 2px 0 4px 0; }
        QLabel#hint { color: #7d8590; font-size: 8.5pt; }
        QPushButton { padding: 5px 10px; border-radius: 5px; border: 1px solid #454a57; background: #353944; }
        QPushButton:hover { background: #404552; }
        QPushButton:disabled { color: #626873; border-color: #3a3e48; }
        QPushButton#primary { background: #2f5d9a; border-color: #3d6fb6; color: white; }
        QPushButton#primary:hover { background: #3a6db0; }
        QPushButton#primary:disabled { background: #2b3442; color: #6c7686; }
        QTreeWidget { border: 1px solid #3a3e48; }
        QScrollArea { border: 1px solid #3a3e48; }
    """)


def main():
    app = QApplication(sys.argv)
    dark_palette(app)
    w = MainWindow()
    w.show()
    files = [a for a in sys.argv[1:] if os.path.isfile(a)]
    for f in files:
        if f.lower().endswith(".amix"):
            w.load_project(f)
        else:
            w.add_files([f])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
