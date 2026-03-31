"""Voice Fallback — autonomous voice capability when workstation is unreachable.

Provides local STT (Vosk) and TTS (Piper) so the robot can understand
and respond to voice commands even without network connectivity.

Engines:
    VoskFallbackSTT   — offline speech-to-text via Vosk (small Russian model)
    PiperFallbackTTS  — offline text-to-speech via Piper (Russian medium)

Pipeline:
    VoiceFallbackPipeline — combines STT + TTS + optional UtteranceNormalizer

Usage:
    pipeline = VoiceFallbackPipeline()
    if pipeline.available:
        pipeline.activate()
        result = pipeline.process_audio(mic_bytes)
        # → {"recognized": "стой", "response_text": "...",
        #    "response_audio": <wav bytes>, "mode": "fallback"}
"""
from __future__ import annotations

import io
import json
import logging
import os
import struct
import subprocess
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional imports — graceful fallback if not installed
# ---------------------------------------------------------------------------
try:
    import vosk  # type: ignore[import-untyped]

    _VOSK_AVAILABLE = True
except ImportError:
    _VOSK_AVAILABLE = False
    logger.info("vosk not installed — VoskFallbackSTT will be unavailable")


# =========================================================================
# STT Engine — Vosk
# =========================================================================

class VoskFallbackSTT:
    """Offline speech-to-text using Vosk with a small Russian model."""

    def __init__(self, model_path: str = "models/vosk-model-small-ru-0.22") -> None:
        self._model_path = model_path
        self._model: Any = None
        self._available = False

        if not _VOSK_AVAILABLE:
            logger.warning("vosk package not installed")
            return

        if not os.path.isdir(model_path):
            logger.warning("Vosk model not found at %s", model_path)
            return

        try:
            vosk.SetLogLevel(-1)  # suppress vosk logs
            self._model = vosk.Model(model_path)
            self._available = True
            logger.info("VoskFallbackSTT ready (model: %s)", model_path)
        except Exception as exc:
            logger.error("Failed to load Vosk model: %s", exc)

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self._available

    # ------------------------------------------------------------------
    def transcribe(
        self,
        audio_bytes: bytes,
        sample_rate: int = 16000,
    ) -> Dict[str, Any]:
        """Transcribe PCM16 mono audio bytes to text.

        Returns dict with keys: text, latency_ms, engine.
        """
        if not self._available:
            return {"text": "", "latency_ms": 0, "engine": "vosk_fallback"}

        t0 = time.monotonic()

        rec = vosk.KaldiRecognizer(self._model, sample_rate)
        chunk_size = 4000

        offset = 0
        while offset < len(audio_bytes):
            chunk = audio_bytes[offset : offset + chunk_size]
            rec.AcceptWaveform(chunk)
            offset += chunk_size

        raw = rec.FinalResult()
        try:
            result = json.loads(raw)
            text = result.get("text", "")
        except (json.JSONDecodeError, TypeError):
            text = ""

        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        logger.debug("Vosk transcribed (%s ms): %s", latency_ms, text)

        return {
            "text": text,
            "latency_ms": latency_ms,
            "engine": "vosk_fallback",
        }


# =========================================================================
# TTS Engine — Piper
# =========================================================================

_PIPER_SEARCH_PATHS = [
    "/usr/bin/piper",
    "/usr/local/bin/piper",
    "/opt/piper/piper",
    os.path.expanduser("~/.local/bin/piper"),
    "models/piper",
    "/app/piper",
]


class PiperFallbackTTS:
    """Offline text-to-speech using Piper (ONNX Russian model)."""

    def __init__(self, model_path: str = "models/piper-ru-medium.onnx") -> None:
        self._model_path = model_path
        self._piper_bin: Optional[str] = None
        self._available = False

        if not os.path.isfile(model_path):
            logger.warning("Piper model not found at %s", model_path)
            return

        self._piper_bin = self._find_piper()
        if self._piper_bin is None:
            logger.warning("piper binary not found in any search path")
            return

        self._available = True
        logger.info(
            "PiperFallbackTTS ready (binary: %s, model: %s)",
            self._piper_bin,
            model_path,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _find_piper() -> Optional[str]:
        """Search common locations for the piper binary."""
        for path in _PIPER_SEARCH_PATHS:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
        # Also try PATH
        try:
            result = subprocess.run(
                ["which", "piper"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self._available

    # ------------------------------------------------------------------
    def synthesize(self, text: str) -> Dict[str, Any]:
        """Synthesize text to WAV audio bytes.

        Returns dict with keys: audio (WAV bytes), latency_ms, engine.
        """
        if not self._available:
            return {"audio": b"", "latency_ms": 0, "engine": "piper_fallback"}

        t0 = time.monotonic()

        try:
            proc = subprocess.run(
                [
                    self._piper_bin,  # type: ignore[list-item]
                    "--model", self._model_path,
                    "--output_raw",
                ],
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=30,
            )

            if proc.returncode != 0:
                logger.error("Piper failed (rc=%d): %s", proc.returncode, proc.stderr[:200])
                return {"audio": b"", "latency_ms": 0, "engine": "piper_fallback"}

            pcm_data = proc.stdout
            wav_data = self._pcm_to_wav(pcm_data, sample_rate=22050, sample_width=2, channels=1)

        except subprocess.TimeoutExpired:
            logger.error("Piper timed out after 30s")
            return {"audio": b"", "latency_ms": 0, "engine": "piper_fallback"}
        except Exception as exc:
            logger.error("Piper synthesis error: %s", exc)
            return {"audio": b"", "latency_ms": 0, "engine": "piper_fallback"}

        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        logger.debug("Piper synthesized (%s ms, %d bytes WAV)", latency_ms, len(wav_data))

        return {
            "audio": wav_data,
            "latency_ms": latency_ms,
            "engine": "piper_fallback",
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _pcm_to_wav(
        pcm_data: bytes,
        sample_rate: int = 22050,
        sample_width: int = 2,
        channels: int = 1,
    ) -> bytes:
        """Convert raw PCM16 data to a WAV file in memory."""
        data_size = len(pcm_data)
        byte_rate = sample_rate * channels * sample_width
        block_align = channels * sample_width

        buf = io.BytesIO()
        # RIFF header
        buf.write(b"RIFF")
        buf.write(struct.pack("<I", 36 + data_size))  # chunk size
        buf.write(b"WAVE")
        # fmt sub-chunk
        buf.write(b"fmt ")
        buf.write(struct.pack("<I", 16))               # sub-chunk size
        buf.write(struct.pack("<H", 1))                 # PCM format
        buf.write(struct.pack("<H", channels))
        buf.write(struct.pack("<I", sample_rate))
        buf.write(struct.pack("<I", byte_rate))
        buf.write(struct.pack("<H", block_align))
        buf.write(struct.pack("<H", sample_width * 8))  # bits per sample
        # data sub-chunk
        buf.write(b"data")
        buf.write(struct.pack("<I", data_size))
        buf.write(pcm_data)

        return buf.getvalue()


# =========================================================================
# Voice Fallback Pipeline
# =========================================================================

class VoiceFallbackPipeline:
    """Combines STT + TTS for autonomous voice operation.

    Activated when the workstation is unreachable; deactivated when
    connectivity is restored.
    """

    def __init__(self, utterance_normalizer: Any = None) -> None:
        self._stt = VoskFallbackSTT()
        self._tts = PiperFallbackTTS()
        self._normalizer = utterance_normalizer
        self._active = False

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        """True if both STT and TTS engines are available."""
        return self._stt.available and self._tts.available

    @property
    def active(self) -> bool:
        return self._active

    # ------------------------------------------------------------------
    def activate(self) -> Optional[bytes]:
        """Activate fallback voice mode. Returns announcement audio if TTS available."""
        if self._active:
            return None
        self._active = True
        logger.info("Voice fallback pipeline ACTIVATED")
        return self.speak("Связь с сервером потеряна. Работаю автономно.")

    def deactivate(self) -> Optional[bytes]:
        """Deactivate fallback voice mode. Returns announcement audio if TTS available."""
        if not self._active:
            return None
        self._active = False
        logger.info("Voice fallback pipeline DEACTIVATED")
        return self.speak("Связь восстановлена!")

    # ------------------------------------------------------------------
    def process_audio(
        self,
        audio_bytes: bytes,
        sample_rate: int = 16000,
    ) -> Dict[str, Any]:
        """Process incoming audio: transcribe → normalize → respond.

        Returns dict with keys:
            recognized     — transcribed text
            response_text  — text response (if any)
            response_audio — WAV bytes of response (if TTS available)
            action         — normalized action (if normalizer available)
            mode           — always "fallback"
        """
        result: Dict[str, Any] = {
            "recognized": "",
            "response_text": "",
            "response_audio": b"",
            "action": None,
            "mode": "fallback",
        }

        if not self._active:
            return result

        # --- STT ---
        stt_result = self._stt.transcribe(audio_bytes, sample_rate)
        text = stt_result.get("text", "").strip()
        result["recognized"] = text

        if not text:
            return result

        logger.info("Fallback voice recognized: '%s'", text)

        # --- Normalize command (optional) ---
        normalizer = self._normalizer
        if normalizer is None:
            normalizer = self._try_import_normalizer()

        if normalizer is not None:
            try:
                norm_result = normalizer.normalize(text)
                if norm_result and norm_result.action:
                    result["action"] = {
                        "action": norm_result.action.value if hasattr(norm_result.action, "value") else str(norm_result.action),
                        "target": norm_result.target,
                        "confidence": norm_result.confidence,
                    }
                    response = f"Команда принята: {norm_result.action.value if hasattr(norm_result.action, 'value') else norm_result.action}"
                    result["response_text"] = response
                    result["response_audio"] = self.speak(response) or b""
                    return result
            except Exception as exc:
                logger.debug("Normalizer failed: %s", exc)

        # No normalizer or normalization failed — echo back
        result["response_text"] = text
        return result

    # ------------------------------------------------------------------
    def speak(self, text: str) -> Optional[bytes]:
        """Synthesize speech. Returns WAV audio bytes or None."""
        if not self._tts.available:
            return None
        tts_result = self._tts.synthesize(text)
        audio = tts_result.get("audio", b"")
        return audio if audio else None

    # ------------------------------------------------------------------
    @staticmethod
    def _try_import_normalizer() -> Any:
        """Try to import UtteranceNormalizer from libs (may not be on PYTHONPATH)."""
        try:
            from libs.robot_interface.utterance_map import UtteranceNormalizer
            return UtteranceNormalizer()
        except ImportError:
            return None
