# Журнал рішень

Хронологічний запис значущих технічних рішень і знайдених багів.

## Виявлення реальної схеми gRPC (не за здогадками)

На старті проекту помилково припускалось, що Starlink Mini — єдиний
пристрій. Живі виклики `grpcurl describe` на dish (`192.168.100.1:9200`)
і router (`192.168.1.1:9000`) підтвердили: це два окремі логічні
пристрої з різними `DeviceInfo`, різними enum станів оновлення
(`SoftwareUpdateState` vs `WifiSoftwareUpdateState`), різними наборами
alert-полів (`DishAlerts` 19 полів vs `WifiAlerts` 21 поле). Уся
подальша логіка (`starlink_client.py`, `labels.py`) побудована на
цих підтверджених схемах, не на припущеннях.

**Практика, що склалась**: для будь-яких технічних даних (розміри
плат, protobuf-схеми, назви enum) — підтверджувати через `grpcurl
describe`/web_search/фото, не вигадувати правдоподібні числа.

## Баг: конфлікт параметра `timeout` в Telegram API

**Симптом**: `TypeError: got multiple values for argument 'timeout'`,
бот не отримував жодних вхідних команд, помилка тихо гасилась у
`except Exception` кожні 5с.

**Причина**: `_api_call(method, token, timeout, **params)` мав
формальний параметр `timeout`, а виклик `getUpdates` одночасно
передавав HTTP-таймаут позиційно і Telegram API параметр
`timeout=POLL_TIMEOUT_SEC` (long polling) як іменований — колізія.

**Виправлення**: перейменовано внутрішній параметр на `http_timeout`.

## NoNewPrivileges вимкнено на webui та shutdown-button сервісах

`starlink-webui.service` і `starlink-shutdown-button.service`
навмисно **без** `NoNewPrivileges=true` — обидва викликають
`sudo systemctl reboot/poweroff` (ручний reboot/shutdown Pi через
веб-інтерфейс і через фізичну кнопку). `NoNewPrivileges` на рівні
ядра блокує будь-який `sudo` незалежно від `/etc/sudoers`, тож із
цим прапорцем ці виклики не спрацювали б.

`starlink-monitor.service` лишається з `NoNewPrivileges=true` —
той процес sudo не викликає.

## Баг: signature_phrases.txt read-only при веб-редагуванні

**Симптом**: `[Errno 30] Read-only file system` при спробі зберегти
фрази через веб-інтерфейс.

**Причина**: `ProtectSystem=strict` робить всю ФС read-only, крім
`ReadWritePaths`, куди спочатку входив лише `/var/lib/starlink-monitor`,
не `/opt/starlink-monitor/app/signature_phrases.txt`.

**Виправлення**: додано конкретний файл (не весь `/opt/starlink-monitor`)
у `ReadWritePaths` — мінімальне розширення дозволів.

## Фізична кнопка виключення: окремий процес, не інтеграція в існуючі

**Рішення**: `app/shutdown_button.py` — окремий Python-модуль і
окремий systemd-сервіс, не частина `monitor.py`/`webapp.py`.

**Чому окремо**:
- GPIO-доступ вимагає групу `gpio` і системний `python3-libgpiod`
  (apt, не pip — чистіше на Raspberry Pi OS) — зайва залежність для
  установок без фізичної кнопки
- Вимкнено за замовчуванням (`SHUTDOWN_BUTTON_GPIO_PIN=0`), сервіс
  одразу виходить — не критична помилка, якщо кнопки немає
- `Restart=on-failure` (не `always`) свідомо: чистий вихід при
  вимкненій кнопці не має спричиняти нескінченний рестарт-цикл systemd

**Побічний ефект**: venv тепер створюється з `--system-site-packages`
(щоб бачити системний `gpiod`) — це змінює поведінку venv для *всіх*
пакетів, не лише gpiod (венв тепер бачить будь-який системний Python-
пакет). Прийнятний компроміс, бо альтернатива (pip-встановлення
gpiod з компіляцією C-розширення) менш надійна на слабкому Pi Zero 2 W.

**Третя зміна NoNewPrivileges**: аналогічно до `starlink-webui.service`
(див. розділ вище), `starlink-shutdown-button.service` теж без
`NoNewPrivileges` — потребує sudo для `poweroff`.
