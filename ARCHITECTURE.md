# Архитектура Fabric-shell

Документ описывает текущий код и целевое направление развития. Ориентиром
служит архитектура [Tsumiki](https://github.com/rubiin/tsumiki): приложение
собирает независимые модули, модули используют общие сервисы и UI-примитивы,
а стили и конфигурация не смешиваются с системной логикой.

Мы не копируем Tsumiki целиком: у нас один профиль рабочего стола, i3/X11,
Fabric и собственная композиция по мотивам macOS. TOML-схема на сотни опций,
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
      │   ├─ pam.py                     проверка пароля через libpam (ctypes)
      │   ├─ polkit.py                  polkit authentication agent (D-Bus + PolkitAgent.Session)
      │   ├─ keyring.py                 gnome-keyring system prompter (D-Bus + Gcr.SecretExchange)
      │   ├─ askpass.py                 SSH_ASKPASS: askpass.sh → D-Bus → окно пароля
      │   └─ tether.py                  тексты iPhone через tetherd (Messages и коды в уведомлениях)
      ├─ shared/                        переиспользуемые UI-кирпичи (ui/month.py: MonthView + month_start/grid_days)
      │   ├─ constants.py               пути и layout-метрики (gaps, bar height)
      │   ├─ widgets.py                 text/stat/island, hover_reveal/slide, run, audio helpers
      │   └─ window.py                  MonitorWindow, PopupWindow
      └─ modules/                       UI-модули: папка на модуль (см. «Контракт модуля»)
          ├─ base.py                    Module: абстрактный класс всех модулей
          ├─ calendar/                  календарь на каждом мониторе
          ├─ sysmon/                    панель системного монитора
          ├─ sound/                     панель звука: выход/вход, громкость, устройство (logic: pactl → devices)
          ├─ network/                   панель сети: Wi-Fi и Ethernet (logic: разбор nmcli)
          ├─ claude/                    лимиты Claude: слот в баре + панель; данные — usage.py (поток в процессе)
          ├─ display/                   яркость, контраст, гамма, теплота, пресеты
          ├─ bar/                       status bar + tray; workspaces.py — i3 IPC в процессе; панели из shell.modules
          ├─ music/                     music popup; player.py — MPRIS по D-Bus сигналам (logic: время, обложка)
          ├─ volume_osd/                OSD: громкость и раскладка
          ├─ notifications/             popup + notification center
          │   ├─ record.py              NotificationRecord, чтение с шины, история на диске
          │   ├─ card.py                карточка (popup и история)
          │   └─ hub.py                 NotificationHub: стек попапов, центр, таймауты, DND
          ├─ launcher/                  лаунчер (замена rofi); источники — services/launcher.py
          ├─ dayline/                   Super+C: календарь с заметками и напоминаниями
          │   ├─ times.py               разбор времени, шаг, повторы, «Today»: чистый Python
          │   ├─ store.py               Note, Notes (→ ~/.local/share/dayline/notes.json)
          │   ├─ icloud.py              Sync с iCloud Reminders (pyicloud); вход: python -m modules.dayline.icloud login
          │   └─ window.py              панель
          ├─ messages/                  Super+T: тексты iPhone
          │   ├─ format.py              номера, дни, поиск: чистый Python
          │   └─ window.py              панель
          ├─ voice/                     оверлей голосового ввода
          ├─ lock/                      экран блокировки
          └─ auth/                      окно пароля для polkit, gnome-keyring и ssh askpass

scripts/                                 PipeWire, nmcli, GPU, голос → JSON-строки
style.css                                порядок @import = каскад: styles/{tokens,ui,base}.css, modules/*/style.css, styles/buttons.css
design.toml, styles/tokens.css           токены визуальной системы
sites.toml                               сайты для лаунчера
```

Зависимости идут только вниз: `config.py → modules → shared → services`.

### Контракт модуля

Каждый модуль — папка `modules/<name>/` и класс-наследник `Module` (`modules/base.py`):

```text
modules/<name>/
  __init__.py   class <Name>(Module): name, action, build(), при необходимости activate()/check()
  window.py     вид: окна и виджеты на базах из shared/window.py
  logic.py      чистые функции без GTK + check() (если они есть; у больших модулей — по смыслу:
                dayline/times.py, notifications/record.py, messages/format.py)
  style.css     селекторы модуля; подключается строкой @import в style.css
```

```python
class Dayline(Module):
    name = "dayline"
    action = "toggle-dayline"          # D-Bus экшен (toggle-*.sh) → activate(), по умолчанию toggle окна

    def build(self) -> list[DaylineWindow]:
        self.notes = Notes()
        self.window = DaylineWindow(self.shell.monitors[0], self.shell.clock, self.notes)
        return [self.window]

    @staticmethod
    def check() -> None:               # без дисплея; config.py --check запускает check() всех модулей
        times.check()
```

- `config.py::MODULES` — единственный список модулей; порядок = порядок сборки (bar после
  панелей, которые он открывает; messages после notifications). `Shell` строит каждый в
  `shell.modules[name]`, кладёт результат `build()` в `module.windows`, регистрирует `action`.
- Общие потоки (`clock`, `system`, `audio`, `network`, `keyboard`, `backlight`, …) — поля `Shell`.
  Другой модуль — только через `self.shell.modules[...]` (`modules["claude"].slot(i)`,
  `modules["notifications"].hub`); модули не импортируют друг друга. Модуль, который строится
  позже, читается лениво (лямбдой).
- Сервис, нужный одному модулю, живёт в его папке (`dayline/store.py`); общий — в `services/`.
- Файл, перерастающий ~400 строк, делится по смыслу внутри папки модуля.
- Self-check: `def check()` в файле, модуль вызывает его из `Module.check()`. Весь набор —
  `.venv/bin/python config.py --check`.

**Новый модуль:** папка с `__init__.py` + `window.py` (+ `style.css`), класс `Module`,
строка в `MODULES`, строка `@import` в `style.css`; для хоткея — `toggle-<name>.sh` и бинд i3.

Исключение из «зависимости вниз»: `services/launcher.py` берёт из `shared` пути и
`run`/`copy_text`, потому что строки лаунчера сами запускают процессы и пишут в буфер.

`launch.sh` запускает Fabric через `config.py`. Legacy Eww-файлы удалены; в
runtime остаётся только Fabric implementation.

### Уведомления: что именно перенесено

Notification manager перенесён на Fabric на уровне UI и lifecycle:

- `modules/notifications/hub.py::NotificationHub` создаёт popup stack и notification center, ведёт DND;
- `modules/notifications/card.py::NotificationCard` отвечает за карточку, actions и close;
- `modules/notifications/record.py` читает уведомление с шины и хранит историю;
- класс `Notifications(Module)` в `modules/notifications/__init__.py` собирает hub и отдаёт его окна;
- `fabric.notifications.Notifications` принимает D-Bus notifications;
- окна создаются как Fabric `X11Window` и входят в `Application("fabric-shell", ...)`.

Старый Eww D-Bus backend и его control scripts удалены. Текущий runtime
уведомлений — только Fabric.

### Ответственность текущих частей

| Часть | Отвечает за | Не должна делать |
| --- | --- | --- |
| `launch.sh` | Python runtime и запуск | строить UI или хранить state |
| `Shell` | общий контекст, state streams и окна | содержать логику каждого модуля |
| `modules/<name>/` | одну фичу: окна, её логику и стили | читать окружение напрямую, импортировать другой модуль |
| `MODULES` в `config.py` | состав и порядок модулей | динамически загружать неизвестный код |
| `scripts/` | адаптацию ОС к JSON/командам | знать GTK/Fabric widgets |
| `style.css` | порядок стилей (каскад) | содержать правила модулей |
| `design.toml` | цвета, размеры, spacing, typography, motion и layers | описывать поведение модулей |

### Базовые классы (точки расширения)

| База | Даёт | Наследник реализует |
| --- | --- | --- |
| `Module` | место в `shell.modules`, D-Bus экшен, `windows`, self-check | `name`, `build()`; по желанию `action`, `activate()`, `check()` |
| `State` | `value`, `subscribe()`, `emit()` (только при изменении) | когда вызывать `emit()` |
| `PollingState(State)` | GLib-таймер, `tick()` | `interval` (мс) и `read()` |
| `JsonState(State)` | скрипт → JSON-строки, respawn, `stop_all()` | путь к скрипту и default |
| `MonitorWindow` | окно на конкретном мониторе | содержимое |
| `PopupWindow(MonitorWindow)` | popup/dialog, скрыт по умолчанию, `toggle()` | geometry, margin, child |
| `OverlayWindow(MonitorWindow)` | всплывает без фокуса (OSD, попапы уведомлений), вне управления i3 | geometry, margin, child |

Новый источник данных с опросом:

```python
class BatteryState(PollingState):
    interval = 5000

    def read(self) -> dict:
        return {"percent": int(Path("/sys/class/power_supply/BAT0/capacity").read_text())}
```

Окна, которые появляются сами (не по клику), — только `OverlayWindow`: окно под
управлением i3 при показе получает фокус, и i3 переносит курсор на его монитор.

Новый popup — `class FooWindow(PopupWindow)` с `geometry`/`margin`/`child`;
дочерние виджеты, чью видимость меняет state, помечаются `set_no_show_all(True)`.
UI-хелперы: `hover_reveal()` + `slide()` (раскрытие по наведению),
`volume_icon()`/`volume_text()`/`toggle_mute()`, `stat()`, `island()`.

## 2. Принципы

1. **State отдельно от view.** Скрипт или сервис публикует данные, модуль
   подписывается и обновляет виджеты.
2. **Один источник истины.** i3, PipeWire, D-Bus и MPRIS вызываются в
   `scripts/` или `services/`, а не дублируются в UI-классах.
3. **Модули независимы.** Модуль получает shell и возвращает окна; другой модуль он
   видит только через `shell.modules`, а не импортом.
4. **События вместо polling, где возможно.** Polling оставляем для часов и
   метрик без событийного API.
5. **Дизайн — контракт.** Accent (blue) — выделение и фокус, red — проблему/
   mute/overheat, cyan — caps и вторичное состояние. Слайдеры и прогресс белые,
   кнопки без фона до hover. Текст — Inter, цифры в баре — JetBrains Mono.
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
  base.py                       Module
  <name>/                       модуль-папка: __init__ (Module), window, logic, style.css
  overview/                     будущий overview/quick settings
services/
  state.py                      JsonState, ClockState, SystemState
  monitors.py                   Monitor и xrandr discovery
  audio.py, media.py            PipeWire/PulseAudio и MPRIS
  notifications.py              Fabric notification service
  workspaces.py                 i3 workspace service
shared/
  widgets.py                    text/stat/island и GTK helpers
  ui/                           UI-кит панелей (список компонентов в __init__.py), стили в styles/ui.css
  window.py                      MonitorWindow и lifecycle
  state.py                       parsing, subscriptions, teardown
styles/
  tokens.css                    palette, radius, spacing, typography
  ui.css                        UI-кит: классы .ui-*
  base.css, buttons.css         рамка шелла и общие кнопки (стили модулей — в их папках)
tests/
  test_state.py, test_scripts.py
```

### `app`

`app` знает, какие модули включены, но не знает их внутреннюю разметку. Сейчас это
`Shell` и `MODULES` в `config.py`:

```python
shell = Shell()                       # потоки + for cls in MODULES: build()
Application("fabric-shell", *shell.windows).run()
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

Модуль — класс `Module` + view + logic + module styles (см. «Контракт модуля»). Он не создаёт singleton,
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

Фон один — `bg`, от него два уровня стекла: `bg_bar` (бар) и `bg_panel` (все
остальные панели: попапы, лаунчер, OSD, уведомления, тултипы). Прозрачности
задаются в `[opacity]` design.toml (`bar`, `panel`). `bg_critical` —
единственное намеренное отличие: оно обозначает состояние critical, а не
отдельную тему.

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
- ✅ новый модуль: папка, класс `Module`, строка в `MODULES` (см. «Контракт модуля»).

### Этап E — polish

- hot reload CSS и безопасных параметров;
- lazy import тяжёлых модулей;
- metrics/logging запуска и обновления state;
- документация shortcuts, module API и design tokens.

## 7. Тестирование и критерии готовности

- `--check` запускает `check()` каждого модуля и фиксирует имена D-Bus экшенов;
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
