# Trust Layer Robot Bridge

Мост между роботом Noetix N2 и системой Trust Layer.
Устанавливается на робота (или локально для тестирования).

## Быстрый старт

### Режим симуляции (без робота)

```bash
pip install -r requirements.txt
python -m bridge.main
```

Мост запустится на `http://localhost:8080` с mock-адаптером.

### Режим реального робота

```bash
ADAPTER_TYPE=http ROBOT_URL=http://192.168.1.100:8000 python -m bridge.main
```

### Docker

```bash
docker build -t trust-layer-bridge .
docker run -p 8080:8080 -e ADAPTER_TYPE=mock trust-layer-bridge
```

## API

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/health` | Статус моста |
| GET | `/robot/state` | Телеметрия робота |
| POST | `/robot/move` | Отправить скорость `{vx, vy, wz}` |
| POST | `/robot/stop` | Аварийная остановка |
| GET | `/robot/reasoning` | Решения safety pipeline |
| POST | `/scenario/inject` | Внедрить тестовые условия |
| POST | `/scenario/clear` | Сбросить к нормальному состоянию |

## Архитектура

```
bridge/
├── main.py            # FastAPI приложение
├── http_adapter.py    # Адаптер для Noetix N2 HTTP API
├── mock_adapter.py    # Симуляция (без робота)
└── safety_pipeline.py # Локальные проверки безопасности
```

### Safety Pipeline

Каждая команда движения проходит через проверки:
1. **Батарея** <10% → DENY (BATT-001)
2. **Наклон** >20° → DENY (TILT-001)
3. **Человек** <1.5м → DENY (HUMAN-001), <2.5м → LIMIT (HUMAN-002)
4. **Скорость** >0.8 м/с → LIMIT (SPEED-001)
5. **Угловая скорость** >1.0 рад/с → LIMIT
6. **Препятствие** <0.5м → DENY (OBS-001)

## Переменные окружения

| Переменная | По умолчанию | Описание |
|------------|-------------|----------|
| `ADAPTER_TYPE` | `mock` | `mock` или `http` |
| `ROBOT_URL` | `http://192.168.1.100:8000` | URL API робота |
| `BRIDGE_PORT` | `8080` | Порт моста |
| `POLL_HZ` | `10` | Частота опроса телеметрии |
