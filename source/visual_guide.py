"""Transient, read-only button recommendation. Never changes commands or states."""
import tkinter as tk
from tkinter import ttk


class ButtonGuide:
    def __init__(self, top, choose):
        self.top, self.choose = top, choose
        self.current = None
        self.original = None
        self.job = None
        self.closed = False
        top.bind('<Destroy>', self.destroy, add='+')
        self.tick()

    def destroy(self, event):
        if event.widget is not self.top:
            return
        self.closed = True
        if self.job:
            self.top.after_cancel(self.job)
            self.job = None

    def child_open(self, widget):
        for child in widget.winfo_children():
            if isinstance(child, tk.Toplevel) and child.winfo_viewable():
                return True
            if self.child_open(child):
                return True
        return False

    def paint(self, button):
        if button is not None and (not button.winfo_viewable() or button.instate(['disabled'])):
            button = None
        if button is self.current:
            return
        if self.current is not None and self.current.winfo_exists():
            self.current.configure(style=self.original)
        self.current, self.original = button, None
        if button is not None:
            self.original = str(button.cget('style'))
            base = self.original or 'TButton'
            name = 'Guide.' + base
            style = ttk.Style(self.top)
            # Inherit metrics, disabled, pressed and keyboard focus from original.
            accent = base == 'Accent.TButton'
            style.configure(name, bordercolor='#829F99', lightcolor='#B8CEC8',
                            darkcolor='#829F99', background='#2877C8' if accent else '#EDF4F2')
            button.configure(style=name)

    def tick(self):
        self.job = None
        if self.closed:
            return
        try:
            blocked = not self.top.winfo_viewable() or self.child_open(self.top)
            self.paint(None if blocked else self.choose())
        except (tk.TclError, AttributeError, IndexError, ValueError, KeyError):
            self.paint(None)
        self.job = self.top.after(150, self.tick)


def main_button(app):
    if app.busy:
        app._guide_run_token = app.review_token()
        return None
    c = app.controls
    if not app.roster:
        return c['load_roster']
    if app.code_dirty:
        return c['save_table']
    if not app.pdf:
        return c['load_pdf']
    ready = bool(app.selected) and len(app.selected) == getattr(app, 'count', 0)
    if not ready:
        return None  # Do not suggest which students should be added or removed.
    sampled = 0 in app.reviewed and (app.count == 1 or any(n > 0 for n in app.reviewed))
    if sampled and getattr(app, '_guide_run_token', None) == app.review_token():
        return None
    return c['run'] if sampled else c['preview']


def review_button(review):
    selected = review.selected()
    context = (review.revision, review.scope.get(), tuple(selected), review.output_folder,
               review.output_mode.get())
    result = review.last_saved.get()
    previous = getattr(review, '_guide_result', '직전 저장 결과: 없음')
    if result != previous:
        review._guide_result = result
        review._guide_completed = context
    if review.busy or not review.rows:
        return None
    if review.raster and review.main_seen != review.main_token():
        return None  # The preserve/replace decision belongs to the user.
    states = review.states
    active = selected if review.scope.get() == '선택 페이지만' else range(len(review.rows))
    good = [i for i in active if states.get(i) == '출력 가능']
    problems = {states.get(i) for i in active if states.get(i) not in ('출력 가능', '제외')}
    if problems:
        # Only a single, unambiguous problem category is a useful navigation hint.
        filters = {'중복' if s and ('중복' in s or '여러 번' in s) else '미정' for s in problems}
        if len(filters) == 1:
            name = next(iter(filters))
            if review.filter != name:
                return review.status_buttons.get(name)
        return None
    if not good or review.edit_open:
        return None
    if not review.output_open:
        return review.output_toggle
    if review.output_folder == '미선택':
        return review.guide_folder
    if not review.agreed.get() or review.saved_revision == review.revision:
        return None
    # Repeated export is valid, but not recommended again after this same output.
    if getattr(review, '_guide_completed', None) == context:
        return None
    return review.export_button


def batch_button(dialog):
    if dialog.review.busy or dialog.rebuilding or dialog.auto_job:
        return None
    step = dialog.book.index(dialog.book.select())
    if step == 0:
        if not dialog.students:
            return dialog.guide_roster
        if not dialog.files:
            return dialog.guide_files
        return dialog.guide_next
    if step == 1:
        if dialog.blocks and dialog.review.rows and all(
                s == '출력 가능' for s in dialog.review.states.values()):
            return dialog.guide_review
        return None
    return review_button(dialog.review)


def merge_button(folder, plan, choose, inspect, execute):
    if not folder:
        return choose
    if plan is None:
        return inspect
    if plan['groups'] and not plan['unmatched'] and all(not g['issues'] for g in plan['groups']):
        return execute
    return None
