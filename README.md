# Fabric shell

Десктоп-шелл для i3/X11 на [Fabric](https://github.com/Fabric-Development/fabric): бар, лаунчер вместо rofi,
уведомления, музыка, OSD громкости, календарь и экран блокировки. Дизайн по мотивам macOS, стеклянные
поверхности с блюром picom поверх обоев.

![Бар](screenshots/bar.png)

## Лаунчер

`Super+D`. Пока поле пустое, видна только строка поиска; результаты выезжают по мере ввода.

| Пустой | Приложения | Калькулятор |
|---|---|---|
| ![](screenshots/launcher-empty.png) | ![](screenshots/launcher-apps.png) | ![](screenshots/launcher-calc.png) |

| Сайты | Эмодзи |
|---|---|
| ![](screenshots/launcher-sites.png) | ![](screenshots/launcher-emoji.png) |

| Ввод | Результат |
|---|---|
| `chr`, `cs2`, `lw`, `chrmium` | приложения: префикс, подстрока, затем нечёткий поиск (первые буквы слов, пропущенные буквы); чаще запускаемые выше |
| `gh`, `sj24` | сайты из [`sites.toml`](sites.toml), открываются в браузере |
| `youtube.com`, `https://…` | открыть ссылку |
| `(1+2)*3^2`, `sqrt(2)` | калькулятор, `Enter` копирует результат |
| `lock`, `suspend`, `reboot`, `shutdown`, `logout` | питание; reboot/shutdown/logout просят второй `Enter` |
| `> htop` | команда в терминале (`st`) или в фоне |
| `c:` `c: текст` | история CopyQ, `Enter` делает запись текущим буфером |
| `:fire` | эмодзи, `Enter` копирует |
| что угодно без совпадений | поиск в Chromium |

`↑`/`↓`, `Tab`/`Shift+Tab` — выбор, `Enter` или клик — запуск, `Esc` или клик мимо — закрыть.

### Сайты

```toml
[GitHub]
url = "https://github.com"
icon = "\U000F02A4"        # глиф Nerd Font, необязательно

["Docker Hub"]              # имя с пробелом — в кавычках
url = "https://hub.docker.com"
```

Файл перечитывается при каждом открытии лаунчера, перезапуск не нужен.

## Остальные окна

| Музыка (`Super+M`) | Календарь (клик по часам) | Громкость |
|---|---|---|
| ![](screenshots/music.png) | ![](screenshots/calendar.png) | ![](screenshots/volume-osd.png) |

| Уведомления | Центр уведомлений (`Super+N`) |
|---|---|
| ![](screenshots/notifications.png) | ![](screenshots/notification-center.png) |

Экран блокировки: `Super+Escape` / `Super+L`, пароль проверяется через PAM.

## Установка

Системные пакеты: `python` 3.14, `gtk3`, `python-gobject`, `picom`, `pipewire` (`wpctl`, `pactl`), `lm_sensors`,
`jq`, `copyq`, `st`, `chromium`, шрифты JetBrainsMono Nerd Font и Noto Color Emoji.

```sh
python -m venv ~/.config/fabric/.venv
~/.config/fabric/.venv/bin/pip install -r ~/.config/fabric/requirements.txt
~/.config/fabric/.venv/bin/python ~/.config/fabric/config.py --check   # самопроверка
~/.config/fabric/launch.sh                                             # запуск (заменяет polybar)
```

i3:

```
exec --no-startup-id $HOME/.config/fabric/launch.sh
bindsym $mod+d      exec --no-startup-id $HOME/.config/fabric/toggle-launcher.sh
bindsym $mod+m      exec --no-startup-id $HOME/.config/fabric/toggle-music.sh
bindsym $mod+n      exec --no-startup-id $HOME/.config/fabric/toggle-notifications.sh
bindsym $mod+Escape exec --no-startup-id $HOME/.config/fabric/lock.sh
```

Блюр и анимации picom настраиваются только для окон с классом `Config.py`. Готовый конфиг лежит в
[`picom.conf`](picom.conf) (picom v12+, backend `glx`). Подключить его можно ссылкой:

```sh
ln -sf ~/.config/fabric/picom.conf ~/.config/picom/picom.conf
pkill picom; picom -b
```

Окна шелла обрезаны X-shape по форме панелей (`MonitorWindow.clip_to`), поэтому блюр виден только под
ними. Если блюр пропал после подключения или отключения монитора, перезапустите picom.

## Устройство

`config.py` собирает модули из `modules/` (окна), которые берут данные из `services/` и строятся на
`shared/` (базовые окна, виджеты). Стили — `style.css` и токены `design.toml` / `styles/tokens.css`.
Подробно — в [ARCHITECTURE.md](ARCHITECTURE.md).
