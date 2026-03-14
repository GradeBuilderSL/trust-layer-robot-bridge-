# Установка Trust Layer Bridge на робота Noetix N2

## Требования

- Python 3.10+
- Доступ к терминалу робота (SSH)
- Сеть между роботом и компьютером

## Установка

### 1. Подключитесь к роботу

```bash
ssh noetix@192.168.1.100
# Пароль: указан на наклейке робота
```

### 2. Скачайте мост

```bash
cd /home/noetix
git clone <URL_РЕПОЗИТОРИЯ> trust-layer-robot-bridge
cd trust-layer-robot-bridge
```

### 3. Установите зависимости

```bash
pip install -r requirements.txt
```

### 4. Запустите

```bash
ADAPTER_TYPE=http python -m bridge.main
```

### 5. Проверьте

С компьютера:

```bash
curl http://192.168.1.100:8080/health
```

Ответ: `{"status": "ok", "adapter": "http", "connected": true, ...}`

## Автозапуск (systemd)

Чтобы мост запускался автоматически при включении робота:

```bash
sudo tee /etc/systemd/system/trust-layer-bridge.service << 'EOF'
[Unit]
Description=Trust Layer Robot Bridge
After=network.target

[Service]
Type=simple
User=noetix
WorkingDirectory=/home/noetix/trust-layer-robot-bridge
Environment=ADAPTER_TYPE=http
Environment=ROBOT_URL=http://127.0.0.1:8000
ExecStart=/usr/bin/python3 -m bridge.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable trust-layer-bridge
sudo systemctl start trust-layer-bridge
```

Проверить статус:

```bash
sudo systemctl status trust-layer-bridge
```
