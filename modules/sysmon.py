"""System monitor panel: CPU, memory, GPU and disk, dropped from the bar's stats."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any

import cairo
from fabric.widgets.box import Box
from fabric.widgets.label import Label
from gi.repository import Gtk

from services.monitors import Monitor
from services.state import JsonState
from services import mock
from services.system import SystemState, cpu_model
from shared.constants import SCRIPTS
from shared.ui import amount, big_value, header, meter, panel
from shared.widgets import css, flag, text
from shared.window import BarPanel

HISTORY = 60  # seconds of CPU load in the graph, recorded while the panel is closed too
HOT = 80  # °C, the bar's overheat threshold
FULL = 0.9  # a meter past this fill turns red
GIB = 1073741824


def fill(value: float, total: float, bar: Gtk.ProgressBar) -> None:
    fraction = min(value / total, 1.0) if total else 0.0
    bar.set_fraction(fraction)
    flag(bar, "alert", fraction >= FULL)


def section(kicker: str, detail: Gtk.Widget, value: Label, meta: list[Gtk.Widget], *rows: Gtk.Widget) -> Box:
    return Box(
        orientation="v",
        spacing=6,
        children=[
            header(kicker, detail),
            Box(children=[value, Box(h_expand=True), Box(spacing=12, valign="end", style_classes=("sysmon-meta",), children=meta)]),
            *rows,
        ],
    )


def detail_row(label: str, value: Label, bar: Gtk.ProgressBar) -> Box:
    return Box(
        orientation="v",
        spacing=5,
        style_classes=("sysmon-row",),
        children=[Box(children=[text(label, "sysmon-label"), Box(h_expand=True), value]), bar],
    )


class Graph(Gtk.DrawingArea):
    """CPU load history as an area chart: newest sample on the right, 0-100% bottom to top.
    Colour comes from CSS `color`, so state classes restyle it like any label."""

    def __init__(self, history: deque[float]):
        super().__init__()
        self.history = history
        css(self, "sysmon-graph")
        self.set_size_request(-1, 48)
        self.connect("draw", self.on_draw)

    def on_draw(self, _area: Gtk.DrawingArea, cr: cairo.Context) -> bool:
        width, height = self.get_allocated_width(), self.get_allocated_height()
        color = self.get_style_context().get_color(self.get_state_flags())
        rgb = (color.red, color.green, color.blue)
        cr.set_source_rgba(*rgb, 0.10)
        for level in (0.5, 1.0):  # 50% guide and baseline
            cr.rectangle(0, round((height - 1) * level), width, 1)
        cr.fill()
        step = width / (HISTORY - 1)
        points = [
            (width - (len(self.history) - 1 - index) * step, (height - 2) * (1 - value / 100) + 1)
            for index, value in enumerate(self.history)
        ]
        if len(points) < 2:
            return False
        cr.move_to(*points[0])
        for point in points[1:]:
            cr.line_to(*point)
        line = cr.copy_path()
        cr.line_to(points[-1][0], height)
        cr.line_to(points[0][0], height)
        cr.close_path()
        fill = cairo.LinearGradient(0, 0, 0, height)
        fill.add_color_stop_rgba(0, *rgb, 0.30)
        fill.add_color_stop_rgba(1, *rgb, 0.02)
        cr.set_source(fill)
        cr.fill()
        cr.append_path(line)
        cr.set_source_rgba(*rgb, color.alpha)
        cr.set_line_width(1.5)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.stroke()
        return False


class SystemMonitorWindow(BarPanel):
    def __init__(self, monitor: Monitor, system: SystemState, gpu: JsonState, history: deque[float]):
        try:
            model = "AMD Ryzen 7 5800X" if mock.ENABLED else cpu_model(Path("/proc/cpuinfo").read_text())
        except OSError:
            model = ""

        self.cpu = big_value()
        self.cpu_temp = text("", "sysmon-value")
        self.freq = text("", "sysmon-value")
        self.load = text("", "sysmon-value")
        self.graph = Graph(history)
        self.cores = [meter("thin") for _ in system.value["cores"]]
        cpu = section(
            "CPU",
            text(model, "ui-detail"),
            self.cpu,
            [self.cpu_temp, self.freq, self.load],
            self.graph,
            Box(spacing=4, homogeneous=True, children=self.cores),
        )

        self.memory = big_value()
        self.memory_total = text("", "sysmon-value")
        self.memory_meter = meter()
        self.swap = text("", "sysmon-value")
        self.swap_meter = meter("thin")
        memory = section(
            "Memory",
            Box(),
            self.memory,
            [self.memory_total],
            self.memory_meter,
            detail_row("Swap", self.swap, self.swap_meter),
        )

        self.gpu_name = text("", "ui-detail")
        self.gpu = big_value()
        self.gpu_temp = text("", "sysmon-value")
        self.power = text("", "sysmon-value")
        self.power.set_no_show_all(True)
        self.vram = text("", "sysmon-value")
        self.vram_meter = meter("thin")
        self.gpu_section = section(
            "GPU",
            self.gpu_name,
            self.gpu,
            [self.gpu_temp, self.power],
            detail_row("Video memory", self.vram, self.vram_meter),
        )
        self.gpu_section.set_no_show_all(True)  # shown once gpu.sh reports a card

        self.disk = big_value()
        self.disk_total = text("", "sysmon-value")
        self.disk_temp = text("", "sysmon-value")
        self.disk_meter = meter()
        disk = section("Disk", text("/", "ui-detail"), self.disk, [self.disk_total, self.disk_temp], self.disk_meter)

        super().__init__(
            monitor,
            "sysmon",
            panel(cpu, memory, self.gpu_section, disk),
        )
        self.system = system
        # nvidia-smi only runs while the panel is open; the CPU history is kept by build()
        self.connect("show", lambda *_: (gpu.start(), self.update(system.value)))
        self.connect("hide", lambda *_: gpu.stop())
        system.subscribe(self.update)
        gpu.subscribe(self.update_gpu)

    def update(self, value: dict[str, Any]) -> None:
        if not self.get_visible():
            return
        self.cpu.set_markup(amount(f"{value['cpu']:.0f}", "%"))
        self.cpu_temp.set_text(f"{value['temp']:.0f}°")
        flag(self.cpu_temp, "alert", value["temp"] >= HOT)
        self.freq.set_text(f"{value['freq']:.1f} GHz")
        self.load.set_text(f"load {value['load']:.2f}")
        self.graph.queue_draw()
        for index, (bar, load) in enumerate(zip(self.cores, value["cores"])):
            bar.set_fraction(load / 100)
            bar.set_tooltip_text(f"Core {index + 1}: {load:.0f}%")

        self.memory.set_markup(amount(f"{value['used'] / GIB:.1f}", "GB"))
        self.memory_total.set_text(f"of {value['total'] / GIB:.1f} GB")
        fill(value["used"], value["total"], self.memory_meter)
        self.swap.set_text(f"{value['swap_used'] / GIB:.1f} / {value['swap_total'] / GIB:.1f} GB" if value["swap_total"] else "off")
        fill(value["swap_used"], value["swap_total"], self.swap_meter)

        self.disk.set_markup(amount(f"{value['disk_used'] / GIB:.0f}", "GB"))
        self.disk_total.set_text(f"of {value['disk_total'] / GIB:.0f} GB")
        self.disk_temp.set_text(f"{value['disk_temp']:.0f}°")
        self.disk_temp.set_visible(bool(value["disk_temp"]))
        flag(self.disk_temp, "alert", value["disk_temp"] >= HOT)
        fill(value["disk_used"], value["disk_total"], self.disk_meter)

    def update_gpu(self, value: dict[str, Any]) -> None:
        self.gpu_section.set_visible(bool(value.get("name")))
        if not value.get("name"):
            return
        self.gpu_section.show_all()
        self.gpu_name.set_text(str(value["name"]).removeprefix("NVIDIA "))
        load, temp, power = value.get("load"), value.get("temp"), value.get("power")
        self.gpu.set_markup(amount("–" if load is None else f"{load:.0f}", "%"))
        self.gpu_temp.set_text("–°" if temp is None else f"{temp:.0f}°")
        flag(self.gpu_temp, "alert", (temp or 0) >= HOT)
        self.power.set_visible(power is not None)
        self.power.set_text(f"{power or 0:.0f} W")
        used, total = value.get("mem_used") or 0, value.get("mem_total") or 0
        self.vram.set_text(f"{used / 1024:.1f} / {total / 1024:.1f} GB")
        fill(used, total, self.vram_meter)


def build(context: Any) -> list[Any]:
    history: deque[float] = deque(maxlen=HISTORY)
    if mock.ENABLED:
        history.extend([22, 25, 24, 28, 32, 30, 26, 24, 29, 35, 42, 45, 38, 33, 28, 31, 34, 37] * 3)
    context.system.subscribe(lambda value: history.append(value["cpu"]))
    gpu = JsonState(SCRIPTS / "gpu.sh", {}, autostart=False)
    context.sysmons = [SystemMonitorWindow(monitor, context.system, gpu, history) for monitor in context.monitors]
    return context.sysmons
