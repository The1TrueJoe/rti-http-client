# rti-http-client

RTI XP-series driver that sends HTTP requests over plain TCP.

## Driver capabilities

- **Send HTTP String** — direct command with a path, URL, or raw HTTP request string as the parameter.
- **Configured HTTP strings** — up to 10 named commands defined entirely in driver settings (no parameter needed during programming).
- **Flexible input formats:**
  - `/path` — uses Default Host, Default Port, Default Method, and Default Headers.
  - `http://host:port/path` — overrides host and port for that command.
  - Raw HTTP request text — sent byte-for-byte (method line, headers, body). Use `\r\n` for CRLF in configuration fields.
- **System events:** `Connected`, `Disconnected`, `Response Received`, `Error`.
- **System variables:** `Connected`, `Last Request`, `Last Response`, `Last Status Code`, `TX Count`, `RX Count`, `Last Error`.

> **Plain HTTP only.** HTTPS/TLS is not supported by the RTI TCP stack. Use a local HTTP-to-HTTPS proxy if TLS is required.

## Build

Only Python 3 (≥ 3.8) is required — no Node.js and no third-party packages:

```sh
python3 scripts/package-driver.py
```

If you have npm handy, the package.json wrapper also works:

```sh
npm run package
```

Output: `dist/Simple HTTP Client.rtidriver`

## Import and configure

1. Build the package (or download from the GitHub Actions artifacts).
2. In RTI Integration Designer 9.6+, open the XP-6/XP-8 processor settings page and click **Add Driver**.
3. Select `Simple HTTP Client.rtidriver`.
4. Set **Default Host** and **Default Port** (required for relative-path and URL-format commands).
5. Set **Number of HTTP strings** and fill each entry's **Name** and **HTTP String**.
6. Use the generated commands on buttons, macros, or events in your project.

## HTTP string formats

| Example input | What is sent |
|---|---|
| `/api/power/on` | `GET /api/power/on HTTP/1.1` to Default Host:Port |
| `http://192.168.1.50/status` | `GET /status HTTP/1.1` to `192.168.1.50:80` |
| `http://192.168.1.50:8080/cmd` | `GET /cmd HTTP/1.1` to `192.168.1.50:8080` |
| `POST /api HTTP/1.1\r\nHost: 192.168.1.50\r\nContent-Length: 2\r\n\r\n{}` | Raw request sent as-is |

Default Headers (optional) are added after `Host:` and `Connection:` in auto-built requests. Separate multiple headers with `\r\n`.

## Testing locally

`test_server.py` runs a plain HTTP/1.1 server that logs every request the driver sends and returns a configurable response:

```sh
python3 test_server.py --port 8080
# Set driver Default Host → your machine's LAN IP
# Set driver Default Port → 8080
```

Options: `--status CODE`, `--body TEXT`, `--response-file PATH`.

## CI / release

`.github/workflows/package.yml` runs on every push to `main` and on pull requests; no Node.js required. The `.rtidriver` file is uploaded as a workflow artifact. Pushes to tags matching `v*` additionally attach it to a GitHub release.

## Project layout

```
driver/
  ConfigSettings.xml           driver configuration settings (host, port, method, HTTP strings)
  DynamicConfigInfo            expression/variable declarations for conditional category display
  DriverManifest.template.xml  driver metadata; __VERSION__/__SCRIPT_SIZE__/__HELP_SIZE__ replaced at build
  SystemEvents.xml             event definitions
  SystemFunctions.xml          command definitions
  SystemVariables.xml          system variable reference docs (not packaged — ID 9.6 uses runtime writes)
  http_client.js               XP processor script
  instructions.rtf             help text stream shown in Integration Designer
scripts/
  package-driver.py            pure-Python OLE/CFB writer; produces the .rtidriver file from driver/
test_server.py                 local HTTP test server for development and testing
```
