"""Music popup driven by scripts/music.sh (MPRIS)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.image import Image
from gi.repository import GdkPixbuf, GLib, Gtk, Pango

from services.monitors import Monitor
from services.state import JsonState
from shared.constants import POPUP_TOP, SCRIPTS
from shared.widgets import css, run, text
from shared.window import PopupWindow


def local_art_path(value: str) -> str | None:
    if value.startswith("file://"):
        value = unquote(urlparse(value).path)
    if value.startswith("/") and Path(value).is_file():
        return value
    return None


class MusicWindow(PopupWindow):
    def __init__(self, monitor: Monitor, music: JsonState):
        self.status = text("Stopped", "music-popup-kicker", xalign=0)
        self.track = text("No media player", "music-popup-title", xalign=0)
        self.artist = text("Start a player to see track details", "music-popup-artist", xalign=0)
        self.play = Button(label="󰐊", style_classes=("music-button", "music-main", "music-control-icon"))
        self.progress = Gtk.ProgressBar()
        css(self.progress, "music-progress")
        self.elapsed = text("0:00", "music-time")
        self.duration = text("0:00", "music-time")
        self.note = text("󰝚", "music-disc-icon")
        self.cover = Image(size=(42, 42), style_classes=("music-cover",), visible=False)
        for art_widget in (self.note, self.cover):
            art_widget.set_no_show_all(True)  # set_art() owns their visibility
        self.art_key: tuple[str, float] | None = None
        for label in (self.track, self.artist):
            # max_width_chars caps the natural width so long titles ellipsize
            # inside the fixed popup instead of widening the window.
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(1)

        def control(action: str) -> Callable[..., None]:
            return lambda *_: run(str(SCRIPTS / "music.sh"), action)

        self.play.connect("clicked", control("play-pause"))
        popup = Box(
            orientation="v",
            spacing=14,
            style_classes=("music-popup",),
            children=[
                Box(
                    spacing=10,
                    style_classes=("music-popup-head",),
                    children=[
                        Box(style_classes=("music-disc",), children=[self.note, self.cover]),
                        Box(
                            orientation="v",
                            spacing=2,
                            h_expand=True,
                            children=[self.status, self.track, self.artist],
                        ),
                        Button(label="×", style_classes=("music-close",), on_clicked=lambda *_: self.hide()),
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
        self.clip_to(16, popup)
        # music.sh polls MPRIS every second; only run it while the popup is shown.
        self.connect("show", lambda *_: music.start())
        self.connect("hide", lambda *_: music.stop())
        music.subscribe(self.update)

    def update(self, value: dict[str, Any]) -> None:
        status = str(value.get("status", "Stopped"))
        self.status.set_text("NOW PLAYING" if status == "Playing" else status.upper())
        self.track.set_text(str(value.get("title", "Unknown track")))
        self.track.set_tooltip_text(self.track.get_text())
        self.artist.set_text(str(value.get("artist", "Unknown artist")))
        self.set_art(local_art_path(str(value.get("art", ""))))
        self.play.set_label("󰏤" if status == "Playing" else "󰐊")
        length = max(int(value.get("length", 1)), 1)
        self.progress.set_fraction(min(max(int(value.get("position", 0)) / length, 0), 1))
        self.elapsed.set_text(str(value.get("elapsed", "0:00")))
        self.duration.set_text(str(value.get("duration", "0:00")))

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
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(art, 42, 42, True)
            except GLib.Error:
                pass
        if pixbuf:
            self.cover.set_from_pixbuf(pixbuf)
        self.cover.set_visible(pixbuf is not None)
        self.note.set_visible(pixbuf is None)


def build(context: Any) -> list[Any]:
    context.music_window = MusicWindow(context.monitors[0], context.music)
    return [context.music_window]
