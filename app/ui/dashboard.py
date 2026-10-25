"""Home dashboard: everything the owner must act on today, on one screen."""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QScrollArea, QVBoxLayout, QWidget)

from .. import db, pdc, theme

RED, AMBER, GREEN, BLUE, GREY = "#dc2626", "#d97706", "#16a34a", "#2563eb", "#64748b"


def rupees(v):
    return f"₹{v:,.0f}"


class Card(QFrame):
    """A titled white card with a colored accent, a big number and a list of rows."""

    def __init__(self, title, accent):
        super().__init__()
        self.setObjectName("card")
        self.setStyleSheet(f"#card{{background:white;border:1px solid #e2e8f0;"
                           f"border-left:5px solid {accent};border-radius:10px;}}")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 12, 16, 12)
        self.body.setSpacing(4)
        t = QLabel(title)
        t.setStyleSheet(f"font-size:13px;font-weight:600;color:{GREY};text-transform:uppercase;")
        self.body.addWidget(t)
        self.accent = accent

    def clear(self):
        while self.body.count() > 1:
            w = self.body.takeAt(1).widget()
            if w:
                w.hide()                 # disappear at once; don't wait for deleteLater
                w.setParent(None)
                w.deleteLater()

    def big(self, text, sub=""):
        l = QLabel(text)
        l.setStyleSheet(f"font-size:28px;font-weight:700;color:{self.accent};")
        self.body.addWidget(l)
        if sub:
            s = QLabel(sub)
            s.setStyleSheet(f"color:{GREY};font-size:12px;")
            self.body.addWidget(s)

    def row(self, left, right="", color="#0f172a"):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 2, 0, 2)
        a, b = QLabel(left), QLabel(right)
        a.setStyleSheet("font-size:13px;color:#0f172a;")
        b.setStyleSheet(f"font-size:13px;font-weight:600;color:{color};")
        h.addWidget(a, 1)
        h.addWidget(b)
        self.body.addWidget(w)

    def empty(self, text):
        l = QLabel(text)
        l.setStyleSheet(f"color:{GREEN};font-size:13px;")
        self.body.addWidget(l)


def _nice_max(v):
    """Round a chart maximum up to 1, 2 or 5 x 10^n so the grid lines read well."""
    if v <= 0:
        return 1
    mag = 1
    while mag * 10 <= v:
        mag *= 10
    for m in (1, 2, 5, 10):
        if m * mag >= v:
            return m * mag


def short_money(v):
    return f"₹{v / 100000:g}L" if v >= 100000 else f"₹{v / 1000:g}k" if v >= 1000 else f"₹{v:g}"


class BarChart(QWidget):
    """Daily sales bars, drawn directly (no chart library needed)."""

    def __init__(self):
        super().__init__()
        self.data = []
        self.setMinimumHeight(190)

    def set_data(self, data):
        self.data = data
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        left, top, bottom = 52, 8, 22
        top_v = _nice_max(max((v for _, v in self.data), default=0))
        plot_h = h - top - bottom
        p.setFont(self.font())
        for i in range(5):
            y = top + plot_h * i / 4
            p.setPen(QPen(QColor("#334155" if theme.is_dark() else "#e2e8f0"), 1))
            p.drawLine(left, int(y), w - 6, int(y))
            p.setPen(QColor(GREY))
            p.drawText(QRectF(0, y - 9, left - 6, 18), Qt.AlignRight | Qt.AlignVCenter, short_money(top_v * (4 - i) / 4))
        n = len(self.data)
        if not n:
            return
        bw = (w - left - 6) / n
        for i, (d, v) in enumerate(self.data):
            bh = plot_h * v / top_v
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#3b82f6" if i == n - 1 else "#1e40af") if theme.is_dark() else QColor("#1d4ed8" if i == n - 1 else "#93c5fd"))
            p.drawRoundedRect(QRectF(left + i * bw + 2, top + plot_h - bh, max(bw - 4, 1), bh), 2, 2)
            if i % 5 == 4 or i == 0:
                p.setPen(QColor(GREY))
                p.drawText(QRectF(left + i * bw - 20, h - bottom + 4, bw + 40, 16), Qt.AlignCenter, f"{d:%d %b}")


class Dashboard(QWidget):
    def __init__(self, con):
        super().__init__()
        self.con = con
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)
        page = QWidget()
        scroll.setWidget(page)
        root = QVBoxLayout(page)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        self.heading = QLabel("Today")
        self.heading.setStyleSheet("font-size:24px;font-weight:700;color:#0f172a;")
        root.addWidget(self.heading)

        self.summary = Card("Today's summary", BLUE)
        self.expiry = Card("Expiring soon", RED)
        self.export = Card("Export / sell today", GREEN)
        self.low = Card("Low stock - reorder", AMBER)
        self.fast = Card("Fast movers (7 days)", GREEN)
        self.slow = Card("Slow movers - discount these", GREY)

        root.addWidget(self.summary)
        self._build_trends(root)
        grid = QGridLayout()
        grid.setSpacing(16)
        for i, c in enumerate([self.expiry, self.export, self.low, self.fast]):
            grid.addWidget(c, i // 2, i % 2)
        root.addLayout(grid)
        self.pdc = Card("PDCs maturing this week", BLUE)
        root.addWidget(self.pdc)
        root.addWidget(self.slow)
        self.loc = Card("Low stock by location", AMBER)
        root.addWidget(self.loc)
        root.addStretch()
        self.refresh()

    def refresh(self):
        from datetime import date
        self.heading.setText(f"Today - {date.today():%A, %d %B %Y}")
        self._summary()
        self._expiry()
        self._export()
        self._low()
        self._fast()
        self._slow()
        self._trends()
        self._loc_low()
        self._pdc()

    def _summary(self):
        s = db.today_summary(self.con)
        c = self.summary
        c.clear()
        trend = ""
        if s["vs_yesterday"] is not None:
            arrow = "▲" if s["vs_yesterday"] >= 0 else "▼"
            trend = f"  {arrow} {abs(s['vs_yesterday']):.0f}% vs yesterday"
        c.big(rupees(s["sales"]), f"{s['bills']} bills   |   Profit {rupees(s['profit'])}{trend}")

    def _expiry(self):
        c = self.expiry
        c.clear()
        for d in (7, 15, 30):
            n, v = db.expiring(self.con, d)
            c.row(f"Within {d} days", f"{n} batches  |  {rupees(v)} at risk",
                  RED if d == 7 else AMBER if d == 15 else GREY)
        for r in db.expiring_list(self.con, 30, 4):
            left = r["days_left"]
            tag = "EXPIRED" if left < 0 else f"{left}d left"
            c.row(f"{r['name']} ({r['batch_no']}) x{r['qty']}", tag, RED if left <= 7 else AMBER)

    def _export(self):
        c = self.export
        c.clear()
        rows = db.export_today(self.con)
        if not rows:
            return c.empty("Nothing marked for export today")
        c.big(str(len(rows)), "batches ready")
        for r in rows[:5]:
            c.row(f"{r['name']} ({r['batch_no']})", f"{r['qty']} pcs")

    def _low(self):
        c = self.low
        c.clear()
        rows = db.low_stock(self.con)
        if not rows:
            return c.empty("All stock levels are healthy")
        c.big(str(len(rows)), "products at or below reorder level")
        for r in rows[:5]:
            c.row(r["name"], f"{r['stock']} left (min {r['reorder_level']})",
                  RED if r["stock"] == 0 else AMBER)

    def _fast(self):
        c = self.fast
        c.clear()
        rows = db.fast_movers(self.con)
        if not rows:
            return c.empty("No sales in the last 7 days")
        for i, r in enumerate(rows, 1):
            c.row(f"{i}. {r['name']}", f"{r['sold']} sold  |  {rupees(r['revenue'])}", GREEN)

    def _slow(self):
        c = self.slow
        c.clear()
        rows = db.slow_movers(self.con)
        if not rows:
            return c.empty("Everything is selling")
        for r in rows:
            exp = r["next_expiry"] or "no expiry"
            c.row(r["name"], f"{r['stock']} in stock  |  {rupees(r['value'])} tied up  |  expires {exp}")

    # ---------- trends: this week vs last week, 30-day chart, top customers / products
    def _build_trends(self, root):
        row = QHBoxLayout()
        row.setSpacing(16)
        self.week = {k: Card(t, BLUE) for k, t in (("sales", "Sales - this week"), ("bills", "Bills - this week"),
                                                    ("profit", "Profit - this week"))}
        for c in self.week.values():
            row.addWidget(c)
        root.addLayout(row)
        self.chart_card = Card("Sales - last 30 days", BLUE)
        self.chart = BarChart()
        self.chart_card.body.addWidget(self.chart)
        root.addWidget(self.chart_card)
        g = QHBoxLayout()
        g.setSpacing(16)
        self.top_c = Card("Top customers (30 days)", BLUE)
        self.top_p = Card("Top products by quantity (30 days)", GREEN)
        g.addWidget(self.top_c)
        g.addWidget(self.top_p)
        root.addLayout(g)

    def _trends(self):
        cmp_ = db.week_compare(self.con)
        for key, card in self.week.items():
            this, last, pct = cmp_[key]
            card.clear()
            card.big(rupees(this) if key != "bills" else f"{this:g}", f"last week: " + (rupees(last) if key != "bills" else f"{last:g}"))
            if pct is None:
                card.row("", "no sales last week to compare", GREY)
            else:
                up = pct >= 0
                card.row("", f"{'▲' if up else '▼'} {abs(pct):.0f}% vs last week", GREEN if up else RED)
        self.chart.set_data(db.daily_sales(self.con, 30))
        self.top_c.clear()
        rows = db.top_customers(self.con)
        if not rows:
            self.top_c.empty("No credit or named-customer sales yet")
        for i, r in enumerate(rows, 1):
            self.top_c.row(f"{i}. {r['name']}", f"{rupees(r['revenue'])}  |  {r['bills']} bill{'s' if r['bills'] != 1 else ''}", BLUE)
        self.top_p.clear()
        rows = db.top_products_qty(self.con)
        if not rows:
            self.top_p.empty("No sales in the last 30 days")
        for i, r in enumerate(rows, 1):
            self.top_p.row(f"{i}. {r['name']}", f"{r['sold']} sold  |  {rupees(r['revenue'])}", GREEN)

    def _pdc(self):
        c = self.pdc
        c.clear()
        rows = pdc.maturing(self.con, 7)
        if not rows:
            return c.empty("No cheques maturing this week")
        rec = sum(r["amount"] for r in rows if r["kind"] == "received")
        iss = sum(r["amount"] for r in rows if r["kind"] == "issued")
        c.big(str(len(rows)), f"to receive {rupees(rec)}   |   to pay {rupees(iss)}")
        for r in rows[:6]:
            d = r["days_left"]
            when = "overdue" if d < 0 else "today" if d == 0 else f"in {d}d"
            c.row(f"{r['party']}  -  cheque {r['cheque_no']}  ({'received' if r['kind'] == 'received' else 'issued'})",
                  f"{rupees(r['amount'])}  |  {when}", RED if d < 0 else AMBER if d == 0 else GREY)

    def _loc_low(self):
        data = db.low_stock_by_location(self.con)
        if len(data) < 2:                      # only the main shop exists: nothing location-specific to show
            self.loc.hide()
            return
        self.loc.show()
        self.loc.clear()
        for name, rows in data:
            self.loc.row(f"{name}", f"{len(rows)} low" if rows else "all fine", AMBER if rows else GREEN)
            for prod, q, lvl, elsewhere in rows[:3]:
                more = f"   ({elsewhere} in other locations)" if elsewhere else ""
                self.loc.row(f"      {prod}", f"{q} left (min {lvl}){more}", GREY)
