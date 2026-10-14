"""Speak to the assistant: the microphone, written down while you talk, on this computer.

The AI Assistant window's Speak button (and F2) records from the microphone and shows the words in
the input as they are spoken; stopping leaves the text there to read, correct and send with Enter.
Nothing is sent by speaking alone: a misheard "50 µm" must never reach the stage unread.

The speech is written down by Phonon-2 (Fermion Research, model licence CC-BY-4.0, code Apache-2.0),
a compressed NVIDIA Parakeet that runs on the CPU, English only. Nothing leaves the computer, and
nothing is recorded to disk. At run time it needs only PyTorch, which mesoSPIM already has, and the
small `fermion-research` package, installed without its own dependencies:

    pip install --no-deps fermion-research==0.2.11

The model (157 MB) is downloaded once; that one download also needs `huggingface_hub` and
`zstandard`. `FERMION_CACHE_DIR` and `HF_HOME` choose where it is kept.

The pieces, each replaceable so the tests need neither a sound card nor a model:

- `QtMicrophone` hands over 16 kHz mono blocks from Qt's own audio input (no extra package);
- `load_phonon2` loads the model; `phonon2_session` is Phonon-2's own live session, which writes a
  phrase down again every half second while it is spoken and settles it at a pause;
- `Voice` ties them together for the window. The model loads and every phrase is decoded on a thread
  of its own, so the window never waits on the speech engine.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from PyQt5 import QtCore

#: The model, as Phonon-2's package names it.
MODEL = "phonon-2"
#: What the microphone hands over: 16 kHz, one channel, the rate the model was trained on.
SAMPLE_RATE = 16000
#: How the microphone hands it over: about a tenth of a second at a time.
BLOCK_SAMPLES = 1600


def missing() -> str | None:
    """What is missing to speak to the assistant on this computer, in words; None when nothing is."""
    if importlib.util.find_spec("fermion") is None:
        return "pip install --no-deps fermion-research==0.2.11"
    try:
        from PyQt5 import QtMultimedia  # noqa: F401
    except ImportError:
        return "PyQt5's QtMultimedia, for the microphone"
    return None


def load_phonon2():
    """Phonon-2 on the CPU, downloading and unpacking the model the first time.

    Its CPU engine is loaded directly rather than through the package's own front door, which
    insists on libraries (transformers, soundfile) that only its other engines and its file reader
    use; with the model on disk, the engine itself needs PyTorch alone.
    """
    from fermion._speech import engine_phonon2_cpu, fetch
    from fermion.transcribe import _resolve

    repo, key, pin, local_dir = _resolve(MODEL)
    model_dir = Path(local_dir or fetch.profile_dir(repo, pin["unpack_dir"]))
    if not model_dir.is_dir() or not any(model_dir.iterdir()):
        model_dir = fetch.ensure(repo, key, pin)   # the one download: needs huggingface_hub and zstandard
    return engine_phonon2_cpu.load(model_dir, profile=key, backend=pin["backend"], quiet=True)


def phonon2_session(engine, on_partial, on_final):
    """Phonon-2's live session: `on_partial` gets the phrase so far, `on_final` a settled phrase."""
    from fermion._speech import live

    return live.LiveSession(engine, on_partial=on_partial, on_final=on_final)


class QtMicrophone:
    """The computer's default microphone, through Qt's own audio input."""

    def __init__(self):
        self.audio = None
        self.device = None

    def start(self, on_block):
        import numpy as np
        from PyQt5 import QtMultimedia

        audio_format = QtMultimedia.QAudioFormat()
        audio_format.setSampleRate(SAMPLE_RATE)
        audio_format.setChannelCount(1)
        audio_format.setSampleSize(16)
        audio_format.setCodec("audio/pcm")
        audio_format.setByteOrder(QtMultimedia.QAudioFormat.LittleEndian)
        audio_format.setSampleType(QtMultimedia.QAudioFormat.SignedInt)
        source = QtMultimedia.QAudioDeviceInfo.defaultInputDevice()
        if source.isNull():
            raise RuntimeError("no microphone is connected")
        if not source.isFormatSupported(audio_format):
            raise RuntimeError(f"the microphone {source.deviceName()} cannot record 16 kHz mono")
        self.audio = QtMultimedia.QAudioInput(source, audio_format)
        self.audio.setBufferSize(BLOCK_SAMPLES * 2 * 4)
        self.device = self.audio.start()
        if self.device is None:              # Windows' privacy settings or a security program said no
            self.audio = None
            raise RuntimeError(f"the microphone {source.deviceName()} could not be opened; check that "
                               "desktop apps may use it (Windows Settings, Privacy, Microphone) and that "
                               "no security program blocks Python")

        def read():
            data = bytes(self.device.readAll())
            if data:
                on_block(np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0)

        self.device.readyRead.connect(read)

    def stop(self):
        if self.audio is not None:
            self.audio.stop()
        self.audio, self.device = None, None


class Decoder(QtCore.QObject):
    """The speech engine, on a thread of its own: loads the model, and writes down what it hears.

    Every recording has a number; blocks, ends and cancels for an earlier number are ignored, so a
    block still queued from a cancelled recording never lands in the next one.
    """

    sig_ready = QtCore.pyqtSignal()
    sig_text = QtCore.pyqtSignal(str, str)          # the settled phrases, the phrase being formed
    sig_finished = QtCore.pyqtSignal(str)
    sig_failed = QtCore.pyqtSignal(str)

    def __init__(self, load_engine, make_session):
        super().__init__()
        self.load_engine, self.make_session = load_engine, make_session
        self.engine = None
        self.session = None
        self.recording = 0
        self.settled = []

    @QtCore.pyqtSlot()
    def load(self):
        try:
            self.engine = self.load_engine()
        except Exception as error:          # noqa: BLE001 -- said in the window, never raised there
            self.sig_failed.emit(f"the speech model could not be loaded: {error}")
            return
        self.sig_ready.emit()

    @QtCore.pyqtSlot(int)
    def begin(self, recording):
        self.recording, self.settled = recording, []
        self.session = self.make_session(self.engine, self.on_partial, self.on_final)

    @QtCore.pyqtSlot(object, int)
    def feed(self, block, recording):
        if recording == self.recording and self.session is not None:
            self.session.feed(block)

    @QtCore.pyqtSlot(int)
    def end(self, recording):
        if recording != self.recording or self.session is None:
            return
        self.session.finish()
        self.session = None
        self.sig_finished.emit(" ".join(self.settled))

    @QtCore.pyqtSlot(int)
    def cancel(self, recording):
        if recording == self.recording:
            self.session = None

    def on_partial(self, text):
        self.sig_text.emit(" ".join(self.settled), text)

    def on_final(self, text):
        self.settled.append(text)
        self.sig_text.emit(" ".join(self.settled), "")


class Voice(QtCore.QObject):
    """What the window holds: `load`, then `begin` and `end` (or `cancel`) once per recording.

    `sig_text(settled, forming)` says what has been heard so far; `sig_finished(text)` the whole
    recording, once it is stopped; `sig_failed(message)` what went wrong, in words.
    """

    sig_ready = QtCore.pyqtSignal()
    sig_text = QtCore.pyqtSignal(str, str)
    sig_finished = QtCore.pyqtSignal(str)
    sig_failed = QtCore.pyqtSignal(str)
    sig_load = QtCore.pyqtSignal()
    sig_begin = QtCore.pyqtSignal(int)
    sig_feed = QtCore.pyqtSignal(object, int)
    sig_end = QtCore.pyqtSignal(int)
    sig_cancel = QtCore.pyqtSignal(int)

    def __init__(self, load_engine=load_phonon2, make_session=phonon2_session, microphone=None, parent=None):
        super().__init__(parent)
        self.microphone = microphone if microphone is not None else QtMicrophone()
        self.recording = 0
        self.listening = False
        self.thread = QtCore.QThread()
        self.decoder = Decoder(load_engine, make_session)
        self.decoder.moveToThread(self.thread)
        for signal, slot in ((self.sig_load, self.decoder.load), (self.sig_begin, self.decoder.begin),
                             (self.sig_feed, self.decoder.feed), (self.sig_end, self.decoder.end),
                             (self.sig_cancel, self.decoder.cancel)):
            signal.connect(slot, QtCore.Qt.QueuedConnection)
        self.decoder.sig_ready.connect(self.sig_ready)
        self.decoder.sig_text.connect(self.sig_text)
        self.decoder.sig_finished.connect(self.sig_finished)
        self.decoder.sig_failed.connect(self.sig_failed)
        self.thread.start()

    def load(self):
        self.sig_load.emit()

    def begin(self):
        """Start a recording: the microphone on, its blocks to the engine as they come."""
        self.recording += 1
        recording = self.recording
        self.sig_begin.emit(recording)
        try:
            self.microphone.start(lambda block: self.sig_feed.emit(block, recording))
        except Exception as error:          # noqa: BLE001 -- no microphone is said, not raised
            self.sig_failed.emit(str(error))
            return False
        self.listening = True
        return True

    def end(self):
        """Stop the recording; `sig_finished` brings the whole of it once the last phrase settles."""
        if self.listening:
            self.microphone.stop()
            self.listening = False
            self.sig_end.emit(self.recording)

    def cancel(self):
        """Throw the recording away: nothing more is heard of it."""
        if self.listening:
            self.microphone.stop()
            self.listening = False
        self.sig_cancel.emit(self.recording)
        self.recording += 1

    def close(self):
        """Stop listening and end the engine's thread."""
        self.cancel()
        self.thread.quit()
        self.thread.wait(3000)
