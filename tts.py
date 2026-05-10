import os
import tempfile
import threading
import wave
import winsound

from piper import PiperVoice

DEFAULT_MODEL = os.path.join(
    os.path.dirname(__file__), "models", "en_US-amy-medium.onnx"
)
VOICE_MODEL = os.getenv("PIPER_MODEL", DEFAULT_MODEL)
TTS_DISABLED = os.getenv("TTS_DISABLED", "").lower() in ("1", "true", "yes")


class TTS:
    def __init__(self, model_path=VOICE_MODEL, disabled=TTS_DISABLED):
        self.voice = None
        self._lock = threading.Lock()
        if disabled:
            print("[tts] disabled via TTS_DISABLED")
            return
        if not os.path.exists(model_path):
            print(f"[tts] model not found at {model_path}; speech disabled")
            return
        try:
            self.voice = PiperVoice.load(model_path)
            print(f"[tts] loaded {os.path.basename(model_path)}")
        except Exception as ex:
            print(f"[tts] failed to load voice: {ex}")

    def speak(self, text):
        if not self.voice or not text:
            return
        threading.Thread(target=self._run, args=(text,), daemon=True).start()

    def _run(self, text):
        with self._lock:
            path = None
            try:
                fd, path = tempfile.mkstemp(suffix=".wav")
                os.close(fd)
                with wave.open(path, "wb") as wav_file:
                    self.voice.synthesize_wav(text, wav_file)
                winsound.PlaySound(path, winsound.SND_FILENAME)
            except Exception as ex:
                print(f"[tts] error: {ex}")
            finally:
                if path:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass


if __name__ == "__main__":
    t = TTS()
    t.speak("Coach online. Synthesis test, one two three.")
    import time

    time.sleep(8)
