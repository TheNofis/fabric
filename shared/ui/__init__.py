"""UI kit for BarPanel dropdowns. Build panels from these instead of raw Box/Button trees.

Styled in styles/ui.css (.ui-*); module styles may override per panel.

Components:
    panel(*children)                     layout.py   panel root: vertical, spacing 20, .popup.ui-panel
    header(title, end=None)              layout.py   section kicker, optional widget on the right (switch, detail)
    big_value()                          labels.py   big light numeral heading a section
    amount(value, unit)                  labels.py   markup: number + small dimmed unit ('6.2 GB')
    check(on=True)                       labels.py   accent check for the selected row
    row_list()                           rows.py     column that holds list_rows
    list_row(icon, label, end, ...)      rows.py     device/network row: icon, ellipsized title, end widget;
                                                     no on_clicked = insensitive, on_menu = right click
    button(label, on_clicked, primary=)  buttons.py  dialog/form button; primary = accent fill
    nav_button(glyph, on_clicked)        buttons.py  month arrow, not focusable
    switch(on_change, *classes)          controls.py on/off toggle (Gtk.Switch, already shown)
    slider(*classes, max_value=100)      controls.py volume-style Scale from 0; flag "dim" when muted
    meter(*classes)                      controls.py usage bar (Gtk.ProgressBar), "thin" for secondary;
                                                     flag "warn"/"alert" to tint it
    password_entry(on_activate)          inputs.py   masked Entry, Enter calls on_activate
    password_field(entry, *extra)        inputs.py   rounded field: lock icon, entry, extra widgets

Primitives (text, css, flag, slide) stay in shared/widgets.py.
"""

from shared.ui.buttons import button, nav_button
from shared.ui.controls import meter, slider, switch
from shared.ui.labels import amount, big_value, check
from shared.ui.inputs import password_entry, password_field
from shared.ui.layout import header, panel
from shared.ui.rows import list_row, row_list

__all__ = ["amount", "big_value", "button", "check", "header", "list_row", "row_list", "meter", "nav_button", "panel", "password_entry", "password_field", "slider", "switch"]
