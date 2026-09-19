# Design concept — Milonga

A PyQt6 replacement for Astor + Jive (with an ATK-Panel-class device view).

---

## 1. Key design aspects

These are the decisions everything else follows from. They are listed first
because they are the ones worth arguing about.

### KD-1 — `tango` is imported in exactly one package

All Tango access sits behind an async `TangoBackend` protocol in `milonga.core`.
`import tango` appears only in `milonga/core/backend/pytango.py`. Consequences:

* the whole application logic is testable against `FakeBackend` with no running
  control system — this is what makes the project maintainable at all;
* a second backend (e.g. a REST/TangoGQL gateway for a remote/webbed setup) is a
  drop-in later;
* PyTango's quirks (DevFailed chains, blocking constructors, green modes) are
  contained in one reviewable file.

### KD-2 — Nothing blocks the GUI thread, structurally rather than by discipline

Tango calls are network RPCs that can hang for the full client timeout (3 s
default, longer for a dead host). A tree of 100 hosts must never serialise those.

The rule is enforced by shape: the backend protocol exposes **only coroutines**.
There is no synchronous entry point to call by accident.

```
┌──────────────── GUI thread ────────────────┐
│ Qt widgets ── Qt models ── ViewModels      │
│        │                       │           │
│        │            await backend.*()      │   qasync: asyncio loop
│        │                       │           │   driven by the Qt event loop
└────────┼───────────────────────┼───────────┘
         │                       │
         │            ┌──────────▼────────────┐
         │            │ TangoBackend (async)  │
         │            ├───────────────────────┤
         │            │ pytango asyncio green │  non-blocking device I/O
         │            │ mode (DeviceProxy)    │
         │            ├───────────────────────┤
         │            │ ThreadPoolExecutor    │  Database calls, proxy
         │            │ (bounded, per host)   │  construction, subscribe_event
         │            └──────────┬────────────┘
         │                       │ PyTango event thread
         │  loop.call_soon_threadsafe ──> MonitorHub ──> Qt signals
         └───────────────────────────────────────────────┘
```

* `qasync` runs one asyncio loop **on** the Qt event loop: no cross-thread data
  races in application state, cancellation is `task.cancel()`, and timeouts are
  `asyncio.timeout(...)` instead of ad-hoc watchdogs.
* Anything PyTango offers in `asyncio` green mode (`tango.asyncio.DeviceProxy`)
  is awaited directly. Everything else — `tango.Database`, proxy construction,
  `subscribe_event` — is pushed to a bounded `ThreadPoolExecutor`. The bound is
  per host, so one dead machine cannot starve the pool for the others.
* Event callbacks arrive on a PyTango-owned thread. They are marshalled with
  `loop.call_soon_threadsafe` and never touch a widget directly.

*Rejected alternative:* `QThreadPool` + `QRunnable` + signals. It works, but
every operation that is a sequence of calls ("register server, update starter,
start it, wait for it to appear") becomes a state machine, and cancelling a
half-finished sequence when a panel closes becomes guesswork. `async def` writes
those sequences linearly, and `CancelledError` unwinds them correctly.

### KD-3 — Events first, polling as a declared fallback

A refresh timer over a 10 000-device database is the reason the Java tools feel
slow. Instead:

* `MonitorHub` owns every subscription. Widgets ask for
  `hub.watch("sys/tg_test/1", "double_scalar")` and get a handle; the hub keeps
  **one** subscription per `(device, attribute, event_type)` no matter how many
  widgets want it, and drops it when the last handle is released.
* Handles are bound to widget visibility: a collapsed tree branch or a hidden tab
  costs nothing. This is the single biggest lever on perceived responsiveness.
* If `subscribe_event` fails (no event system configured, server too old), the
  hub transparently degrades to polling that attribute at a declared period and
  **marks the data source in the UI** (a small dotted underline on the value), so
  operators know whether they are looking at pushed or pulled data.
* Host state uses the Starter `Servers` attribute — one subscription per host
  gives the complete per-server state table.

### KD-4 — One snapshot store, diffed; Qt models are thin adapters

`SystemStore` holds immutable dataclass snapshots (`HostSnapshot`,
`ServerSnapshot`, `DeviceSnapshot`, …). Updates produce a new snapshot plus a
**diff**; Qt models translate the diff into `dataChanged` on the affected rows
only. No `beginResetModel()` on refresh, therefore selection, expansion state,
scroll position and in-progress edits survive live updates.

### KD-5 — Every database mutation is a Command object

Jive edits a live control system's database, and its mistakes are silent. Here,
each mutation implements:

```python
class Command(Protocol):
    def describe(self) -> str: ...
    async def preview(self, backend: TangoBackend) -> Diff: ...
    async def apply(self, backend: TangoBackend) -> None: ...
    async def revert(self, backend: TangoBackend) -> None: ...   # or RevertUnsupported
```

* Bulk edits show the `Diff` before anything is written.
* Applied commands land in a **Journal** dock: timestamp, user, target, undo
  button when `revert` is supported (property writes capture the previous value;
  a delete captures the full record).
* `ReadOnlyBackend` wraps the backend and refuses `apply` — read-only is one
  decorator, not a flag checked in forty widgets. The same wrapper is used to
  reflect Tango Access Control rights.
* Destructive commands (delete server, hard kill, delete device) require typed
  confirmation of the object name, not just an OK button.

### KD-6 — Attribute rendering is a registry, not a switch statement

```python
Renderer = Callable[[AttributeSpec], QWidget]
registry.register(key=(DataFormat.SPECTRUM, DataType.NUMERIC), factory=SpectrumView)
registry.register(class_name="MyMotor", attribute="position", factory=MotorDial)
```

Lookup order: `(class, attribute)` → `(class, format/type)` → `(format, type,
writable)` → generic fallback. Sites plug in their own widgets through the
`milonga.renderers` entry point group without forking. The same mechanism
provides whole **class panels** (`milonga.panels`), so a site's motor panel
replaces the generic device panel for that class.

### KD-7 — One navigator, many scopes; one address space

Jive's five trees and Astor's host tree become one navigator with a scope
switcher (Hosts / Servers / Devices / Classes / Aliases / Free properties). Every
object has a URI — `tango://host:10000/sys/tg_test/1#attributes` — which is what
the command palette resolves, what tabs restore from, and what "copy link"
produces for pasting into a ticket.

### KD-8 — Lazy, virtualised, filtered

Trees fetch children on expansion (`get_device_member_list` per family, not a
full dump). Filtering happens in a `QSortFilterProxyModel` over what is loaded,
plus an explicit "search the database" action for property-value searches that
must hit the server. Startup does not read the whole database.

### KD-9 — Errors are data, not dialogs

`DevFailed` is converted to a structured `TangoError` (severity, reason, desc,
origin, full chain). Panels show an inline banner with a "details" disclosure;
modal error boxes are reserved for actions the user explicitly triggered and that
failed entirely. Stale data is greyed with a "last update 12 s ago" tooltip
rather than silently frozen.

---

## 2. Package layout

```
milonga/
  core/                     # no Qt import anywhere below this line
    backend/
      protocol.py           # TangoBackend Protocol + DTOs
      pytango_backend.py    # the only file importing `tango`
      fake_backend.py       # in-memory control system for tests/demo
      readonly.py           # decorator enforcing read-only / TAC
    model/                  # frozen dataclasses: Host, Server, Device,
                            # AttributeSpec, AttributeValue, PropertyEntry, ...
    store.py                # SystemStore: snapshots + diffs
    monitor.py              # MonitorHub: subscriptions, dedup, polling fallback
    commands/               # property_edit, server_create, device_add,
                            # rename, delete, starter_ops, attr_config, ...
    services/
      inventory.py          # database browsing + caching
      control.py            # Starter operations, level orchestration
      diagnostics.py        # polling, events, statistics, versions
    uri.py, errors.py, config.py
  ui/
    app.py, mainwindow.py, theme.py, palette.py
    models/                 # QAbstractItemModel adapters over the store
    widgets/                # StateChip, ValueEditor, SpectrumView, ImageView,
                            # PropertyTable, LevelLane, LogTail, DiffView
    panels/                 # overview, host, server, device, properties,
                            # polling, events, statistics, access_control
    dialogs/                # wizards, confirmations, preferences
    palette_search.py       # Ctrl+K command palette
  plugins/                  # entry-point discovery
  cli.py
tests/
  core/                     # pure, against FakeBackend
  ui/                       # pytest-qt smoke + model tests
  integration/              # optional, docker tango-cs + TangoTest + Starter
```

Dependencies: `PyQt6`, `pytango`, `pyqtgraph` (plots/images), `qasync`,
`numpy`, `platformdirs`. Nothing else in the core.

---

## 3. Data flow, end to end

```
user expands "Hosts"                      Starter `Servers` change event
        │                                            │
        ▼                                            ▼
HostTreeModel.fetchMore()                   MonitorHub callback
        │                                            │
   await inventory.hosts()                  loop.call_soon_threadsafe
        │                                            │
        ▼                                            ▼
   TangoBackend ──── executor ──> Database     SystemStore.apply(diff)
        │                                            │
        ▼                                            ▼
   SystemStore.apply(diff) ──────────────> storeChanged(diff) [Qt signal]
                                                     │
                            ┌────────────────────────┼───────────────────┐
                            ▼                        ▼                   ▼
                     HostTreeModel            OverviewPanel         StatusBar
                    (dataChanged rows)        (chips repaint)     (counters)
```

A write follows the mirror path: panel → `Command` → preview `Diff` → user
confirms → `apply` → store invalidation → diff → views.

---

## 4. GUI concept

### 4.1 Frame

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ● tango-cs:10000 ▾ │ ⌕ search devices, servers, hosts…  (Ctrl+K) │ ⟳ │ 🔒 RO │
├──────────────┬───────────────────────────────────────────────┬───────────────┤
│ NAVIGATOR    │  ┌ System ┐┌ tango-srv-02 ┐┌ sys/tg_test/1 ×┐  │ INSPECTOR     │
│ ┌──────────┐ │  ├────────┴┴──────────────┴┴────────────────┴┐ │               │
│ │Hosts  ▾  │ │  │                                           │ │ sys/tg_test/1 │
│ └──────────┘ │  │                                           │ │ ● ON          │
│ ⌕ filter…    │  │            document area                  │ │ TangoTest     │
│              │  │         (tabs, splittable)                │ │ TangoTest/tst │
│ ▾ beamline   │  │                                           │ │ tango-srv-02  │
│   ● srv-01   │  │                                           │ │               │
│   ◐ srv-02   │  │                                           │ │ [Restart]     │
│   ○ srv-03   │  │                                           │ │ [Properties]  │
│   ✕ srv-04   │  │                                           │ │ [Monitor]     │
│ ▸ control    │  │                                           │ │               │
│ ▸ storage    │  │                                           │ │               │
├──────────────┴───────────────────────────────────────────────┴───────────────┤
│ JOURNAL │ ERRORS │ LOG        12:04:31 put_property dev/1 Polled=…   [undo]   │
├──────────────────────────────────────────────────────────────────────────────┤
│ 142 devices · 18 hosts · 63 subscriptions · 2 pending · events OK             │
└──────────────────────────────────────────────────────────────────────────────┘
```

Left navigator (scope switcher + filter), centre tabbed document area (any panel
can be split or torn off), right contextual inspector, bottom journal/errors,
status bar carrying the health of the connection itself.

### 4.2 Panels

| Panel | Replaces | Content |
|---|---|---|
| **System overview** | Astor main window | Host cards or dense table, state chip, running/stopped counts, level progress, multi-select bulk start/stop |
| **Host** | Astor host status window | Servers grouped in **level lanes** (level 1 … N, "not controlled"), per-server chip + PID + uptime, start/stop/restart/hard-kill, log tail, Starter properties |
| **Server** | Astor + Jive server node | Instance info, served classes, devices, host/level editor, controls, log, statistics, Tango version |
| **Device** | Jive test panel + AtkPanel | Header (state/status/quality), tabs: Attributes · Commands · Properties · Attribute config · Polling · Events · Logging · Info |
| **Properties** | Jive property editor | Multi-row table, array values, history timeline, diff preview, copy/paste across objects |
| **Class** | Jive class node | Class properties, class attribute properties, device instances |
| **Free properties** | Jive property tree | object → property → value editor |
| **Polling** | Astor polling window | Polled items per server, periods, ring depth, thread pool, poll status |
| **Events** | Astor event manager | Event channel per server, subscribers per attribute, live event test |
| **Statistics / Versions** | Astor tools | Restart counts, uptime, IDL/release version matrix |
| **Access control** | Astor TAC | Users, IP masks, per-device command rights |
| **Wizards** | Astor/Jive wizards | New server, new device, add host, class property wizard |

### 4.3 Device panel sketch

```
┌ sys/tg_test/1 ──────────────────────────────────── ● ON  "Device is ON" ─────┐
│ Attributes │ Commands │ Properties │ Config │ Polling │ Events │ Logs │ Info │
├──────────────────────────────────────────────────────────────────────────────┤
│ ⌕ filter   ☑ scalars ☑ spectra ☑ images        refresh: events ▾   ⏸ pause   │
│                                                                              │
│  SCALARS                                                                     │
│   double_scalar     12.874  mm   ▏ VALID  12:04:31   [ 12.874    ] [Write]   │
│   long_scalar          143       ▏ VALID  12:04:31   [ 143       ] [Write]   │
│   state             ● ON         ▏ VALID  12:04:29                           │
│   string_scalar     "idle"       ▏ ALARM  12:04:31   [ idle      ] [Write]   │
│                                                                              │
│  SPECTRA                              ┌──────────── double_spectrum ───────┐ │
│   double_spectrum  [1024] ▸           │      ╱╲      ╱╲                    │ │
│   long_spectrum    [256]  ▸           │  ╱╲ ╱  ╲  ╱╲╱  ╲   ╱╲              │ │
│                                       └────────────────────────────────────┘ │
│  IMAGES                                                                      │
│   double_image     [256×256] ▸        [image view, LUT, ROI readout]         │
└──────────────────────────────────────────────────────────────────────────────┘
```

Writable attributes keep the write value in a separate editor next to the read
value; a write is always an explicit action, never a side effect of focus loss.

### 4.4 Visual language

* **State chips** are the only place Tango state colours are defined
  (`ON`/`RUNNING` green, `ALARM`/`STANDBY` amber, `FAULT` red, `MOVING` blue,
  `UNKNOWN`/`OFF` grey, unreachable = red outline). Colour is always paired with
  a shape/letter so it survives colour-blindness and greyscale screenshots.
* Quality colours are distinct from state colours and only appear on values.
* Light and dark themes from one token set; density toggle (comfortable/compact)
  because control rooms run both 24" desks and 4K wall displays.
* No modal progress dialogs. Long operations show inline progress on the object
  they affect and remain cancellable.

---

## 5. Milestones

| # | Deliverable | Why this order |
|---|---|---|
| 0 | `core.backend` protocol + `FakeBackend` + store + monitor hub, no GUI | Everything else is testable from day one |
| 1 | Shell: main window, navigator, palette, journal, theme | The frame every panel plugs into |
| 2 | Jive read path: device/server/class trees, property viewer, device info | Highest daily value, read-only, low risk |
| 3 | Device panel: attributes, commands, spectra, images | Unblocks "is my device alive" |
| 4 | Write path: Command/Diff/Journal, property editing, attribute config | First mutations, with the safety net already in place |
| 5 | Astor: overview, host panel, Starter control, levels, logs | Needs the write path's confirmation machinery |
| 6 | Wizards, polling, events, statistics, versions, TAC | The long tail that makes replacement complete |
| 7 | Packaging, plugin entry points, docs | Ship |

---

## 6. Risks and where they are handled

| Risk | Handling |
|---|---|
| PyTango asyncio green mode has gaps or surprises | Contained in `pytango_backend.py`; any call without a green variant goes to the executor. Measure per call, do not assume |
| `DeviceProxy()` construction blocks on unreachable hosts | Always constructed in the executor, cached per device, with a per-host circuit breaker after repeated failures |
| Event callbacks from PyTango threads | Single funnel through `loop.call_soon_threadsafe`; no widget touched off-loop |
| Dead host freezes the UI | Bounded per-host executor + per-call timeout + circuit breaker; the tree shows the host as unreachable instead of waiting |
| Accidental destructive DB edit | Command preview, typed confirmation, journal with undo, read-only decorator |
| Qt binding lock-in | Only `ui/` imports Qt; no Qt types cross into `core` |
| Large spectra/images at high event rates | Decimation for display, frame dropping with a visible "N frames skipped" marker, per-panel pause |
