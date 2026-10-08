"""Music popup driven by scripts/music.sh (MPRIS)."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.image import Image
from gi.repository import GdkPixbuf, GLib

from services.monitors import Monitor
from services.state import JsonState
from shared.constants import POPUP_TOP, SCRIPTS
from shared.ui import meter
from shared.widgets import line, run, text
from shared.window import PopupWindow


DRIFT_US = 1_500_000  # a report this far off the extrapolated position is a seek, not poll jitter


def clock(us: float) -> str:
    seconds = int(us // 1_000_000)
    return f"{seconds // 60}:{seconds % 60:02d}"


def local_art_path(value: str) -> str | None:
    if value.startswith("file://"):
        value = unquote(urlparse(value).path)
    if value.startswith("/") and Path(value).is_file():
        return bluez_cover(value)
    return None


def bluez_cover(path: str) -> str:
    """bluez pulls the iPhone's cover twice per track; the second pull is iOS's grey placeholder.
    Take the real one: the previous /tmp/sessionN-M file, fetched within the same second."""
    match = re.fullmatch(r"(/tmp/session\d+-)(\d+)", path)
    if not match:
        return path
    first = f"{match[1]}{int(match[2]) - 1}"
    try:
        return first if os.stat(path).st_mtime - os.stat(first).st_mtime < 1 else path
    except OSError:
        return path


class MusicWindow(PopupWindow):
    def __init__(self, monitor: Monitor, music: JsonState):
        self.status = text("Stopped", "music-popup-kicker", xalign=0)
        # line(): long titles ellipsize inside the fixed popup instead of widening the window
        self.track = line("No media player", "music-popup-title")
        self.artist = line("Start a player to see track details", "music-popup-artist")
        # which player the popup drives; click hands off to the next one (PC <-> iPhone)
        self.source = Button(style_classes=("music-source",), tooltip_text="Switch player", visible=False)
        self.source.set_no_show_all(True)  # update() owns its visibility
        self.play = Button(label="󰐊", style_classes=("music-button", "music-main", "music-control-icon"))
        self.progress = meter("thin", "music-progress")
        self.elapsed = text("0:00", "music-time")
        self.duration = text("0:00", "music-time")
        self.note = text("󰝚", "music-disc-icon")
        self.note.set_hexpand(True)  # centre the glyph in the cover-sized disc
        self.cover = Image(size=(64, 64), style_classes=("music-cover",), visible=False)
        for art_widget in (self.note, self.cover):
            art_widget.set_no_show_all(True)  # set_art() owns their visibility
        self.art_key: tuple[str, float] | None = None
        # music.sh reports the position once a second; between reports it is extrapolated per frame
        # (MPRIS: position advances at Rate while Playing), so the bar glides instead of stepping
        self.anchor: tuple[float, float] = (0.0, time.monotonic())  # (position µs, monotonic time)
        self.length, self.playing, self.track_key = 1, False, ""
        self.ticker = 0

        def control(action: str) -> Callable[..., None]:
            return lambda *_: run(str(SCRIPTS / "music.sh"), action)

        self.play.connect("clicked", control("play-pause"))
        self.source.connect("clicked", control("switch"))
        popup = Box(
            orientation="v",
            spacing=14,
            style_classes=("music-popup",),
            children=[
                Box(
                    spacing=14,
                    style_classes=("music-popup-head",),
                    children=[
                        Box(style_classes=("music-disc",), children=[self.note, self.cover]),
                        Box(
                            orientation="v",
                            spacing=2,
                            h_expand=True,
                            children=[Box(spacing=6, children=[self.status, self.source]), self.track, self.artist],
                        ),
                        Button(label="×", style_classes=("music-close",), tooltip_text="Close", on_clicked=lambda *_: self.hide()),
                    ],
                ),
                Box(
                    spacing=8,
                    h_align="center",
                    style_classes=("music-controls",),
                    children=[
                        Button(label="󰒮", style_classes=("music-button", "music-control-icon"), on_clicked=control("previous")),
                        self.play,
                        Button(label="󰒭", style_classes=("music-button", "music-control-icon"), on_clicked=control("next")),
                    ],
                ),
                Box(
                    orientation="v",
                    spacing=6,
                    style_classes=("music-track",),
                    children=[
                        self.progress,
                        Box(
                            style_classes=("music-track-times",),
                            children=[self.elapsed, Box(h_expand=True), self.duration],
                        ),
                    ],
                ),
            ],
        )
        super().__init__(
            monitor,
            title="fabric-music",
            dismissible=True,
            hotkey="m",
            geometry="top",
            margin=f"{POPUP_TOP}px 0px 0px 0px",
            size=(420, -1),
            child=popup,
        )
        self.clip_to(22, popup)
        # music.sh polls MPRIS every second; only run it while the popup is shown.
        self.connect("show", lambda *_: music.start() or self.start_ticker())
        self.connect("hide", lambda *_: music.stop() or self.stop_ticker())
        music.subscribe(self.update)

    def update(self, value: dict[str, Any]) -> None:
        status = str(value.get("status", "Stopped"))
        self.status.set_text("NOW PLAYING" if status == "Playing" else status.upper())
        source = str(value.get("source", ""))
        self.source.set_label(f"{'󰄜' if value.get('phone') else '󰍹'} {source}" + (" 󰓡" if int(value.get("players", 0)) > 1 else ""))
        self.source.set_sensitive(int(value.get("players", 0)) > 1)
        self.source.set_visible(bool(source))
        self.track.set_text(str(value.get("title", "Unknown track")))
        self.track.set_tooltip_text(self.track.get_text())
        self.artist.set_text(str(value.get("artist", "Unknown artist")))
        self.set_art(local_art_path(str(value.get("art", ""))))
        self.play.set_label("󰏤" if status == "Playing" else "󰐊")
        self.length = max(int(value.get("length", 1)), 1)
        self.duration.set_text(str(value.get("duration", "0:00")))
        position, track_key, playing = int(value.get("position", 0)), f"{source}\0{self.track.get_text()}", status == "Playing"
        # re-anchor on a track/state change or a real seek; small differences are poll latency,
        # and snapping to them would kick the bar back and forth every second
        if track_key != self.track_key or playing != self.playing or abs(position - self.position()) > DRIFT_US:
            self.anchor = (position, time.monotonic())
        self.track_key, self.playing = track_key, playing
        self.draw_position()

    def position(self) -> float:
        start, at = self.anchor
        return min(start + (time.monotonic() - at) * 1_000_000 if self.playing else start, self.length)

    def draw_position(self) -> None:
        position = self.position()
        self.progress.set_fraction(min(max(position / self.length, 0), 1))
        if (elapsed := clock(position)) != self.elapsed.get_text():
            self.elapsed.set_text(elapsed)

    def start_ticker(self) -> None:
        if not self.ticker:
            self.ticker = self.add_tick_callback(self.tick)

    def tick(self, *_: Any) -> bool:
        if self.playing:
            self.draw_position()
        return True

    def stop_ticker(self) -> None:
        if self.ticker:
            self.remove_tick_callback(self.ticker)
            self.ticker = 0

    def set_art(self, art: str | None) -> None:
        # update() runs every second (position); decode the cover only when it changes.
        try:
            key = (art, os.stat(art).st_mtime) if art else None
        except OSError:
            key = None
        if key == self.art_key:
            return
        self.art_key = key
        pixbuf = None
        if key:
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(art, 64, 64, True)
            except GLib.Error:
                pass
        if pixbuf:
            self.cover.set_from_pixbuf(pixbuf)
        self.cover.set_visible(pixbuf is not None)
        self.note.set_visible(pixbuf is None)


def build(context: Any) -> list[Any]:
    context.music_window = MusicWindow(context.monitors[0], context.music)
    return [context.music_window]
