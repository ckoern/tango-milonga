# Functional analysis: what Astor, Jive and ATK actually do

Scope of the replacement: **all of Astor + all of Jive**, plus the generic device
panel that Jive's "Test device" already is (i.e. an ATK-Panel equivalent), because
the two tools are unusable without it.

Working name for the replacement: **Milonga** (package `milonga`, entry point
`milonga`). Placeholder — trivial to rename, it appears only in `pyproject.toml`
and the package directory.

---

## 1. Astor — process/deployment control

### 1.1 The model Astor operates on

Astor is a client of the **Starter** device server. One Starter instance runs per
host and owns every device server *registered to that host* in the Tango database.

* Starter device name must be `tango/admin/<short-hostname>` to be discovered.
* Per-server database record (`DbServerInfo`): `name` (`Class/instance`), `host`,
  `mode`, `level`.
  * `level` 0 = "not controlled" (Starter knows the server but never starts it),
    1..N = **startup level**; Starter starts level 1, waits
    `InterStartupLevelWait` seconds, starts level 2, etc.
  * `mode` = controlled/uncontrolled flag.
* Starter device interface:

  | Command | In → Out | Meaning |
  |---|---|---|
  | `DevStart` | `str` → void | start `Class/instance` |
  | `DevStop` | `str` → void | stop (`Kill` on the admin device) |
  | `HardKillServer` | `str` → void | `kill -9` |
  | `DevStartAll` / `DevStopAll` | `short` → void | whole startup level |
  | `DevGetRunningServers` | `bool` → `str[]` | running processes |
  | `DevGetStopServers` | `bool` → `str[]` | registered but stopped |
  | `DevReadLog` | `str` → `str` | tail of the server's log file |
  | `UpdateServersInfo` | void | re-read controlled-server list from the DB |
  | `NotifyDaemonState` | void → state | notifd status (legacy) |
  | `ResetStatistics` | void | clear the statistics file |

  | Attribute | Type | Meaning |
  |---|---|---|
  | `HostState` | scalar short | aggregated host state |
  | `RunningServers` | spectrum str | running server names |
  | `StoppedServers` | spectrum str | stopped server names |
  | `Servers` | spectrum str | one line per server: name, state, controlled, level |
  | `NotifdState` | scalar state | notification daemon |

  | Property | Meaning |
  |---|---|
  | `StartDsPath` | search path for executables |
  | `StartServersAtStartup`, `InterStartupLevelWait`, `ServerStartupTimeout` | boot behaviour |
  | `AutoRestartDuration` | auto-restart failed servers for N minutes |
  | `LogFileHome`, `KeepLogFiles` | log file handling |
  | `UseEvents`, `WaitForDriverStartup` | transport / boot tuning |

  `Servers` is the one attribute worth subscribing to: it is the whole host state
  in a single change event.

### 1.2 Astor's UI surface

| Astor feature | What it is |
|---|---|
| Main tree | Hosts grouped into user-defined *families/branches*, colour-coded |
| Host colours | green = all running, orange = mixed, white = all stopped, blue = starting, red = Starter unreachable |
| Server colours | green = running, blue = running but not answering ping, red = stopped |
| Host status window | All servers of a host grouped **by startup level**; right-click → start / stop / restart / hard kill / test / read log; optional display of uncontrolled servers (excluded from host state) |
| Add controlled host | Register a `Starter/<host>` server + `tango/admin/<host>` device |
| Start a new server | Register server on a host, set level, `UpdateServersInfo`, `DevStart` |
| New server wizard | Create server → class → devices → properties in one flow |
| Multi start/stop | Select many servers across hosts, act level-ordered |
| Branch management | Create/rename host groups, move hosts between them (shared between users) |
| Server statistics | Restart counts / uptime from Starter statistics, `ResetStatistics` |
| Polling management | Per server: polled attributes/commands, period, ring depth, polling thread pool, start/stop polling |
| Event manager | Event channel per server, subscribers per attribute, event reception test |
| Tango versions | IDL/release version per running server (find servers on old Tango) |
| TAC configuration | Edit the `AccessControl` device: users, IP masks, per-device command rights |
| Remote login | Open an ssh session on the host |
| Read-only modes | full read/write, DB read-only, fully read-only |
| Device test | Bundled device browser + test panel (same as Jive's) |

---

## 2. Jive — database control

Jive is a database editor plus a device test panel. It is the tool people actually
live in, so its ergonomics matter more than Astor's.

### 2.1 Trees / scopes

* **Server** — `Class` → `instance` → served class → device.
* **Device** — `domain` → `family` → `member`.
* **Class** — class → its class-level properties and attribute properties.
* **Alias** — device aliases.
* **Property** — *free properties*: object → property (not attached to a device).
* Node filtering, multiple selection, collection display.

### 2.2 Operations

**Server scope**
* Create a server (register `Class/instance` with its first device).
* Add / remove devices to an existing server, add / remove served classes.
* Rename a server (`DbRenameServer`), delete a server.
* Restart / stop server through the host's Starter (Jive delegates to Starter).
* Server info: host, exported state, PID, start/stop timestamps.

**Device scope**
* Device properties: create, edit (scalar and array values), rename, delete,
  **property history**, copy/paste between devices, multi-row editing.
* Rename a device, delete a device, unexport, define/remove alias.
* Device info (`DbGetDeviceInfo`): class, server, host, exported, IOR, PID,
  last exported / unexported.
* **Test device panel**: state/status, attribute list with live values and write
  fields, command list with argin editor and result display, per-attribute
  monitoring/plot.
* **Attribute configuration**: label, unit, standard unit, display unit, format,
  min/max value, min/max alarm, min/max warning, delta, description, display
  level, event configuration (absolute/relative change, period, archive
  criteria), and attribute-level *properties* stored in the DB.
* **Polling**: enable/disable per attribute/command, polling period, external
  trigger, ring depth, poll status.
* **Logging**: add/remove logging targets (file, console, device), log level.
* **Device wizard**: walk the class' declared properties and prompt for values.

**Class scope**
* Class properties (defaults inherited by every device of the class).
* Class-level attribute properties.

**Free properties**
* Arbitrary `object → property → value[]` storage used for site configuration.

**Cross-cutting**
* Search: by device name, alias, class, server, and by property *value*.
* Multiple editing: apply one edit to many selected rows/objects at once.

---

## 3. ATK / AtkPanel — generic device GUI

ATK is a Swing widget toolkit; the part that matters here is **AtkPanel**, the
generic per-device window: state/status header, one row per attribute with
live value, unit, quality colour, write field for writable attributes, and
plot/image viewers for `SPECTRUM` / `IMAGE` attributes, command buttons, plus
error display and automatic refresh via events with polling fallback.

Jive's "Test device" is a weaker version of the same thing. The replacement needs
**one** device panel implementation used by both entry points, covering:

| Data format | Read rendering | Write rendering |
|---|---|---|
| `SCALAR` numeric | value + unit + quality colour, sparkline on demand | spin/line edit, slider for bounded |
| `SCALAR` string | text, monospace if long | line edit / multiline |
| `SCALAR` bool | state chip | toggle |
| `SCALAR` enum / `DevState` | labelled chip | combo box |
| `SPECTRUM` | line plot + optional table, X from `DevLong` index or paired attribute | table editor / CSV paste |
| `IMAGE` | 2-D image view, colour map, LUT, cross-cuts, ROI readout | file / array import (rare) |
| Any | quality (`VALID/CHANGING/ALARM/WARNING/INVALID`) drives colour; timestamp shown | write is a separate explicit action |

Pipes (`DevicePipe`) exist in Tango ≥ 9 and neither tool handles them well; the
replacement should at least display them read-only.

---

## 4. Consolidated requirement list for the replacement

1. **Multi control system**: several `TANGO_HOST` databases open at once.
2. **One window**, dockable panels and tabs, no swarm of free-floating windows.
3. **Live everywhere**: states update by Tango events, never by a global timer
   that refreshes the whole tree.
4. **Safe writes**: every database mutation is explicit, previewable, logged and,
   where the Tango API allows, undoable. A read-only mode that is actually
   enforced in one place.
5. **Scale**: usable with 10 000+ devices, 100+ hosts — lazy trees, no full DB
   dump on startup.
6. **Search first**: one palette resolves device / alias / server / class / host.
7. **Complete**: nothing in the Astor/Jive feature tables above may be missing,
   including the unglamorous parts (property history, TAC, polling pools,
   attribute config, logging targets).
