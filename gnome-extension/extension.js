// SPDX-License-Identifier: GPL-3.0-or-later
// Screend helper: runs inside GNOME Shell and offers the Screend app what a
// regular Wayland client can't do itself:
//   * instant capture (the stage is frozen in-process; only the chosen area is
//     encoded) with a selector that snaps to windows
//   * placing Screend's own windows (quick access card, pins) above others
//     without stealing focus
//   * writing images to the clipboard without having keyboard focus

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

Gio._promisify(Shell.Screenshot.prototype, 'screenshot_stage_to_content');
Gio._promisify(Shell.Screenshot, 'composite_to_stream');

const BUS_NAME = 'io.github.jksnetwork.Screend.Shell';
const OBJECT_PATH = '/io/github/jksnetwork/Screend/Shell';
const API_VERSION = 4;

const IFACE = `
<node>
  <interface name="io.github.jksnetwork.Screend.Shell">
    <method name="SelectArea">
      <arg type="b" name="adjust" direction="in"/>
      <arg type="s" name="path" direction="out"/>
      <arg type="s" name="kind" direction="out"/>
      <arg type="(iiii)" name="rect" direction="out"/>
    </method>
    <method name="CaptureRect">
      <arg type="(iiii)" name="rect" direction="in"/>
      <arg type="s" name="path" direction="out"/>
    </method>
    <method name="CaptureMonitor">
      <arg type="s" name="path" direction="out"/>
      <arg type="(iiii)" name="rect" direction="out"/>
    </method>
    <method name="CaptureAll">
      <arg type="s" name="path" direction="out"/>
    </method>
    <method name="CaptureFocusedWindow">
      <arg type="s" name="path" direction="out"/>
    </method>
    <method name="StartRecording">
      <arg type="s" name="mode" direction="in"/>
      <arg type="b" name="adjust" direction="in"/>
      <arg type="b" name="cursor" direction="in"/>
      <arg type="b" name="started" direction="out"/>
    </method>
    <method name="StopRecording"/>
    <signal name="RecordingFinished">
      <arg type="s" name="path"/>
    </signal>
    <property name="Recording" type="b" access="read"/>
    <method name="SetClipboardText">
      <arg type="s" name="text" direction="in"/>
    </method>
    <method name="ShowFrame">
      <arg type="(iiii)" name="rect" direction="in"/>
    </method>
    <method name="HideFrame"/>
    <method name="SetClipboardImage">
      <arg type="s" name="path" direction="in"/>
    </method>
    <method name="ManageWindow">
      <arg type="s" name="title" direction="in"/>
      <arg type="s" name="mode" direction="in"/>
      <arg type="i" name="x" direction="in"/>
      <arg type="i" name="y" direction="in"/>
    </method>
    <property name="Version" type="u" access="read"/>
  </interface>
</node>`;

const DIM = [0, 0, 0, 0.45];
const ACCENT = [0.25, 0.6, 1.0];
const LOUPE_SRC = 15;
const LOUPE_ZOOM = 8;
const CARD_MARGIN = 20;
const HANDLE = 10;
const MIN_SIZE = 4;

function outputDir() {
    const dir = GLib.build_filenamev([GLib.get_user_runtime_dir(), 'screend']);
    GLib.mkdir_with_parents(dir, 0o700);
    return dir;
}

async function encodeToFile(texture, rect, scale) {
    const stream = Gio.MemoryOutputStream.new_resizable();
    const [x, y, w, h] = rect ?? [0, 0, -1, -1];
    await Shell.Screenshot.composite_to_stream(
        texture, x, y, w, h, scale, null, 0, 0, 1, stream);
    stream.close(null);
    const bytes = stream.steal_as_bytes();
    const stamp = GLib.get_real_time();
    const path = GLib.build_filenamev([outputDir(), `shot-${stamp}.png`]);
    GLib.file_set_contents(path, bytes.get_data());
    return path;
}

function scaledRect([x, y, w, h], scale) {
    return [x * scale, y * scale, w * scale, h * scale].map(Math.round);
}

function pointerMonitor() {
    const [px, py] = global.get_pointer();
    return Main.layoutManager.monitors.find(m =>
        px >= m.x && px < m.x + m.width && py >= m.y && py < m.y + m.height) ??
        Main.layoutManager.primaryMonitor;
}

/** Normal application windows on the current workspace, topmost first. */
function visibleWindows() {
    const workspace = global.workspace_manager.get_active_workspace();
    const windows = global.get_window_actors()
        .map(actor => actor.meta_window)
        .filter(w => w && !w.minimized && w.showing_on_its_workspace() &&
            (w.is_on_all_workspaces() || w.get_workspace() === workspace) &&
            w.get_window_type() !== Meta.WindowType.DESKTOP &&
            !(w.get_title() ?? '').startsWith('Screend Quick Access'));
    return global.display.sort_windows_by_stacking(windows).reverse();
}

/** Encode a window's own pixels (unobscured), cropped to its frame. */
async function captureWindow(window) {
    const actor = window.get_compositor_private();
    const content = actor.paint_to_content(null);
    const texture = content.get_texture();
    const scale = actor.get_resource_scale();
    const frame = window.get_frame_rect();
    const buffer = window.get_buffer_rect();
    const rect = [frame.x - buffer.x, frame.y - buffer.y, frame.width, frame.height];
    return encodeToFile(texture, scaledRect(rect, scale), scale);
}

const Selector = GObject.registerClass(
class Selector extends St.Widget {
    _init(content, scale, adjust, onDone) {
        super._init({reactive: true, can_focus: true, x: 0, y: 0,
            width: global.stage.width, height: global.stage.height});
        this._content = content;
        this._scale = scale;
        this._onDone = onDone;
        this._adjust = adjust;
        this._fixed = null; // [x, y, w, h] once a drag is released in adjust mode
        this._edit = null;  // {mode: 'move'|handle id, start: [x, y], rect}
        this._windows = visibleWindows().map(w => ({window: w, rect: w.get_frame_rect()}));
        this._start = null;
        this._end = null;
        this._spaceAnchor = null;
        [this._px, this._py] = global.get_pointer();

        const frozen = new St.Widget({width: this.width, height: this.height});
        frozen.set_content(content);
        this.add_child(frozen);

        this._area = new St.DrawingArea({width: this.width, height: this.height});
        this._area.connect('repaint', () => this._repaint());
        this.add_child(this._area);

        this._label = new St.Label({style_class: 'screend-pill', visible: false});
        this.add_child(this._label);
        this._hint = new St.Label({
            style_class: 'screend-pill',
            text: 'Drag to capture an area  ·  Click a window or screen  ·  Esc to cancel',
        });
        this.add_child(this._hint);

        // magnifier: a zoomed copy of the frozen image inside a clipped box
        const size = LOUPE_SRC * LOUPE_ZOOM;
        this._loupe = new St.Widget({width: size, height: size,
            clip_to_allocation: true});
        this._loupeImage = new Clutter.Actor({width: this.width, height: this.height});
        this._loupeImage.set_content(content);
        this._loupeImage.set_content_scaling_filters(
            Clutter.ScalingFilter.NEAREST, Clutter.ScalingFilter.NEAREST);
        this._loupeImage.set_scale(LOUPE_ZOOM, LOUPE_ZOOM);
        this._loupe.add_child(this._loupeImage);
        this._loupe.add_child(new St.Widget({style_class: 'screend-loupe-frame', width: size, height: size}));
        this._loupe.add_child(new St.Widget({style_class: 'screend-loupe-center',
            x: size / 2 - LOUPE_ZOOM / 2, y: size / 2 - LOUPE_ZOOM / 2,
            width: LOUPE_ZOOM, height: LOUPE_ZOOM}));
        this.add_child(this._loupe);
        this._coords = new St.Label({style_class: 'screend-pill screend-small'});
        this.add_child(this._coords);
    }

    open() {
        Main.layoutManager.screenshotUIGroup.add_child(this);
        this._update();
        const group = Main.layoutManager.screenshotUIGroup;
        group.get_parent().set_child_above_sibling(group, null);
        this._grab = Main.pushModal(this, {actionMode: Shell.ActionMode.POPUP});
        if ((this._grab.get_seat_state() & Clutter.GrabState.KEYBOARD) === 0) {
            this._finish(null);
            return;
        }
        global.display.set_cursor(Meta.Cursor.CROSSHAIR);
        this.grab_key_focus();
    }

    _finish(result) {
        if (this._done)
            return;
        this._done = true;
        if (this._grab) {
            Main.popModal(this._grab);
            this._grab = null;
        }
        global.display.set_cursor(Meta.Cursor.DEFAULT);
        this.destroy();
        this._onDone(result);
    }

    // -- geometry --------------------------------------------------------------

    _selection() {
        if (this._fixed)
            return this._fixed;
        if (!this._start || !this._end)
            return null;
        const [x0, y0] = this._start, [x1, y1] = this._end;
        return [Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0)];
    }

    _hovered() {
        const {_px: px, _py: py} = this;
        const hit = this._windows.find(({rect: r}) =>
            px >= r.x && px < r.x + r.width && py >= r.y && py < r.y + r.height);
        if (hit)
            return {kind: 'window', window: hit.window, rect: [hit.rect.x, hit.rect.y, hit.rect.width, hit.rect.height]};
        const m = pointerMonitor();
        return {kind: 'screen', rect: [m.x, m.y, m.width, m.height]};
    }

    // -- events -----------------------------------------------------------------

    vfunc_motion_event(event) {
        [this._px, this._py] = event.get_coords();
        if (this._edit) {
            this._applyEdit(this._px, this._py);
        } else if (this._fixed) {
            const handle = this._handleAt(this._px, this._py);
            global.display.set_cursor(handle ? Meta.Cursor.CROSSHAIR
                : this._inside(this._px, this._py) ? Meta.Cursor.MOVE_OR_RESIZE_WINDOW : Meta.Cursor.CROSSHAIR);
        } else if (this._start) {
            if (this._spaceAnchor) {
                // Space held: move the whole selection
                const dx = this._px - this._spaceAnchor[0], dy = this._py - this._spaceAnchor[1];
                this._start = [this._start[0] + dx, this._start[1] + dy];
                this._spaceAnchor = [this._px, this._py];
            }
            this._end = [this._px, this._py];
        }
        this._update();
        return Clutter.EVENT_STOP;
    }

    vfunc_button_press_event(event) {
        if (event.get_button() === Clutter.BUTTON_SECONDARY) {
            this._finish(null);
            return Clutter.EVENT_STOP;
        }
        if (event.get_button() !== Clutter.BUTTON_PRIMARY)
            return Clutter.EVENT_PROPAGATE;
        [this._px, this._py] = event.get_coords();
        if (this._fixed) {
            if (event.get_click_count() === 2 && this._inside(this._px, this._py)) {
                this._confirm();
                return Clutter.EVENT_STOP;
            }
            const handle = this._handleAt(this._px, this._py);
            if (handle !== null || this._inside(this._px, this._py)) {
                this._edit = {mode: handle ?? 'move', start: [this._px, this._py], rect: [...this._fixed]};
                return Clutter.EVENT_STOP;
            }
            this._fixed = null; // start over with a new selection
        }
        this._start = [this._px, this._py];
        this._end = this._start;
        this._update();
        return Clutter.EVENT_STOP;
    }

    _inside(x, y) {
        const [rx, ry, rw, rh] = this._fixed;
        return x >= rx && x <= rx + rw && y >= ry && y <= ry + rh;
    }

    _handles() {
        const [x, y, w, h] = this._fixed;
        // id: which edges the handle moves (l, r, t, b)
        return [
            ['lt', x, y], ['t', x + w / 2, y], ['rt', x + w, y], ['r', x + w, y + h / 2],
            ['rb', x + w, y + h], ['b', x + w / 2, y + h], ['lb', x, y + h], ['l', x, y + h / 2],
        ];
    }

    _handleAt(px, py) {
        const hit = this._handles().find(([, hx, hy]) =>
            Math.abs(px - hx) <= HANDLE && Math.abs(py - hy) <= HANDLE);
        return hit ? hit[0] : null;
    }

    _applyEdit(px, py) {
        const {mode, start, rect} = this._edit;
        const dx = px - start[0], dy = py - start[1];
        let [x0, y0, x1, y1] = [rect[0], rect[1], rect[0] + rect[2], rect[1] + rect[3]];
        if (mode === 'move') {
            x0 += dx; x1 += dx; y0 += dy; y1 += dy;
        } else {
            if (mode.includes('l'))
                x0 += dx;
            if (mode.includes('r'))
                x1 += dx;
            if (mode.includes('t'))
                y0 += dy;
            if (mode.includes('b'))
                y1 += dy;
        }
        this._fixed = [Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0)];
    }

    _confirm() {
        const sel = this._selection();
        this._finish(sel && sel[2] >= MIN_SIZE && sel[3] >= MIN_SIZE
            ? {kind: 'area', rect: sel.map(Math.round)} : this._hovered());
    }

    vfunc_button_release_event(event) {
        if (event.get_button() !== Clutter.BUTTON_PRIMARY)
            return Clutter.EVENT_PROPAGATE;
        if (this._edit) {
            this._edit = null;
            this._update();
            return Clutter.EVENT_STOP;
        }
        if (!this._start)
            return Clutter.EVENT_PROPAGATE;
        const sel = this._selection();
        if (sel[2] < MIN_SIZE && sel[3] < MIN_SIZE) {
            this._finish(this._hovered());
        } else if (this._adjust) {
            this._fixed = sel;
            this._start = this._end = null;
            this._update();
        } else {
            this._finish({kind: 'area', rect: sel.map(Math.round)});
        }
        return Clutter.EVENT_STOP;
    }

    vfunc_key_press_event(event) {
        const sym = event.get_key_symbol();
        if (sym === Clutter.KEY_Escape) {
            this._finish(null);
        } else if (sym === Clutter.KEY_space && this._start) {
            this._spaceAnchor = [this._px, this._py];
        } else if (sym === Clutter.KEY_Return || sym === Clutter.KEY_KP_Enter || sym === Clutter.KEY_space) {
            this._confirm();
        } else if (this._fixed && [Clutter.KEY_Left, Clutter.KEY_Right, Clutter.KEY_Up, Clutter.KEY_Down].includes(sym)) {
            // arrows move the selection; with Ctrl they resize it; Shift = 10px steps
            const state = event.get_state();
            const step = state & Clutter.ModifierType.SHIFT_MASK ? 10 : 1;
            const dx = sym === Clutter.KEY_Left ? -step : sym === Clutter.KEY_Right ? step : 0;
            const dy = sym === Clutter.KEY_Up ? -step : sym === Clutter.KEY_Down ? step : 0;
            let [x, y, w, h] = this._fixed;
            if (state & Clutter.ModifierType.CONTROL_MASK) {
                w = Math.max(MIN_SIZE, w + dx);
                h = Math.max(MIN_SIZE, h + dy);
            } else {
                x += dx;
                y += dy;
            }
            this._fixed = [x, y, w, h];
            this._update();
        }
        return Clutter.EVENT_STOP;
    }

    vfunc_key_release_event(event) {
        if (event.get_key_symbol() === Clutter.KEY_space)
            this._spaceAnchor = null;
        return Clutter.EVENT_STOP;
    }

    // -- drawing ----------------------------------------------------------------

    _update() {
        const sel = this._selection();
        const hover = sel ? null : this._hovered();
        this._highlight = sel ?? hover.rect;
        this._highlightKind = sel ? 'area' : hover.kind;

        this._hint.visible = !sel || (this._fixed && !this._edit);
        this._hint.text = this._fixed
            ? 'Enter or double-click to capture  ·  Drag handles or use arrows to adjust  ·  Esc to cancel'
            : 'Drag to capture an area  ·  Click a window or screen  ·  Esc to cancel';
        this._loupe.visible = this._coords.visible = !this._fixed || Boolean(this._edit);
        if (this._hint.visible) {
            const m = pointerMonitor();
            this._hint.set_position(Math.round(m.x + (m.width - this._hint.width) / 2), m.y + 40);
        }

        const [, , w, h] = this._highlight;
        this._label.text = `${Math.round(w * this._scale)} × ${Math.round(h * this._scale)}`;
        this._label.visible = true;
        const [lx, ly, , lh] = this._highlight;
        this._label.set_position(Math.round(lx), Math.round(ly + lh + 8));

        // magnifier near the pointer, flipped away from monitor edges
        const size = LOUPE_SRC * LOUPE_ZOOM;
        const m = pointerMonitor();
        let x = this._px + 24, y = this._py + 24;
        if (x + size > m.x + m.width)
            x = this._px - 24 - size;
        if (y + size + 30 > m.y + m.height)
            y = this._py - 24 - size - 30;
        this._loupe.set_position(Math.round(x), Math.round(y));
        this._loupeImage.set_position(
            Math.round(-(this._px - LOUPE_SRC / 2) * LOUPE_ZOOM),
            Math.round(-(this._py - LOUPE_SRC / 2) * LOUPE_ZOOM));
        this._coords.text = `${Math.round(this._px * this._scale)}, ${Math.round(this._py * this._scale)}`;
        this._coords.set_position(Math.round(x + (size - this._coords.width) / 2), Math.round(y + size + 6));

        this._area.queue_repaint();
    }

    _repaint() {
        const cr = this._area.get_context();
        const [x, y, w, h] = this._highlight;

        cr.setFillRule(1); // EVEN_ODD
        cr.rectangle(0, 0, this.width, this.height);
        cr.rectangle(x, y, w, h);
        cr.setSourceRGBA(...DIM);
        cr.fill();

        cr.setLineWidth(this._highlightKind === 'area' ? 1 : 3);
        if (this._highlightKind === 'area')
            cr.setSourceRGBA(1, 1, 1, 1);
        else
            cr.setSourceRGBA(...ACCENT, 0.9);
        cr.rectangle(Math.floor(x) + 0.5, Math.floor(y) + 0.5, Math.round(w), Math.round(h));
        cr.stroke();

        if (this._fixed) {
            for (const [, hx, hy] of this._handles()) {
                cr.rectangle(hx - 4, hy - 4, 8, 8);
                cr.setSourceRGBA(1, 1, 1, 1);
                cr.fillPreserve();
                cr.setSourceRGBA(...ACCENT, 1);
                cr.setLineWidth(1.5);
                cr.stroke();
            }
        } else if (!this._start) {
            // crosshair through the pointer
            cr.setLineWidth(1);
            cr.setSourceRGBA(1, 1, 1, 0.5);
            cr.setDash([4, 4], 0);
            const m = pointerMonitor();
            cr.moveTo(m.x, Math.floor(this._py) + 0.5);
            cr.lineTo(m.x + m.width, Math.floor(this._py) + 0.5);
            cr.moveTo(Math.floor(this._px) + 0.5, m.y);
            cr.lineTo(Math.floor(this._px) + 0.5, m.y + m.height);
            cr.stroke();
        }
        cr.$dispose();
    }
});

class WindowRules {
    constructor() {
        this._rules = new Map(); // title -> {mode, x, y}
        this._managed = new Map(); // MetaWindow -> [signal ids]
        this._createdId = global.display.connect('window-created',
            (_d, win) => this._onWindowCreated(win));
    }

    destroy() {
        global.display.disconnect(this._createdId);
        for (const [win, ids] of this._managed)
            ids.forEach(id => win.disconnect(id));
        this._managed.clear();
    }

    add(title, mode, x, y) {
        this._rules.set(title, {mode, x, y});
        for (const actor of global.get_window_actors()) {
            if (actor.meta_window.get_title() === title)
                this._apply(actor.meta_window, false);
        }
    }

    _onWindowCreated(win) {
        const previous = global.display.focus_window;
        const check = () => {
            if (this._rules.has(win.get_title()) && !this._managed.has(win))
                this._apply(win, true, previous);
        };
        check();
        const id = win.connect('notify::title', check);
        win.connect('unmanaged', () => win.disconnect(id));
    }

    _apply(win, isNew, previous = null) {
        const rule = this._rules.get(win.get_title());
        if (!rule)
            return;
        if (rule.mode === 'focus') {
            // windows opened from a hotkey carry no activation token, so mutter
            // would only show "… is ready"; raise them ourselves
            if (isNew) {
                const id = win.connect('shown', () => {
                    win.disconnect(id);
                    Main.activateWindow(win);
                });
            } else {
                Main.activateWindow(win);
            }
            return;
        }
        win.make_above();
        win.stick();
        if (rule.mode === 'card') {
            const place = () => this._placeCard(win);
            // 'shown' fires after mutter's own placement, which would otherwise win
            const ids = [
                win.connect('shown', place),
                win.connect('size-changed', place),
                win.connect('unmanaged', () => this._managed.delete(win)),
            ];
            this._managed.set(win, ids);
            place();
            // hand focus straight back to whatever the user was working in
            if (isNew && previous && previous !== win) {
                const restore = () => {
                    if (global.display.focus_window === win)
                        previous.activate(global.get_current_time());
                };
                restore();
                const id = win.connect('focus', () => {
                    restore();
                    win.disconnect(id);
                });
                GLib.timeout_add(GLib.PRIORITY_DEFAULT, 300, () => {
                    try {
                        win.disconnect(id);
                    } catch {}
                    return GLib.SOURCE_REMOVE;
                });
            }
        } else if (rule.mode === 'pin' && rule.x >= 0 && rule.y >= 0) {
            win.move_frame(true, rule.x, rule.y);
        }
    }

    _placeCard(win) {
        const monitor = pointerMonitor();
        const area = Main.layoutManager.getWorkAreaForMonitor(monitor.index);
        const frame = win.get_frame_rect();
        win.move_frame(true, area.x + CARD_MARGIN, area.y + area.height - frame.height - CARD_MARGIN);
    }
}

export default class ScreendExtension extends Extension {
    enable() {
        this._rules = new WindowRules();
        this._recordingPath = null;
        this._recordingWatch = Main.screenshotUI.connect('notify::screencast-in-progress',
            () => this._onScreencastChanged());
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
        this._nameId = Gio.bus_own_name_on_connection(Gio.DBus.session, BUS_NAME,
            Gio.BusNameOwnerFlags.NONE, null, null);
    }

    disable() {
        if (this._recordingWatch) {
            Main.screenshotUI.disconnect(this._recordingWatch);
            this._recordingWatch = 0;
        }
        this._selector?._finish(null);
        this._selector = null;
        this.HideFrame();
        if (this._nameId) {
            Gio.bus_unown_name(this._nameId);
            this._nameId = 0;
        }
        this._dbus?.unexport();
        this._dbus = null;
        this._rules?.destroy();
        this._rules = null;
    }

    get Version() {
        return API_VERSION;
    }

    async _stage() {
        const shooter = new Shell.Screenshot();
        const [content, scale] = await shooter.screenshot_stage_to_content();
        return {content, scale};
    }

    _reply(invocation, promise, toVariant) {
        promise.then(value => invocation.return_value(toVariant(value)))
            .catch(e => {
                logError(e, 'screend');
                invocation.return_error_literal(Gio.DBusError, Gio.DBusError.FAILED, String(e?.message ?? e));
            });
    }

    SelectAreaAsync([adjust], invocation) {
        this._reply(invocation, this._selectArea(adjust), ([path, kind, rect]) =>
            new GLib.Variant('(ss(iiii))', [path, kind, rect]));
    }

    async _pick(adjust) {
        const {content, scale} = await this._stage();
        const result = await new Promise(resolve => {
            this._selector = new Selector(content, scale, adjust, resolve);
            this._selector.open();
        });
        this._selector = null;
        return {content, scale, result};
    }

    async _selectArea(adjust) {
        if (this._selector)
            return ['', 'busy', [0, 0, 0, 0]];
        const {content, scale, result} = await this._pick(adjust);
        if (!result)
            return ['', 'cancelled', [0, 0, 0, 0]];
        let path;
        if (result.kind === 'window')
            path = await captureWindow(result.window);
        else
            path = await encodeToFile(content.get_texture(), scaledRect(result.rect, scale), scale);
        return [path, result.kind, result.rect.map(Math.round)];
    }

    CaptureRectAsync([rect], invocation) {
        this._reply(invocation, (async () => {
            const {content, scale} = await this._stage();
            return encodeToFile(content.get_texture(), scaledRect(rect, scale), scale);
        })(), path => new GLib.Variant('(s)', [path]));
    }

    CaptureMonitorAsync(_params, invocation) {
        this._reply(invocation, (async () => {
            const m = pointerMonitor();
            const rect = [m.x, m.y, m.width, m.height];
            const {content, scale} = await this._stage();
            return [await encodeToFile(content.get_texture(), scaledRect(rect, scale), scale), rect];
        })(), ([path, rect]) => new GLib.Variant('(s(iiii))', [path, rect]));
    }

    CaptureAllAsync(_params, invocation) {
        this._reply(invocation, (async () => {
            const {content, scale} = await this._stage();
            return encodeToFile(content.get_texture(), null, scale);
        })(), path => new GLib.Variant('(s)', [path]));
    }

    CaptureFocusedWindowAsync(_params, invocation) {
        this._reply(invocation, (async () => {
            // the focus can sit on a window that was just hidden (e.g. Screend's card)
            let win = global.display.focus_window;
            if (!win || win.minimized || !win.showing_on_its_workspace())
                win = visibleWindows()[0];
            if (!win)
                throw new Error('no window to capture');
            return captureWindow(win);
        })(), path => new GLib.Variant('(s)', [path]));
    }

    // -- screen recording, through GNOME's own recorder service -----------------
    //
    // We drive Main.screenshotUI's recorder so GNOME shows its usual red
    // indicator and stop button in the panel. These are private shell APIs, so
    // every use is guarded.

    get Recording() {
        return Boolean(Main.screenshotUI.screencast_in_progress);
    }

    StartRecordingAsync([mode, adjust, cursor], invocation) {
        this._reply(invocation, this._startRecording(mode, adjust, cursor),
            started => new GLib.Variant('(b)', [started]));
    }

    async _startRecording(mode, adjust, cursor) {
        const ui = Main.screenshotUI;
        if (this._selector || ui.screencast_in_progress || !ui._screencastProxy)
            return false;
        let result;
        if (mode === 'screen') {
            const m = pointerMonitor();
            result = {kind: 'screen', rect: [m.x, m.y, m.width, m.height]};
        } else {
            result = (await this._pick(adjust)).result;
        }
        if (!result)
            return false;
        const [x, y, w, h] = result.kind === 'window'
            ? (r => [r.x, r.y, r.width, r.height])(result.window.get_frame_rect())
            : result.rect;
        // let the selector's fade-out leave the frame before recording starts
        await new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, 150, () => r() ?? GLib.SOURCE_REMOVE));
        ui._screencastAreaIndicator?.setSelectionRect(x, y, w, h);
        ui._setScreencastInProgress(true);
        try {
            const template = GLib.build_filenamev([outputDir(), 'recording-%d-%t']);
            const [ok, path] = await ui._screencastProxy.ScreencastAreaAsync(
                Math.round(x), Math.round(y), Math.round(w), Math.round(h), template,
                {'draw-cursor': new GLib.Variant('b', cursor)});
            if (!ok)
                throw new Error('recorder refused to start');
            this._recordingPath = path;
            return true;
        } catch (e) {
            ui._setScreencastInProgress(false);
            throw e;
        }
    }

    StopRecording() {
        Main.screenshotUI.stopScreencast().catch(logError);
    }

    _onScreencastChanged() {
        this._dbus?.emit_property_changed('Recording', new GLib.Variant('b', this.Recording));
        if (Main.screenshotUI.screencast_in_progress || !this._recordingPath)
            return;
        // stopped (from us or GNOME's panel button): wait for the file to be finalised
        const path = this._recordingPath;
        this._recordingPath = null;
        let lastSize = -1, tries = 0;
        GLib.timeout_add(GLib.PRIORITY_DEFAULT, 300, () => {
            const file = Gio.File.new_for_path(path);
            let size = -1;
            try {
                size = file.query_info('standard::size', 0, null).get_size();
            } catch {}
            if ((size > 0 && size === lastSize) || ++tries > 40) {
                this._dbus?.emit_signal('RecordingFinished', new GLib.Variant('(s)', [path]));
                return GLib.SOURCE_REMOVE;
            }
            lastSize = size;
            return GLib.SOURCE_CONTINUE;
        });
    }

    SetClipboardImage(path) {
        const [, data] = GLib.file_get_contents(path);
        St.Clipboard.get_default().set_content(St.ClipboardType.CLIPBOARD, 'image/png',
            new GLib.Bytes(data));
    }

    SetClipboardText(text) {
        St.Clipboard.get_default().set_text(St.ClipboardType.CLIPBOARD, text);
    }

    // a non-interactive outline around an area (scrolling capture shows what it records)
    ShowFrame([x, y, w, h]) {
        if (!this._frame) {
            this._frame = new St.Widget({style_class: 'screend-frame', reactive: false});
            Main.layoutManager.uiGroup.add_child(this._frame);
            Shell.util_set_hidden_from_pick(this._frame, true);
        }
        const pad = 4; // keep the outline itself out of the captured area
        this._frame.set_position(x - pad, y - pad);
        this._frame.set_size(w + 2 * pad, h + 2 * pad);
        this._frame.show();
    }

    HideFrame() {
        this._frame?.destroy();
        this._frame = null;
    }

    ManageWindow(title, mode, x, y) {
        this._rules.add(title, mode, x, y);
    }
}
