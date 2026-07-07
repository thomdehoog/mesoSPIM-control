# Remote Scripting Bench Report - 2026-07-07

## Scope

Live validation of the optional Remote Scripting server against a real
`mesoSPIM_Core` in demo mode.

## Test Setup

- Worktree: `C:\ProgramData\MinicondaZMB\home\t.de\mesospim-control-py312-bench`
- Branch: `bench-remote-scripting-py312`
- Base: `origin/release/candidate-py312` at `560dcf0`
- PR commit under test: `611a3bc Add optional remote scripting server (Tools -> Remote Scripting...)`
- mesoSPIM mode: `python mesoSPIM_Control.py -D`
- Server: `127.0.0.1:42000`
- Server process: `python.exe` PID `7580`
- Token auth: enabled

## Commands

Framed TCP integration suite:

```powershell
$env:MESOSPIM_HOST='127.0.0.1'
$env:MESOSPIM_PORT='42000'
$env:MESOSPIM_TOKEN='<token>'
python -m pytest zmart_drivers\mesospim\tests -m integration -v
```

MCP HTTP smoke checks:

```powershell
curl.exe -s http://127.0.0.1:42000/mcp `
  -H "Authorization: Bearer <token>" `
  -H "Content-Type: application/json" `
  --data-binary "@mesospim_mcp_initialize.json"

curl.exe -s http://127.0.0.1:42000/mcp `
  -H "Authorization: Bearer <token>" `
  -H "Content-Type: application/json" `
  --data-binary "@mesospim_mcp_tools_list.json"

curl.exe -s http://127.0.0.1:42000/mcp `
  -H "Authorization: Bearer <token>" `
  -H "Content-Type: application/json" `
  --data-binary "@mesospim_mcp_get_state.json"
```

Safety checks:

```powershell
curl.exe -s -o NUL -w "%{http_code}\n" http://127.0.0.1:42000/mcp `
  -H "Content-Type: application/json" `
  --data-binary "@mesospim_mcp_tools_list.json"

curl.exe -s -o NUL -w "%{http_code}\n" http://127.0.0.1:42000/mcp `
  -H "Authorization: Bearer <token>" `
  -H "Origin: http://evil.example" `
  -H "Content-Type: application/json" `
  --data-binary "@mesospim_mcp_tools_list.json"
```

## Results

The live server started successfully and listened on `127.0.0.1:42000`.

The framed TCP integration suite reached the server, but every live check that
depends on the handshake was skipped because `hello` failed inside the server:

```text
hello failed: error: 'mesoSPIM_StateSingleton' object has no attribute 'get'
```

MCP HTTP envelope checks:

- `initialize`: pass
- `tools/list`: pass, returned the `COMMANDS` allowlist
- `tools/call get_state`: fail with the same state access error
- missing bearer token: pass, returned `401`
- disallowed Origin `http://evil.example`: pass, returned `403`

`tools/call get_config` returned successfully, but exposed additional binding
drift:

- camera size fell back to `2048x2048` instead of using
  `cfg.camera_parameters["x_pixels"]` and `cfg.camera_parameters["y_pixels"]`
- zoom `pixel_size_um` came from `cfg.zoomdict` servo positions instead of
  `cfg.pixelsize`

## Root Cause

The PR server handlers assume `core.state` behaves like a plain dictionary and
call `.get(...)`. In the live py312 app, `core.state` is a
`mesoSPIM_StateSingleton`. It supports:

```python
core.state["state"]
core.state["position"]
core.state.get_parameter_dict([...])
core.state.get_parameter_list([...])
```

It does not implement:

```python
core.state.get(...)
```

## Fix Direction

Update `mesoSPIM_RemoteScripting.py` to read state through
`mesoSPIM_StateSingleton`'s actual access API while still tolerating dict-like
state objects used by offline tests. Also update `get_config` to read camera
dimensions from `cfg.camera_parameters` and zoom pixel sizes from
`cfg.pixelsize`.
