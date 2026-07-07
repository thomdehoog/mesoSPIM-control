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
- GUI process during final validation: `python.exe` PID `30428`
- TCP mode endpoint: `127.0.0.1:42000`
- MCP mode endpoint: `http://127.0.0.1:42100/mcp`
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

MCP mode was then started from `Tools > Remote Control...`. The GUI started the
adapter on `42100` and an internal TCP backend on an ephemeral localhost port.

```powershell
Get-NetTCPConnection -State Listen |
  Where-Object { $_.LocalAddress -eq '127.0.0.1' -and
                 ($_.LocalPort -eq 42000 -or $_.LocalPort -eq 42100 -or
                  $_.OwningProcess -eq 30428) }
```

The observed layout was:

```text
127.0.0.1:42100  MCP adapter
127.0.0.1:57454  private TCP backend
127.0.0.1:42000  no listener
```

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

GUI MCP mode coverage:

- `initialize`: pass
- `tools/list`: pass, returned 15 tools from the shared allowlist
- `tools/call hello`: pass
- `tools/call get_state`: pass
- `tools/call get_config`: pass, including camera dimensions `5056x2960`
- `tools/call get_position`: pass
- `tools/call ping`: pass
- `tools/call get_progress`: pass
- `tools/call move_absolute` to the current position: pass
- `tools/call move_relative` with zero delta: pass
- `tools/call zero` for all axes: pass
- `tools/call set_state` with the current intensity: pass
- `tools/call stop`: pass
- `tools/call acquire_start` demo snap: pass
- `tools/call stat_files` for the acquired file: pass
- `tools/call stat_files` for a missing file: pass
- `tools/call acquire_finish`: pass
- `tools/call procedure`: pass as controlled error, because server-side
  procedures are not implemented
- wrong bearer token: pass, returned `401`
- disallowed Origin: pass, returned `403`
- direct TCP on `127.0.0.1:42000`: pass, connection refused
- private TCP backend with the public MCP token: pass, returned `AUTH-FAILED`

## Notes

The MCP backend still uses the same TCP command processor, but it is private
plumbing: the public TCP port is closed, the backend port is ephemeral, and the
backend token is separate from the MCP bearer token.

All 15 allowlisted remote-control commands were exercised. The `procedure`
command currently has no server-side implementation, so the expected result is
a controlled MCP tool error.
