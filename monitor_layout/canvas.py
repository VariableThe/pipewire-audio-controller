"""The arrangement canvas: monitors drawn as rounded rectangles.

Design notes
------------
* The canvas is the primary surface - the user manipulates the arrangement
  directly, everything else is a refinement panel.
* Displays are drawn as smooth rounded rectangles with their display number
  in the middle, the display name underneath, and the *main* display marked
  with an accent badge plus a "you" listener glyph.
* Speakers are small dots on the display's speaker edge - the number of dots
  is exactly the confirmed speaker count, so a wrong count is visible.
* Geometry is a front elevation (like the compositor's own display
  settings), so the canvas draws no depth cues; the listening position is
  shown as a glyph under the main display only.
* Rendering is done with Cairo + Pango so the same scene can be painted into
  a window *and* into an offscreen surface for tests and documentation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, Gtk, Pango, PangoCairo  # noqa: E402

from .model import Layout, LayoutError, Monitor, Rect  # noqa: E402

PAD_CM = 14.0  # world margin around the arrangement
MAX_SCALE_PX_PER_CM = 7.0
MIN_SCALE_PX_PER_CM = 0.6
CANVAS_MARGIN_PX = 28.0

ROLE_COLORS = {
    "L": (0.36, 0.62, 0.95),
    "C": (0.55, 0.78, 0.45),
    "R": (0.95, 0.58, 0.36),
}


@dataclass
class Palette:
    """Colours resolved from the theme (or a fixed palette when offscreen)."""

    surface: tuple = (0.16, 0.16, 0.17, 1.0)
    card: tuple = (0.22, 0.22, 0.24, 1.0)
    border: tuple = (0.42, 0.42, 0.45, 1.0)
    fg: tuple = (0.92, 0.92, 0.94, 1.0)
    dim: tuple = (0.62, 0.62, 0.66, 1.0)
    accent: tuple = (0.35, 0.62, 0.95, 1.0)
    accent_fg: tuple = (1.0, 1.0, 1.0, 1.0)
    grid: tuple = (1.0, 1.0, 1.0, 0.05)


def _rgba(ctx: Gtk.StyleContext, name: str, fallback: tuple) -> tuple:
    try:
        result = ctx.lookup_color(name)
    except Exception:
        result = None
    if isinstance(result, tuple):
        ok, color = result
        if not ok:
            return fallback
    else:
        color = result
    if color is None:
        return fallback
    return (color.red, color.green, color.blue, getattr(color, "alpha", 1.0))


def palette_from_style(ctx: Gtk.StyleContext) -> Palette:
    return Palette(
        surface=_rgba(ctx, "window_bg_color", Palette.surface),
        card=_rgba(ctx, "card_bg_color", Palette.card),
        border=_rgba(ctx, "card_border_color", Palette.border),
        fg=_rgba(ctx, "window_fg_color", Palette.fg),
        dim=_rgba(ctx, "dim_label_color", Palette.dim),
        accent=_rgba(ctx, "accent_color", Palette.accent),
        accent_fg=_rgba(ctx, "accent_fg_color", Palette.accent_fg),
        grid=(1.0, 1.0, 1.0, 0.045),
    )


def _set_source(cr: cairo.Context, color: tuple, alpha: Optional[float] = None) -> None:
    r, g, b, a = color
    if alpha is not None:
        a = a * alpha
    cr.set_source_rgba(r, g, b, a)


def _rounded_rect(cr: cairo.Context, x: float, y: float, w: float, h: float, r: float) -> None:
    """Smooth rounded rectangle path.

    The explicit move_to matters: cairo_arc() draws a connecting line from
    the current point, which would otherwise stitch a stray triangle onto
    the shape and ruin the fill.
    """
    r = max(0.0, min(r, min(w, h) / 2.0))
    cr.new_sub_path()
    cr.move_to(x + r, y)
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _dot(cr: cairo.Context, cx: float, cy: float, r: float) -> None:
    """Filled circle without a connecting line from the current point."""
    cr.new_sub_path()
    cr.arc(cx, cy, r, 0, 2 * math.pi)
    cr.close_path()


def _text(
    cr: cairo.Context,
    text: str,
    x: float,
    y: float,
    size: float,
    color: tuple,
    *,
    bold: bool = False,
    align: str = "center",
    alpha: Optional[float] = None,
    max_width: Optional[float] = None,
) -> None:
    """Draw a single line of text centred (or aligned) on (x, y).

    Metrics come from ``get_pixel_extents()`` - Pango's plain extents are in
    Pango units, not pixels, and mixing the two produces huge shapes.
    """
    layout = PangoCairo.create_layout(cr)
    desc = Pango.FontDescription()
    desc.set_family("Sans")
    desc.set_size(int(round(size * 1024)))
    desc.set_weight(Pango.Weight.BOLD if bold else Pango.Weight.NORMAL)
    layout.set_font_description(desc)
    layout.set_text(text, -1)
    if max_width is not None:
        layout.set_width(int(max_width * Pango.SCALE))
        layout.set_ellipsize(Pango.EllipsizeMode.END)
    layout.set_alignment(
        Pango.Alignment.CENTER
        if align == "center"
        else Pango.Alignment.LEFT
        if align == "left"
        else Pango.Alignment.RIGHT
    )
    ink, logical = layout.get_pixel_extents()
    _set_source(cr, color, alpha)
    if align == "center":
        dx = x - logical.width / 2 - logical.x
    elif align == "right":
        dx = x - logical.width - logical.x
    else:
        dx = x - logical.x
    cr.move_to(dx, y - logical.height / 2 - logical.y)
    PangoCairo.show_layout(cr, layout)
    return ink


class Scene:
    """Draws a layout into a Cairo context; no widget state required."""

    def __init__(
        self,
        layout: Layout,
        numbers: Optional[dict[str, int]] = None,
        selected_id: Optional[str] = None,
        palette: Optional[Palette] = None,
        font: Optional[Pango.FontDescription] = None,
    ) -> None:
        self.layout = layout
        self.numbers = numbers or {}
        self.selected_id = selected_id
        self.palette = palette or Palette()
        self.font = font
        self.scale = 1.0
        self.origin = (0.0, 0.0)  # world point at pixel (0, 0)

    # -- coordinate helpers --------------------------------------------------

    def world_bounds(self) -> Rect:
        bounds = self.layout.bounds()
        if bounds is None:
            return Rect(0.0, 0.0, 60.0, 34.0)
        return Rect(
            bounds.x - PAD_CM,
            bounds.y - PAD_CM,
            bounds.width + 2 * PAD_CM,
            bounds.height + 2 * PAD_CM,
        )

    def compute_transform(self, width: float, height: float) -> None:
        world = self.world_bounds()
        avail_w = max(60.0, width - 2 * CANVAS_MARGIN_PX)
        avail_h = max(60.0, height - 2 * CANVAS_MARGIN_PX)
        scale = min(avail_w / world.width, avail_h / world.height)
        scale = max(MIN_SCALE_PX_PER_CM, min(MAX_SCALE_PX_PER_CM, scale))
        self.scale = scale
        self.origin = (
            CANVAS_MARGIN_PX + (avail_w - world.width * scale) / 2 - world.x * scale,
            CANVAS_MARGIN_PX + (avail_h - world.height * scale) / 2 - world.y * scale,
        )

    def to_px(self, x: float, y: float) -> tuple[float, float]:
        ox, oy = self.origin
        return (ox + x * self.scale, oy + y * self.scale)

    def to_world(self, px: float, py: float) -> tuple[float, float]:
        ox, oy = self.origin
        return ((px - ox) / self.scale, (py - oy) / self.scale)

    def hit_test(self, px: float, py: float) -> Optional[str]:
        for mon in reversed(self.layout.monitors):
            x, y = self.to_px(mon.rect().x, mon.rect().y)
            w = mon.rect().width * self.scale
            h = mon.rect().height * self.scale
            if x <= px <= x + w and y <= py <= y + h:
                return mon.id
        return None

    # -- painting ------------------------------------------------------------

    def render(self, cr: cairo.Context, width: float, height: float, transform=None) -> None:
        if transform is None:
            self.compute_transform(width, height)
        else:
            self.scale, self.origin = transform
        pal = self.palette
        cr.save()
        _set_source(cr, pal.surface)
        cr.rectangle(0, 0, width, height)
        cr.fill()
        self._draw_grid(cr, width, height)
        for mon in self.layout.monitors:
            self._draw_monitor(cr, mon)
        self._draw_listener(cr)
        cr.restore()

    def _draw_grid(self, cr: cairo.Context, width: float, height: float) -> None:
        step = 10.0 * self.scale
        if step < 8:
            return
        _set_source(cr, self.palette.grid)
        ox, oy = self.origin
        x = ox % step
        while x < width:
            cr.rectangle(x, 0, 1, height)
            x += step
        y = oy % step
        while y < height:
            cr.rectangle(0, y, width, 1)
            y += step
        cr.fill()

    def _draw_monitor(self, cr: cairo.Context, mon: Monitor) -> None:
        pal = self.palette
        rect = mon.rect()
        x, y = self.to_px(rect.x, rect.y)
        w = rect.width * self.scale
        h = rect.height * self.scale
        radius = min(18.0, min(w, h) * 0.16)
        selected = mon.id == self.selected_id
        is_main = mon.is_main

        # body
        _rounded_rect(cr, x, y, w, h, radius)
        _set_source(cr, pal.card)
        cr.fill()

        if selected:
            _rounded_rect(cr, x, y, w, h, radius)
            _set_source(cr, pal.accent, 0.14)
            cr.fill()

        # border
        _rounded_rect(cr, x + 0.75, y + 0.75, w - 1.5, h - 1.5, radius)
        if selected:
            _set_source(cr, pal.accent)
            cr.set_line_width(2.5)
        elif is_main:
            _set_source(cr, pal.accent, 0.75)
            cr.set_line_width(2.0)
        else:
            _set_source(cr, pal.border)
            cr.set_line_width(1.25)
        cr.stroke()

        # display number in the middle
        number = self.numbers.get(mon.id)
        num_size = max(18.0, min(64.0, h * 0.42))
        if number is not None:
            _text(
                cr,
                str(number),
                x + w / 2,
                y + h / 2 - (8 if h > 70 else 4),
                num_size,
                pal.fg,
                bold=True,
                alpha=0.88 if selected or is_main else 0.58,
            )
        _text(
            cr,
            mon.name,
            x + w / 2,
            y + h / 2 + num_size * 0.55,
            max(10.0, min(14.0, h * 0.12)),
            pal.fg,
            alpha=0.78,
            max_width=w - 16,
        )

        # speakers on the declared edge
        self._draw_speakers(cr, mon, x, y, w, h)

        if is_main:
            self._draw_main_badge(cr, mon, x, y, w)

    def _draw_speakers(
        self, cr: cairo.Context, mon: Monitor, x: float, y: float, w: float, h: float
    ) -> None:
        pal = self.palette
        radius = max(3.0, min(6.0, min(w, h) * 0.05))
        offsets = self._speaker_pixel_offsets(mon, x, y, w, h)
        for (px, py), role in offsets:
            _set_source(cr, pal.accent, 0.9)
            _dot(cr, px, py, radius)
            cr.fill()

    def _speaker_pixel_offsets(
        self, mon: Monitor, x: float, y: float, w: float, h: float
    ) -> list[tuple[tuple[float, float], str]]:
        anchors = mon.speaker_anchors()
        left_to_right = sorted(range(len(anchors)), key=lambda i: anchors[i][0])
        roles_by_index = [""] * len(anchors)
        n = len(anchors)
        for rank, idx in enumerate(left_to_right):
            if n == 1:
                roles_by_index[idx] = "C"
            elif rank == 0:
                roles_by_index[idx] = "L"
            elif rank == n - 1:
                roles_by_index[idx] = "R"
            else:
                roles_by_index[idx] = f"S{rank}"
        out = []
        for i, (ax, ay) in enumerate(anchors):
            px, py = self.to_px(ax, ay)
            out.append(((px, py), roles_by_index[i]))
        return out

    def _draw_main_badge(self, cr: cairo.Context, mon: Monitor, x: float, y: float, w: float) -> None:
        pal = self.palette
        label = "MAIN"
        size = 11.0
        layout = PangoCairo.create_layout(cr)
        desc = Pango.FontDescription()
        desc.set_family("Sans")
        desc.set_size(int(size * 1024))
        desc.set_weight(Pango.Weight.BOLD)
        layout.set_font_description(desc)
        layout.set_text(label, -1)
        _ink, logical = layout.get_pixel_extents()
        pad_x, pad_y = 8.0, 3.0
        bw = logical.width + 2 * pad_x
        bh = logical.height + 2 * pad_y
        bx = x + w / 2 - bw / 2
        by = y - bh - 6
        _rounded_rect(cr, bx, by, bw, bh, bh / 2)
        _set_source(cr, pal.accent)
        cr.fill()
        _text(cr, label, bx + bw / 2, by + bh / 2, size, (0.08, 0.08, 0.12, 1), bold=True)

    def _draw_listener(self, cr: cairo.Context) -> None:
        main = self.layout.main()
        if main is None or getattr(self.layout, "reference_mode", "main") == "listener":
            return
        pal = self.palette
        rect = main.rect()
        cx, cy = self.to_px(rect.center_x, rect.bottom)
        r = 7.0
        # small "listening position" glyph under the main display
        _set_source(cr, pal.accent, 0.75)
        cr.new_sub_path()
        cr.move_to(cx - r, cy + r + 4)
        cr.arc(cx, cy + r + 4, r, math.pi, 2 * math.pi)
        cr.close_path()
        cr.fill()
        _text(cr, "you", cx, cy + r + 20, 10.0, pal.dim)


class RoomScene(Scene):
    """Top-down listening area. +x right, +y down; facing 0 points up.

    Display elevation is deliberately never used as room depth. Positions
    are manual approximations, not acoustic measurements.
    """

    def _dimensions(self, target):
        if isinstance(target, Monitor):
            return target.rect().width, 8.0
        return target.width, 16.0

    def world_bounds(self) -> Rect:
        xs = [self.layout.listener_x - 18, self.layout.listener_x + 18]
        ys = [self.layout.listener_y - 24, self.layout.listener_y + 24]
        for target in self.layout.targets():
            w, h = self._dimensions(target)
            radius = math.hypot(w, h) / 2 + 12
            xs.extend((target.room_x - radius, target.room_x + radius))
            ys.extend((target.room_y - radius, target.room_y + radius))
        return Rect(min(xs)-PAD_CM, min(ys)-PAD_CM,
                    max(xs)-min(xs)+2*PAD_CM, max(ys)-min(ys)+2*PAD_CM)

    def hit_test(self, px, py):
        wx, wy = self.to_world(px, py)
        if math.hypot(wx-self.layout.listener_x, wy-self.layout.listener_y) <= 14:
            return "__listener__"
        for target in reversed(self.layout.targets()):
            angle = math.radians(target.room_yaw)
            dx, dy = wx-target.room_x, wy-target.room_y
            x = dx*math.cos(angle)+dy*math.sin(angle)
            y = -dx*math.sin(angle)+dy*math.cos(angle)
            w, h = self._dimensions(target)
            if abs(x) <= w/2 and abs(y) <= max(h/2, 7):
                return target.id
        return None

    def render(self, cr, width, height, transform=None):
        if transform is None:
            self.compute_transform(width, height)
        else:
            self.scale, self.origin = transform
        pal = self.palette
        cr.save()
        _set_source(cr, pal.surface)
        cr.paint()
        self._draw_grid(cr, width, height)
        for target in self.layout.targets():
            x, y = self.to_px(target.room_x, target.room_y)
            w, h = self._dimensions(target)
            cr.save()
            cr.translate(x, y)
            cr.rotate(math.radians(target.room_yaw))
            _rounded_rect(cr, -w*self.scale/2, -h*self.scale/2,
                          w*self.scale, h*self.scale, 5)
            _set_source(cr, pal.card)
            cr.fill_preserve()
            _set_source(cr, pal.accent if target.id == self.selected_id else pal.border)
            cr.set_line_width(2.5 if target.id == self.selected_id else 1.5)
            cr.stroke()
            cr.restore()
            label = (str(self.numbers.get(target.id, target.id)) if isinstance(target, Monitor)
                     else "S" + str(self.layout.peripherals.index(target) + 1))
            # A mono peripheral has a physical slot at its center. Keep its
            # identifier above the card so the slot marker never covers it.
            label_y = y if isinstance(target, Monitor) else y-h*self.scale/2-12
            _text(cr, label, x, label_y, 14, pal.fg, bold=True)
            _text(cr, target.name, x, y+max(h*self.scale/2+14, 23), 11, pal.fg, alpha=0.78, max_width=180)
            if not target.room_confirmed:
                _text(cr, "Approximate", x, y+max(h*self.scale/2+28, 37), 9, pal.dim)
        # Physical slot positions come from the same model as gain preview.
        for target in self.layout.targets():
            for sx, sy in target.room_speaker_points():
                x, y = self.to_px(sx, sy)
                _set_source(cr, pal.accent, .9)
                _dot(cr, x, y, 3.5)
                cr.fill()
        self._draw_room_listener(cr)
        cr.restore()

    def _draw_room_listener(self, cr):
        pal = self.palette
        x, y = self.to_px(self.layout.listener_x, self.layout.listener_y)
        r = 10*self.scale
        _set_source(cr, pal.accent, .2)
        _dot(cr, x, y, r)
        cr.fill_preserve()
        _set_source(cr, pal.accent)
        cr.set_line_width(3 if self.selected_id == "__listener__" else 1.5)
        cr.stroke()
        a = math.radians(self.layout.listener_facing)
        dx, dy = math.sin(a), -math.cos(a)
        cr.move_to(x+dx*r*.4, y+dy*r*.4)
        cr.line_to(x+dx*r*1.8, y+dy*r*1.8)
        cr.stroke()
        cr.move_to(x+dx*r*1.8, y+dy*r*1.8)
        cr.line_to(x+dx*r*1.15+dy*r*.35, y+dy*r*1.15-dx*r*.35)
        cr.line_to(x+dx*r*1.15-dy*r*.35, y+dy*r*1.15+dx*r*.35)
        cr.close_path()
        cr.fill()
        _text(cr, "You", x, y, 12, pal.fg, bold=True)
        _text(cr, "Seat / sound reference", x, y+r+16, 11, pal.fg, alpha=0.78)


class LayoutCanvas(Gtk.DrawingArea):
    """Interactive canvas: select, drag, rotate, nudge."""

    def __init__(self, controller) -> None:
        super().__init__()
        self.controller = controller
        self.view_mode = "front"
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_focusable(True)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.add_css_class("layout-canvas")
        self._selected_id: Optional[str] = None
        self._drag_id: Optional[str] = None
        self._drag_origin = (0.0, 0.0)
        self._drag_start_px = (0.0, 0.0)
        self._drag_scale = 1.0
        self._drag_origin_px = (0.0, 0.0)
        self._grab_offset = (0.0, 0.0)
        self._focus = False

        click = Gtk.GestureClick()
        click.set_button(1)
        click.connect("pressed", self._on_click)
        self.add_controller(click)

        drag = Gtk.GestureDrag()
        drag.set_button(1)
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)

        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

        self.set_draw_func(self._on_draw)
        self.controller.connect(self.refresh)
        self.connect("notify::has-focus", self._on_focus_changed)

    # -- selection -----------------------------------------------------------

    @property
    def selected_id(self) -> Optional[str]:
        return self._selected_id

    def select(self, monitor_id: Optional[str]) -> None:
        if monitor_id != self._selected_id:
            self._selected_id = monitor_id
            self.queue_draw()
            self.controller.notify(False)

    def refresh(self, layout_changed: bool = None) -> None:
        self.queue_draw()

    # -- scene ---------------------------------------------------------------

    def set_view_mode(self, mode: str) -> None:
        if mode not in ("front", "room"):
            raise LayoutError("Unknown view mode")
        self._on_drag_end(None, 0, 0)
        self.view_mode = mode
        if mode == "front" and self.selected_id not in {m.id for m in self.controller.layout.monitors}:
            self.select(None)
        self.queue_draw()

    def scene(self) -> Scene:
        return (RoomScene if self.view_mode == "room" else Scene)(
            self.controller.layout,
            numbers=self.controller.monitor_numbers(),
            selected_id=self._selected_id,
            palette=palette_from_style(self.get_style_context()),
        )

    def _on_draw(self, area: Gtk.DrawingArea, cr: cairo.Context, w: float, h: float) -> None:
        scene = self.scene()
        scene.render(cr, w, h, (self._drag_scale, self._drag_origin_px) if self._drag_id else None)
        if self.has_focus():
            _set_source(cr, scene.palette.accent, 0.9)
            cr.set_line_width(2.0)
            cr.rectangle(1, 1, w - 2, h - 2)
            cr.stroke()

    def _on_focus_changed(self, *args) -> None:
        self.queue_draw()

    def _on_key_pressed(self, controller, keyval, keycode, state) -> bool:
        return self.key_pressed(keyval, state)

    # -- pointer -------------------------------------------------------------

    def _on_click(self, gesture: Gtk.GestureClick, n_press: int, x: float, y: float) -> None:
        scene = self.scene()
        scene.compute_transform(self.get_width(), self.get_height())
        hit = scene.hit_test(x, y)
        self.select(hit)
        self.grab_focus()

    def _on_drag_begin(self, gesture: Gtk.GestureDrag, sx: float, sy: float) -> None:
        scene = self.scene()
        scene.compute_transform(self.get_width(), self.get_height())
        hit = scene.hit_test(sx, sy)
        if hit is None:
            return
        self.select(hit)
        self._drag_id = hit
        wx, wy = scene.to_world(sx, sy)
        mon = self.controller.layout.get(hit)
        if self.view_mode == "room":
            if hit == "__listener__":
                x, y = self.controller.layout.listener_x, self.controller.layout.listener_y
            else:
                x, y = mon.room_x, mon.room_y
        else:
            x, y = mon.x, mon.y
        self._grab_offset = (wx - x, wy - y)
        self._drag_origin = (x, y)
        self._drag_start_px = (sx, sy)
        self._drag_scale = scene.scale
        self._drag_origin_px = scene.origin

    def _on_drag_update(self, gesture: Gtk.GestureDrag, dx: float, dy: float) -> None:
        if self._drag_id is None:
            return
        wx = (self._drag_start_px[0] + dx - self._drag_origin_px[0]) / self._drag_scale
        wy = (self._drag_start_px[1] + dy - self._drag_origin_px[1]) / self._drag_scale
        x = wx - self._grab_offset[0]
        y = wy - self._grab_offset[1]
        # 0.5 cm grid snap, keeps dragging tidy without fighting the user
        x = round(x * 2) / 2
        y = round(y * 2) / 2
        try:
            if self.view_mode == "room":
                if self._drag_id == "__listener__":
                    self.controller.set_listener(x=x, y=y)
                else:
                    self.controller.set_room_geometry(self._drag_id, x=x, y=y)
            else:
                self.controller.move_monitor(self._drag_id, x, y)
        except LayoutError:
            return
        self.queue_draw()

    def _on_drag_end(self, gesture: Gtk.GestureDrag, dx: float, dy: float) -> None:
        self._drag_id = None
        self._drag_start_px = (0.0, 0.0)
        self._drag_scale = 1.0
        self._drag_origin_px = (0.0, 0.0)
        self.queue_draw()

    # -- keyboard ------------------------------------------------------------

    def key_pressed(self, keyval: int, state: int) -> bool:
        from gi.repository import Gdk

        step = 5.0 if state & Gdk.ModifierType.SHIFT_MASK else 1.0
        monitor_id = self._selected_id
        if monitor_id is None:
            return False
        if self.view_mode == "room":
            return self._room_key_pressed(monitor_id, keyval, step)
        try:
            if keyval == Gdk.KEY_Left:
                self.controller.move_monitor_by(monitor_id, -step, 0)
            elif keyval == Gdk.KEY_Right:
                self.controller.move_monitor_by(monitor_id, step, 0)
            elif keyval == Gdk.KEY_Up:
                self.controller.move_monitor_by(monitor_id, 0, -step)
            elif keyval == Gdk.KEY_Down:
                self.controller.move_monitor_by(monitor_id, 0, step)
            elif keyval in (Gdk.KEY_r, Gdk.KEY_R):
                self.controller.rotate_monitor(monitor_id)
            elif keyval in (Gdk.KEY_m, Gdk.KEY_M):
                self.controller.set_main(monitor_id)
            elif keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace):
                self.controller.remove_monitor(monitor_id)
                self.select(None)
            else:
                return False
        except LayoutError:
            return False
        self.queue_draw()
        return True


    def _room_key_pressed(self, target_id, keyval, step):
        layout = self.controller.layout
        is_seat = target_id == "__listener__"
        target = layout.get(target_id)
        if not is_seat and target is None:
            return False
        x, y = (layout.listener_x, layout.listener_y) if is_seat else (target.room_x, target.room_y)
        deltas = {Gdk.KEY_Left: (-step, 0), Gdk.KEY_Right: (step, 0),
                  Gdk.KEY_Up: (0, -step), Gdk.KEY_Down: (0, step)}
        try:
            if keyval in deltas:
                dx, dy = deltas[keyval]
                if is_seat:
                    self.controller.set_listener(x=x+dx, y=y+dy)
                else:
                    self.controller.set_room_geometry(target_id, x=x+dx, y=y+dy)
            elif keyval in (Gdk.KEY_r, Gdk.KEY_R):
                if is_seat:
                    self.controller.set_listener(facing=(layout.listener_facing+15) % 360)
                else:
                    self.controller.set_room_geometry(target_id, yaw=(target.room_yaw+15) % 360)
            elif keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace) and not is_seat:
                self.controller.remove_target(target_id)
                self.select(None)
            elif keyval in (Gdk.KEY_m, Gdk.KEY_M) and isinstance(target, Monitor):
                self.controller.set_main(target_id)
            else:
                return False
        except LayoutError:
            return False
        self.queue_draw()
        return True
