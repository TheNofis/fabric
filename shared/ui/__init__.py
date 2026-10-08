"""UI kit for the shell's panels. Build panels from these instead of raw Box/Button trees.

Styled in styles/ui.css (.ui-*); module styles may override per panel.

Components:
    panel(*children)                     layout.py   panel root: vertical, spacing 20, .popup.ui-panel
    header(title, end=None)              layout.py   section kicker, optional widget on the right (switch, detail)
    section(kicker, detail, value,       layout.py   stat section: header, big value with meta values on its
            meta, *rows)                             baseline, then meters and detail rows (sysmon, Claude)
    detail_row(label, value, bar=None)   layout.py   secondary line of a section, optional thin meter below
    stat_value(value="")                 layout.py   small value for a section's meta or a detail row
    big_value()                          labels.py   big light numeral heading a section
    amount(value, unit)                  labels.py   markup: number + small dimmed unit ('6.2 GB')
    check(on=True)                       labels.py   accent check for the selected row
    row_list()                           rows.py     column that holds list_rows
    list_row(icon, label, end, ...)      rows.py     device/network row: icon, ellipsized title, end widget;
                                                     no on_clicked = insensitive, on_menu = right click
    lift_inner(row)                      rows.py     keeps buttons inside a clickable row clickable
    button(label, on_clicked, primary=)  buttons.py  dialog/form button; primary = accent fill
    icon_button(glyph, on_clicked, ...)  buttons.py  glyph button that never takes focus
    nav_button(glyph, on_clicked)        buttons.py  month arrow
    Confirm(button, on_confirm, ...)     buttons.py  destructive click: the first arms, the second acts; 3s or
                                                     leaving disarms
    switch(on_change, *classes)          controls.py on/off toggle (Gtk.Switch, already shown)
    slider(*classes, on_change=, ...)    controls.py volume-style Scale; on_change throttled to 50ms while dragging,
                                                     sync(value) follows the backend; flag "dim" when muted
    meter(*classes, glide=True)          controls.py usage bar (Gtk.ProgressBar), "thin" for secondary;
                                                     flag "warn"/"alert" to tint it; glides to new values
    Glider(container, thumb_class,       segmented.py CSS thumb under a container's children; to(index) glides it
           follow=None)                              there (position and size), follow="selected" tracks the
                                                     flagged child by itself; container draws no background
    Segmented(options, *classes)         segmented.py options side by side, the picked one under a thumb;
                                                     select(option) glides it across, labels crossfade
    MonthView(on_back, on_shift,         month.py    month title, arrows, weekday heads and the sliding grid;
              on_pick)                               render(month, days, today, direction, away, marks)
    DateHeading()                        month.py    big day numeral beside weekday and a detail line
    HintEntry(hint, *classes)            inputs.py   Entry with a placeholder that stays while focused (.entry)
    password_entry(on_activate)          inputs.py   masked Entry, Enter calls on_activate
    password_field(entry, *extra)        inputs.py   rounded field: lock icon, entry, extra widgets

Primitives (text, line, wrapped, say, css, flag, slide) stay in shared/widgets.py.
"""

from shared.ui.buttons import Confirm, button, icon_button, nav_button
from shared.ui.controls import Slider, meter, slider, switch
from shared.ui.labels import amount, big_value, check
from shared.ui.inputs import HintEntry, password_entry, password_field
from shared.ui.layout import detail_row, header, panel, section, stat_value
from shared.ui.month import DateHeading, MonthView
from shared.ui.rows import lift_inner, list_row, row_list
from shared.ui.segmented import Glider, Segmented

__all__ = [
    "Confirm", "DateHeading", "Glider", "HintEntry", "MonthView", "Segmented", "Slider", "amount", "big_value", "button", "check",
    "detail_row", "header", "icon_button", "lift_inner", "list_row", "meter", "nav_button", "panel", "password_entry", "password_field",
    "row_list", "section", "slider", "stat_value", "switch",
]
