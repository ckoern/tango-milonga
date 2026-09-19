# Milonga

A Qt administration console for [Tango Controls](https://www.tango-controls.org/),
replacing the Java tools *Astor* (process control through Starter devices) and
*Jive* (database editing), with a generic device panel in place of AtkPanel.

*Milonga* is a working name.

## Documentation

| Document | Content |
|---|---|
| [docs/01-functional-analysis.md](docs/01-functional-analysis.md) | What Astor, Jive and ATK do, feature by feature, and the Tango API each feature sits on |
| [docs/02-design-concept.md](docs/02-design-concept.md) | Architecture, key design decisions, package layout, milestones, risks |
| [docs/mockup.html](docs/mockup.html) | Interactive GUI mockup (open in a browser) |

## Status

Milestones 0 to 2 of the plan in the design concept are implemented: the domain
layer, the application shell, and the read path of Jive. Writing to the database
and live attribute values are not implemented yet, and neither is the PyTango
backend — the application currently runs against the in-memory demo system.

![Device panel](docs/screenshot-device.png)

![Host tree and host panel](docs/screenshot-hosts.png)

```
milonga/core/                               no Qt, no PyTango
  names.py, enums.py, errors.py, model.py   value objects and snapshots
  backend/protocol.py                       the async contract, coroutines only
  backend/fake.py, backend/demo.py          in-memory control system, Starter included
  backend/readonly.py                       read-only enforcement in one wrapper
  store.py                                  snapshots plus diffs
  monitor.py                                subscription ownership, polling fallback
  services/                                 Starter control, inventory, host groups
milonga/ui/                                 the only package importing Qt
  app.py, cli.py                            qasync bootstrap and command line
  theme.py                                  design tokens, light and dark
  tasks.py                                  coroutines tied to a widget's lifetime
  navigator.py                              one tree, six scopes, lazily loaded
  models/                                   lazy tree, generic table, row painting
  panels/                                   device, server, class, object, host
  mainwindow.py, search.py                  window chrome and the command palette
```

## Running

```bash
.venv/bin/python -m milonga.cli                       # demo control system
.venv/bin/python -m milonga.cli --theme light --scope hosts
.venv/bin/python -m milonga.cli sys/tg_test/1         # open a device at startup
```

Keys: `Ctrl+K` search, `F5` refresh the current panel.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev,gui]'

.venv/bin/pytest          # 136 tests, no control system needed
.venv/bin/mypy milonga tests
.venv/bin/ruff check .
```

## Demo system

`milonga.core.backend.demo.build_demo_backend()` returns a populated control
system — six hosts in two groups, one of them unreachable, servers across three
startup levels, a `TangoTest` device with scalar, spectrum and image attributes,
and Starter devices that really start and stop servers.

```python
import asyncio
from milonga.core.backend.demo import build_demo_backend
from milonga.core.services.control import StarterControl

async def main() -> None:
    control = StarterControl(build_demo_backend())
    snapshot = await control.host_snapshot("id09-srv-02")
    print(snapshot.state, snapshot.running_count, "running")

asyncio.run(main())
```
