# Локальная речь и AI на Jetson Orin

Этот документ фиксирует подтвержденный рабочий минимум и целевой локальный стек для E1.

## Подтвержденный минимум

На Jetson уже подтверждено:

- локальный TTS через `espeak-ng`
- русский голос `ru`
- вызов через `e1_server` endpoint `/api/audio/speak`

Установка:

```bash
sudo apt update
sudo apt install -y espeak-ng ffmpeg
```

Проверка:

```bash
espeak-ng --voices | grep -i ru
espeak-ng -v ru "Привет. Я робот E1. Русский голос работает."
```

Через `e1_server`:

```bash
curl -s -X POST "http://127.0.0.1:8083/api/audio/speak" \
  -H "Content-Type: application/json" \
  -d '{"text":"Привет. Я робот E1. Русский голос работает.","lang":"ru"}'
```

## Целевой локальный стек

- TTS: `Piper`
- STT: `faster-whisper`
- LLM: `Qwen2.5-3B-Instruct` через `llama.cpp`

## Базовые системные пакеты

```bash
sudo apt update
sudo apt install -y \
  git cmake build-essential python3-venv python3-pip \
  libopenblas-dev ffmpeg wget curl
```

## Python virtualenv

```bash
python3 -m venv ~/robot-ai
source ~/robot-ai/bin/activate
python -m pip install -U pip wheel setuptools
```

## STT: faster-whisper

Установка:

```bash
source ~/robot-ai/bin/activate
python -m pip install -r requirements-jetson-local-ai.txt
```

Быстрая проверка:

```bash
source ~/robot-ai/bin/activate
python - <<'PY'
from faster_whisper import WhisperModel
model = WhisperModel("small", device="cpu", compute_type="int8")
print("faster-whisper OK")
PY
```

Если CUDA-окружение на Jetson настроено стабильно, потом можно перейти на:

```python
WhisperModel("small", device="cuda", compute_type="float16")
```

## TTS: Piper

Python-пакет `piper-tts` на Jetson давал конфликт зависимостей, поэтому зафиксирован рабочий путь через бинарник.

### Установка бинарника

```bash
mkdir -p ~/opt/piper
cd ~/opt/piper
wget https://github.com/rhasspy/piper/releases/latest/download/piper_linux_aarch64.tar.gz
tar -xzf piper_linux_aarch64.tar.gz
```

### Скачивание русской модели

```bash
mkdir -p ~/models/piper-ru
cd ~/models/piper-ru
wget -O ru_RU-dmitri-medium.onnx "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx"
wget -O ru_RU-dmitri-medium.onnx.json "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx.json"
```

### Проверка

```bash
echo 'Привет. Я робот E1.' | ~/opt/piper/piper --model ~/models/piper-ru/ru_RU-dmitri-medium.onnx --output_file /tmp/piper_test.wav
aplay /tmp/piper_test.wav
```

## LLM: llama.cpp + Qwen2.5-3B-Instruct

### Сборка llama.cpp

```bash
cd ~
git clone https://github.com/ggml-org/llama.cpp.git
cd ~/llama.cpp
cmake -S . -B build -DGGML_CUDA=ON
cmake --build build -j$(nproc)
```

Проверка:

```bash
~/llama.cpp/build/bin/llama-cli --help | head
```

### Скачивание Qwen2.5-3B-Instruct GGUF

```bash
source ~/robot-ai/bin/activate
python -m pip install "huggingface_hub<0.34"
huggingface-cli login
mkdir -p ~/models/qwen2.5-3b
huggingface-cli download Qwen/Qwen2.5-3B-Instruct-GGUF qwen2.5-3b-instruct-q5_k_m.gguf --local-dir ~/models/qwen2.5-3b --local-dir-use-symlinks False
```

Проверка:

```bash
~/llama.cpp/build/bin/llama-cli \
  -m ~/models/qwen2.5-3b/qwen2.5-3b-instruct-q5_k_m.gguf \
  -ngl 99 \
  -c 4096 \
  -p "Ты дружелюбный русскоязычный робот. Коротко поздоровайся."
```

## Почему пока не полностью локально

На момент этой фиксации:

- `espeak-ng` уже подтвержден и подходит для демо
- `Piper` выбран как следующий TTS, но зафиксирован через бинарник, а не Python package
- `faster-whisper` и `Qwen` подготовлены как целевой локальный стек, но еще не сведены в единый production dialog loop

## Рекомендованный порядок внедрения

1. оставить `espeak-ng` как baseline TTS
2. поднять `faster-whisper`
3. поднять `llama.cpp + Qwen2.5-3B-Instruct`
4. заменить `espeak-ng` на `Piper`
5. собрать единый локальный dialog loop
