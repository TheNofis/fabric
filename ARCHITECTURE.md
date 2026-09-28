# Архитектура Fabric-shell

Документ описывает текущий код и целевое направление развития. Ориентиром
служит архитектура [Tsumiki](https://github.com/rubiin/tsumiki): приложение
собирает независимые модули, модули используют общие сервисы и UI-примитивы,
а стили и конфигурация не смешиваются с системной логикой.

Мы не копируем Tsumiki целиком: у нас один профиль рабочего стола, i3/X11,
Fabric и собственная Everforest-композиция. TOML-схема на сотни опций,
универсальный plugin-loader и десятки абстракций появятся только при реальной
необходимости.

## 1. Текущий runtime

```text
launch.sh
  └─ config.py                         entrypoint: Shell, Application actions, --check
      ├─ services/                      источники данных (без GTK-виджетов)
      │   ├─ state.py                   State, PollingState, JsonState, parse_json
      │   ├─ system.py                  ClockState, SystemState, NetworkState, KeyboardState
      │   ├─ monitors.py                Monitor, xrandr discovery
      │   ├─ launcher.py                источники лаунчера: apps, calc, sites, power, commands, clipboard, emoji
      │   └─ pam.py                     проверка пароля через libpam (ctypes)
      ├─ shared/                        переиспользуемые UI-кирпичи
      │   ├─ constants.py               пути и layout-метрики (gaps, bar height)
      │   ├─ widgets.py                 text/stat/island, hover_reveal/slide, run, audio helpers
      │   └─ window.py                  MonitorWindow, PopupWindow
      └─ modules/                       UI-модули: view + build(context)
          ├─ registry.py                порядок сборки окон
          ├─ calendar.py                календарь на каждом мониторе
          ├─ bar.py                     status bar + workspaces
          ├─ music.py                   music popup
          ├─ volume_osd.py              OSD: громкость и раскладка
          ├─ notifications.py           popup + notification center
          ├─ launcher.py                лаунчер (замена rofi)
          └─ lock.py                    экран блокировки

scripts/                                 i3 / PipeWire / MPRIS → JSON-строки
style.css, design.toml, styles/tokens.css визуальная система
sites.toml                               сайты для лаунчера
```

Зависимости идут только вниз: `config.py → modules → shared → services`.
Модуль не импортирует другой UI-модуль (исключение — `bar` получает
`CalendarWindow` для клика по часам). Второе исключение: `services/launcher.py`
берёт из `shared` пути и `run`/`copy_text`, потому что строки лаунчера сами
запускают процессы и пишут в буфер.

`launch.sh` запускает Fabric через `config.py`. Legacy Eww-файлы удалены; в
runtime остаётся только Fabric implementation.

### Уведомления: что именно перенесено

Notification manager перенесён на Fabric на уровне UI и lifecycle:

- `modules/notifications.py::NotificationHub` создаёт popup stack и notification center;
- `modules/notifications.py::NotificationCard` отвечает за карточки, actions, close и DND;
- `build()` в том же файле подключает модуль через registry;
- `fabric.notifications.Notifications` принимает D-Bus notifications;
- окна создаются как Fabric `X11Window` и входят в `Application("fabric-shell", ...)`.

Старый Eww D-Bus backend и его control scripts удалены. Текущий runtime
уведомлений — только Fabric.

### Ответственность текущих частей

| Часть | Отвечает за | Не должна делать |
| --- | --- | --- |
| `launch.sh` | Python runtime и запуск | строить UI или хранить state |
| `Shell` | общий контекст, state streams и окна | содержать логику каждого модуля |
| `modules/*.py` | сборку конкретных окон | читать окружение напрямую |
| `ModuleRegistry` | порядок и выбор модулей | динамически загружать неизвестный код |
| `scripts/` | адаптацию ОС к JSON/командам | знать GTK/Fabric widgets |
| `style.css` | внешний вид и состояния | выполнять shell-команды |
| `design.toml` | цвета, размеры, spacing, typography, motion и layers | описывать поведение модулей |

### Базовые классы (точки расширения)

| База | Даёт | Наследник реализует |
| --- | --- | --- |
| `State` | `value`, `subscribe()`, `emit()` (только при изменении) | когда вызывать `emit()` |
| `PollingState(State)` | GLib-таймер, `tick()` | `interval` (мс) и `read()` |
| `JsonState(State)` | скрипт → JSON-строки, respawn, `stop_all()` | путь к скрипту и default |
| `MonitorWindow` | окно на конкретном мониторе | содержимое |
| `PopupWindow(MonitorWindow)` | popup/dialog, скрыт по умолчанию, `toggle()` | geometry, margin, child |

Новый источник данных с опросом:

```python
class BatteryState(PollingState):
    interval = 5000

    def read(self) -> dict:
        return {"percent": int(Path("/sys/class/power_supply/BAT0/capacity").read_text())}
```

Новый popup — `class FooWindow(PopupWindow)` с `geometry`/`margin`/`child`;
дочерние виджеты, чью видимость меняет state, помечаются `set_no_show_all(True)`.
UI-хелперы: `hover_reveal()` + `slide()` (раскрытие по наведению),
`volume_icon()`/`volume_text()`/`toggle_mute()`, `stat()`, `island()`.

## 2. Принципы

1. **State отдельно от view.** Скрипт или сервис публикует данные, модуль
   подписывается и обновляет виджеты.
2. **Один источник истины.** i3, PipeWire, D-Bus и MPRIS вызываются в
   `scripts/` или `services/`, а не дублируются в UI-классах.
3. **Модули независимы.** Модуль получает контекст и возвращает окна; он не
   импортирует другой UI-модуль и не меняет registry.
4. **События вместо polling, где возможно.** Polling оставляем для часов и
   метрик без событийного API.
5. **Дизайн — контракт.** Green означает нормальное/фокусированное состояние,
   red — проблему/mute/overheat, cyan — caps и вторичное состояние.
6. **Минимальная конфигурация.** Новая настройка появляется только если её
   нужно менять без редактирования кода или появляется второй профиль.
7. **Безопасное выключение.** Timers, subscriptions и дочерние процессы
   освобождаются при закрытии окна.

## 3. Целевая структура

```text
main.py                         запуск Fabric Application
app/
  context.py                    ShellContext: monitors + services
  actions.py                    toggle/open actions
  bootstrap.py                  registry и запуск
modules/
  registry.py                   ModuleSpec и ModuleRegistry
  bar.py, calendar.py           текущие UI-модули
  music.py, volume_osd.py
  notifications.py
  overview.py                   будущий overview/quick settings
services/
  state.py                      JsonState, ClockState, SystemState
  monitors.py                   Monitor и xrandr discovery
  audio.py, media.py            PipeWire/PulseAudio и MPRIS
  notifications.py              Fabric notification service
  workspaces.py                 i3 workspace service
shared/
  widgets.py                    text/stat/island и GTK helpers
  window.py                      MonitorWindow и lifecycle
  state.py                       parsing, subscriptions, teardown
styles/
  tokens.css                    palette, radius, spacing, typography
  modules/                      module-specific selectors
tests/
  test_registry.py, test_state.py, test_scripts.py
```

### `app`

`app` знает, какие модули включены, но не знает их внутреннюю разметку. Он
создаёт `ShellContext` и передаёт его registry:

```python
context = ShellContext.create()
windows = default_registry().build(context)
Application("fabric-shell", *windows).run()
```

### `services`

Сервис владеет одним внешним источником данных и предоставляет стабильный
контракт:

```python
audio.subscribe(callback)
audio.set_volume(50)
audio.toggle_mute()
```

UI не знает, вызывается ли внутри `wpctl`, `pactl`, D-Bus или mock. Сервисы
возвращают нормализованные Python-значения, а не сырые строки команд.

### `modules`

Модуль состоит из factory/build, view и module styles. Он не создаёт singleton,
не запускает бесконечный shell-loop и не хранит системное состояние внутри GTK
widget.

### `shared`

Сюда попадают только реально повторяющиеся вещи: `MonitorWindow`, безопасный
JSON parsing, подписки, teardown timers, `stat`, `island`, tooltip и общие
CSS-классы. Одноразовую разметку сюда не выносим.

## 4. Жизненный цикл данных

```text
OS / compositor / player
        │
        ▼
scripts/ или services/ ── нормализация ──► state stream
                                                  │
                                                  ▼
                                      module.subscribe(update)
                                                  │
                                                  ▼
                                             GTK widget
```

Каждый stream должен иметь default value, обработку битого JSON, teardown
watcher/timer, отсутствие блокирующего subprocess в GTK loop и один self-check
или unit-тест формата данных.

## 5. Конфигурация и расширение

Сейчас профиль фактически задан кодом и `style.css`. Это нормально для одного
пользователя. Вводить конфигурацию нужно поэтапно:

1. список включённых модулей и monitor placement;
2. размеры, таймауты и shortcuts;
3. TOML/YAML + валидация после появления второго профиля;
4. hot reload только после стабильной схемы.

Цвета, spacing, typography, размеры, скругления, прозрачности, тени,
анимации, stroke widths и z-layers остаются в design tokens. Команды ОС — в
сервисах/скриптах.

Обычная notification surface использует тот же `notification` token, что и
основная панель. `notification_critical` — единственное намеренное отличие:
оно обозначает состояние critical, а не отдельную декоративную тему.

```toml
[shell]
modules = ["bar", "calendar", "music", "volume_osd", "notifications"]

[bar]
height = 40
location = "top"

[notifications]
max_history = 50
```

## 6. План развития

### Этап A — декомпозиция ✅ выполнено

- перенести `JsonState`, `ClockState`, `SystemState` в `services/state.py`;
- перенести `Monitor`, `MonitorWindow`, `read_monitors` в `services/monitors.py`;
- перенести `text`, `css`, `flag`, `stat`, `island` в `shared/widgets.py`;
- оставить compatibility imports, чтобы launch и actions не сломались.

### Этап B — модули отделены от `config.py` ✅ выполнено

- перенести `Bar`, `CalendarWindow`, `MusicWindow`, `VolumeOSD` и
  `NotificationHub` в соответствующие `modules/*.py`;
- передавать зависимости через `ShellContext`, а не импортировать `config`;
- удалить compatibility imports после smoke-теста.

### Этап C — стабилизировать сервисные контракты

- заменить прямые `run(...)` в UI на методы сервисов;
- унифицировать audio/media/workspace adapters;
- добавить fake services для тестов без X11, Fabric и реального аудио;
- закрывать все GLib timers и stream processes при destroy.

### Этап D — конфигурация и пользовательские модули

- добавить enabled modules;
- вынести востребованные параметры в TOML;
- валидировать конфиг при старте;
- добавить новый модуль через один файл, registry entry и test.

### Этап E — polish

- hot reload CSS и безопасных параметров;
- lazy import тяжёлых модулей;
- metrics/logging запуска и обновления state;
- документация shortcuts, module API и design tokens.

## 7. Тестирование и критерии готовности

- registry строит только включённые модули и сохраняет порядок;
- битый JSON не ломает stream и сохраняет последнее корректное значение;
- parser мониторов обрабатывает отрицательные координаты и несколько дисплеев;
- actions не блокируют GTK loop;
- закрытие окна удаляет timers/subscriptions;
- каждый script имеет `once`-режим или отдельный форматный тест;
- smoke-запуск на X11 не создаёт дубликаты окон после перезапуска.

Перед изменением UI достаточно compile/self-check. Перед изменением state или
lifecycle обязателен отдельный тест. Полный smoke-тест требует GTK/Fabric/X11
и выполняется в пользовательской сессии, а не в минимальном CI-контейнере.

## 8. Что сознательно не делаем сейчас

- не копируем все 45+ виджетов Tsumiki;
- не вводим plugin discovery и dependency-injection framework;
- не настраиваем каждый pixel через универсальную schema-driven систему;
- не поддерживаем одновременно Fabric и Eww runtime;
- не создаём abstraction ради одного класса или одного модуля.

К этим решениям возвращаемся при появлении второго desktop-профиля, внешних
contributors или измеримой сложности поддержки.
