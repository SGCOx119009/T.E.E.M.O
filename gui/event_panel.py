"""gui/event_panel.py — el Event Stream, a la vista.

QUÉ ES ESTO
    La tabla de la FASE 12: cada paso de herramienta que ha hecho el
    asistente, con su operador, su acción, **cuánto tardó** y cómo
    salió. Con los tres filtros del plan: por turno, por operador y solo
    errores.

POR QUÉ ESTÁ EN UN MÓDULO PROPIO
    `gui/window.py` tiene doscientas sesenta y cinco kilobytes y ya
    contiene la cara, el cajón, la barca y el resto. Un panel de tabla
    con sus filtros son doscientas líneas que no tienen nada que ver con
    la cara, y metidas en el archivo grande se pierden.

LAS REGLAS QUE SIGUE
    * Los widgets se crean **en el hilo de Qt**: este panel se construye
      dentro del cajón, que se construye en el hilo de la interfaz.
    * Los colores salen de la paleta `C` y solo de ahí.
    * Las fuentes salen de `gui/style.py`.
    * Los datos vienen del bus (`core/eventstream.py`), que ya viene
      **enmascarado**: aquí no se vuelve a enmascarar nada, porque lo que
      se ve es lo que el bus tiene.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QHeaderView,
                             QLabel, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

from core import eventstream
from core.localization import t as _t
from gui.style import px_font
from gui.theme import C

#: How many steps the table shows at once.
ROWS = 200


class EventStreamPanel(QWidget):
    """The steps of the assistant, with their latency, and three filters."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(5)

        # ── the filters ──────────────────────────────────────────────────────
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self._operator = QComboBox()
        self._operator.setFixedHeight(26)
        self._operator.setFont(px_font(9))
        self._operator.setStyleSheet(f"""
            QComboBox {{
                background: {C.INPUT_BG}; color: {C.WHITE};
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 2px 6px;
            }}
            QComboBox QAbstractItemView {{
                background: {C.PANEL2}; color: {C.WHITE};
                selection-background-color: {C.PRI_DIM};
            }}
        """)
        self._operator.currentIndexChanged.connect(self.refresh)
        row.addWidget(self._operator, stretch=1)

        self._errors = QCheckBox(_t("events.filter.errors"))
        self._errors.setFont(px_font(9))
        self._errors.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._errors.stateChanged.connect(self.refresh)
        row.addWidget(self._errors)

        self._turn = QCheckBox(_t("events.filter.turn"))
        self._turn.setFont(px_font(9))
        self._turn.setChecked(True)
        self._turn.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._turn.stateChanged.connect(self.refresh)
        row.addWidget(self._turn)
        root.addLayout(row)

        # ── the table ────────────────────────────────────────────────────────
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels([
            _t("events.col.step"),
            _t("events.col.operator"),
            _t("events.col.action"),
            _t("events.col.duration"),
            _t("events.col.status"),
        ])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self._table.setFont(px_font(9))
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setStyleSheet(f"""
            QTableWidget {{
                background: {C.INPUT_BG}; color: {C.WHITE};
                border: 1px solid {C.BORDER}; border-radius: 2px;
                gridline-color: {C.BORDER};
            }}
            QTableWidget::item {{ padding: 1px 4px; }}
            QHeaderView::section {{
                background: {C.PANEL2}; color: {C.TEXT_DIM};
                border: none; border-bottom: 1px solid {C.BORDER};
                padding: 3px 4px;
            }}
        """)
        root.addWidget(self._table, stretch=1)

        self._empty = QLabel(_t("events.empty"))
        self._empty.setFont(px_font(9))
        self._empty.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        root.addWidget(self._empty)

        self._last_operators: list[str] = []

    # ── the data ────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Fill the table with what the bus holds, after the filters.

        It runs on the thread of Qt: the window calls it from a signal,
        and a signal from another thread is delivered here."""
        try:
            self._fill()
        except Exception:                               # noqa: BLE001
            # A panel that fails to draw is an empty panel, not a crash of
            # the program that was drawing it.
            pass

    def _fill(self) -> None:
        operator = self._operator.currentData()
        errors_only = bool(self._errors.isChecked())
        turn = eventstream.current_turn() if self._turn.isChecked() else None
        events = eventstream.events(
            turn_id=turn,
            operator=operator if operator else None,
            errors_only=errors_only,
            limit=ROWS,
        )
        self._sync_operators()

        self._table.setRowCount(len(events))
        for row, event in enumerate(events):
            self._table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
            self._table.setItem(row, 1, QTableWidgetItem(event.operator))
            self._table.setItem(row, 2, QTableWidgetItem(event.action))
            self._table.setItem(row, 3, QTableWidgetItem(f"{event.duration_ms} ms"))
            state = QTableWidgetItem(
                _t("events.state.ok") if event.ok else _t("events.state.bad"))
            # THE DETAIL IS IN THE HOVER, not in the cell: the cell says what
            # happened, and the reason for it is one hover away. The reason
            # is a code of the tool, and a table full of codes is a table
            # nobody reads.
            state.setToolTip(event.error or event.summary())
            self._table.setItem(row, 4, state)
            # THE COLOUR SAYS THE STATE: green for what went well, red for
            # what did not. And it comes from the palette, like every other
            # colour in this product.
            colour = QColor(C.GREEN if event.ok else C.ERR)
            for column in range(5):
                item = self._table.item(row, column)
                if item is not None:
                    item.setForeground(colour)
        self._empty.setVisible(not events)

    def _sync_operators(self) -> None:
        """Keep the list of operators in the filter up to date.

        The list is only rebuilt when it changes: a combo that is rebuilt
        on every event closes itself while the user is choosing."""
        names = eventstream.operators()
        if names == self._last_operators:
            return
        self._last_operators = names
        current = self._operator.currentData()
        self._operator.blockSignals(True)
        self._operator.clear()
        self._operator.addItem(_t("events.all"), "")
        for name in names:
            self._operator.addItem(name, name)
        index = self._operator.findData(current)
        self._operator.setCurrentIndex(index if index >= 0 else 0)
        self._operator.blockSignals(False)
