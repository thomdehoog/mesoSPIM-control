# Remote Control Bench Report - 2026-07-07

## Scope

Live validation of the refactored Remote Control PR against a real
`mesoSPIM_Core` in demo mode.

The refactor keeps mesoSPIM-control itself TCP-only. MCP is shipped as a
separate adapter process that forwards MCP JSON-RPC tool calls to the same
framed TCP command server.

## Test Setup

- Worktree: `C:\ProgramData\MinicondaZMB\home\t.de\mesospim-control-py312-bench`
- Branch: `bench-remote-scripting-py312`
- Base: `origin/release/candidate-py312` at `560dcf0`
- Original PR commit: `611a3bc Add optional remote scripting server (Tools -> Remote Scripting...)`
- mesoSPIM mode: `python mesoSPIM_Control.py -D`
- GUI process during final validation: `python.exe` PID `35692`
- TCP server: `127.0.0.1:42000`
- MCP adapter smoke endpoint: `http://127.0.0.1:42100/mcp`
- Token auth: enabled

## Architecture

- `mesoSPIM/src/mesoSPIM_RemoteCommands.py` owns the shared command allowlist,
  JSON validation, and Core execution.
- `mesoSPIM/src/mesoSPIM_RemoteScripting.py` owns only the framed TCP transport
  and token gate.
- `mesoSPIM/mesoSPIM_MCP_Adapter.py` owns MCP/HTTP. It does not import or call
  `mesoSPIM_Core`; every `tools/call` opens a TCP connection, authenticates,
  sends the same JSON command, and wraps the TCP reply as MCP content.
- The GUI exposes one Remote Control mode at a time: direct TCP, or MCP plus a
  private localhost TCP backend.
- In MCP mode the TCP backend is bound to `127.0.0.1:0`, so the OS chooses an
  ephemeral port. The backend also receives a separate generated token that is
  not the MCP bearer token.

## Commands

Framed TCP integration suite with demo acquisition enabled:

```powershell
$env:MESOSPIM_HOST='127.0.0.1'
$env:MESOSPIM_PORT='42000'
$env:MESOSPIM_TOKEN='<token>'
$env:MESOSPIM_ALLOW_ACQUIRE='1'
python -m pytest tests -m integration -v -s
```

MCP adapter smoke checks were run by starting the adapter manually against the
live TCP server:

```powershell
python mesoSPIM\mesoSPIM_MCP_Adapter.py `
  --host 127.0.0.1 --port 42100 --token <token> `
  --mesospim-host 127.0.0.1 --mesospim-port 42000 --mesospim-token <token>
```

Then JSON-RPC POST requests were sent to `http://127.0.0.1:42100/mcp`.

## Results

Static/local checks:

- `python -m py_compile mesoSPIM\src\mesoSPIM_RemoteCommands.py mesoSPIM\src\mesoSPIM_RemoteScripting.py mesoSPIM\mesoSPIM_MCP_Adapter.py mesoSPIM\src\mesoSPIM_MainWindow.py mesoSPIM\src\mesoSPIM_Core.py`: pass
- direct regression against real `mesoSPIM_StateSingleton`: pass
- source inspection confirmed HTTP, JSON-RPC, Origin, and Bearer request
  handling live in the external adapter, not in `mesoSPIM_Core`

Manual TCP smoke:

- authentication: pass
- `hello`: pass
- `get_state`: pass
- `get_position`: pass
- `get_config`: pass, including live lasers, filters, zoom pixel sizes, and
  camera dimensions `5056x2960`

ZMART live TCP integration suite:

```text
5 passed, 124 deselected
```

The passing tests covered:

- handshake and protocol identity
- live config binding
- live state and position binding
- zero-net-motion `move_absolute`
- demo acquisition file write

MCP adapter smoke coverage:

- `initialize`: pass
- `tools/list`: pass, returned 15 tools from the shared allowlist
- `tools/call get_state`: pass
- `tools/call get_config`: pass
- `tools/call does_not_exist`: pass as controlled MCP tool error
- wrong bearer token: pass, returned `401`
- separate MCP bearer token and TCP backend token: pass
- backend TCP token used as MCP bearer token: pass, returned `401`

## Notes

During final validation the GUI was left in TCP mode. That is not a problem for
adapter validation: the adapter is intentionally a separate process that talks
to a TCP server. Selecting MCP in the GUI starts the same kind of adapter
process automatically, but uses a hidden ephemeral localhost TCP port and a
separate backend token so MCP remains the only advertised control surface.

`tools/call zero` was not run because it changes the operator coordinate origin.
