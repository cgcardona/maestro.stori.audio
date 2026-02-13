# Front-end implementation prompt: MCP DAW WebSocket

Use this as a **single comprehensive prompt** for a Swift (or other front-end) agent to implement the Stori ↔ Composer MCP WebSocket connection so that Cursor (or any MCP client) can control the DAW in real time.

---

## Objective

Implement the **DAW WebSocket connection** from the Stori app to the Composer backend so that:

1. When a user (or Cursor) calls an MCP tool that targets the DAW (e.g. `stori_read_project`, `stori_set_track_color`, `stori_play`), Composer forwards that call to the connected Stori instance over the WebSocket.
2. The Stori app receives the tool call, executes it in the DAW, and sends back a **tool_response** with the result.
3. Optionally, the app sends **project_state** so that `stori_read_project` can return current project/tracks/regions without a round-trip (and so Cursor can refer to “the first track”, etc.).

Until this is implemented, any DAW tool called from Cursor will return: **"No DAW connected. Please open Stori and connect."**

---

## 1. WebSocket URL and authentication

- **URL:**  
  `wss://<host>/api/v1/mcp/daw?token=<jwt>`  
  - `<host>`: your Composer API host (e.g. `stage.stori.audio`, `composer.stori.audio`, or `localhost` if using local nginx with `NGINX_CONF_DIR=conf.d-local`).
  - **No path beyond** `/api/v1/mcp/daw`; only the `token` query parameter.
- **Auth:** The **JWT (access token)** must be passed **only** as the query parameter `token`. The backend validates it **before** accepting the WebSocket (HTTP 4001 / close if missing or invalid). Do **not** use `Authorization` header for this WebSocket; the backend only reads `?token=`.
- **Token source:** Same JWT the app uses for REST (e.g. from access code flow). Generate with:  
  `docker compose exec composer python scripts/generate_access_code.py --generate-user-id --days 7`  
  (and register the user if needed for compose/budget). See [integrate.md](integrate.md) for full token and registration flow.

**Example (pseudo):**

```swift
let baseURL = "wss://stage.stori.audio"  // or your host
let path = "/api/v1/mcp/daw"
let url = URL(string: "\(baseURL)\(path)?token=\(accessToken)")!
// Open WebSocket to url
```

---

## 2. Connection lifecycle

1. **Connect** to `wss://<host>/api/v1/mcp/daw?token=<jwt>`.
2. Backend **validates the JWT**; if invalid, it **closes** with code `4001` (no upgrade). The client never sees 101; treat as unauthorized.
3. If valid, backend **accepts** the connection and **registers** this client as the active DAW, then sends one **`connected`** message. The app should treat receipt of that as "connected". Only one DAW is “active” at a time per server; a new connection replaces the previous one.
4. **Keep the connection open.** While open, the backend will send **tool_call** messages; the app must respond with **tool_response** for each.
5. On **disconnect**, the backend unregisters the DAW. Cursor will then get “No DAW connected” for any DAW tool until the app reconnects.

---

## 3. Messages FROM backend TO app (you receive)

All messages are **JSON objects**. The backend sends these types:

### `connected` (first message after handshake)

Right after the WebSocket is accepted, the backend sends one message so the app can mark "connected":

```json
{ "type": "connected", "connection_id": "<opaque id>" }
```

Treat receipt of this as a successful connection. No response required.

### `tool_call`

The backend sends this when an MCP client (e.g. Cursor) invokes a DAW tool.

**Shape:**

```json
{
  "type": "tool_call",
  "request_id": "<string, unique per call>",
  "tool": "<tool name, e.g. stori_read_project | stori_set_track_color | stori_play | ...>",
  "arguments": { ... }
}
```

- **`request_id`:** Opaque string. You **must** include this exact value in the corresponding **tool_response** so the backend can match the response to the call. If you don’t respond, the MCP client will see “DAW did not respond in time” after ~30 seconds.
- **`tool`:** One of the DAW tool names (see [api.md](api.md)). Examples: `stori_read_project`, `stori_set_track_color`, `stori_set_track_icon`, `stori_play`, `stori_stop`, `stori_set_tempo`, `stori_add_track`, etc.
- **`arguments`:** Object with parameters for that tool (e.g. `{ "trackId": "abc", "color": "blue" }` for `stori_set_track_color`).

**You must:** Execute the tool in the DAW (or, for `stori_read_project`, return cached project state if you have it), then send **exactly one** **tool_response** per **tool_call** with the same `request_id`.

---

## 4. Messages FROM app TO backend (you send)

Send **JSON objects** (e.g. via your WebSocket “send JSON” API). The backend accepts these message types:

### 4.1 `tool_response` (required for every tool_call)

**Shape:**

```json
{
  "type": "tool_response",
  "request_id": "<same as in tool_call>",
  "result": {
    "success": true,
    ...optional extra fields...
  }
}
```

- **`request_id`:** Must match the `request_id` from the **tool_call** you are answering.
- **`result`:** Must be an object. The backend forwards this to the MCP client. It **must** include **`success`** (boolean):
  - `true`: tool ran successfully; optional fields (e.g. `message`, or project summary for `stori_read_project`) can be included.
  - `false`: tool failed; you can put e.g. `"error": "reason"` in `result` for the user to see.

**Example (success):**

```json
{
  "type": "tool_response",
  "request_id": "stori_set_track_color_140234",
  "result": { "success": true }
}
```

**Example (failure):**

```json
{
  "type": "tool_response",
  "request_id": "stori_set_track_color_140234",
  "result": { "success": false, "error": "Track not found" }
}
```

### 4.2 `project_state` (optional but recommended)

So that **stori_read_project** can return immediately (and so Cursor can refer to “first track”, etc.), the app can push the current project state to the backend. The backend caches it per connection and uses it when it receives a **tool_call** for `stori_read_project` (and when formatting the response).

**Shape:**

```json
{
  "type": "project_state",
  "state": {
    "name": "My Project",
    "tempo": 120,
    "keySignature": "C",
    "timeSignature": { "numerator": 4, "denominator": 4 },
    "tracks": [
      {
        "id": "<track-uuid>",
        "name": "Drums",
        "drumKitId": "TR-808",
        "gmProgram": 0,
        "mixerSettings": {
          "volume": 0.8,
          "pan": 0.5,
          "isMuted": false
        },
        "midiRegions": [
          {
            "id": "<region-uuid>",
            "name": "Region 1",
            "startTime": 0,
            "duration": 16,
            "notes": []
          }
        ]
      }
    ]
  }
}
```

- **When to send:** On project open, and whenever the project or track list (or key metadata) changes. You can throttle (e.g. debounce) to avoid flooding.
- **`state.tracks[].midiRegions`:** For **stori_read_project** with `include_notes: false` (default), the backend only needs `id`, `name`, `startTime`, `duration`, and can omit or truncate `notes`. If the MCP client calls with `include_notes: true`, the backend will include `notes` from the cached state if present.

### 4.3 `ping` (optional)

**Shape:** `{ "type": "ping" }`  
**Backend response:** Sends `{ "type": "pong" }`. Use for keepalive if needed.

---

## 5. Tools to implement first (minimum for testing)

Implement handlers in the app for at least these DAW tools so you can test from Cursor:

| Tool | Action in DAW | Arguments (typical) | Result to send |
|------|----------------|---------------------|-----------------|
| **stori_read_project** | Return current project (or use cached state) | `include_notes`, `include_automation` (optional) | `result: { success: true, ... }` with project summary; or rely on backend to use your **project_state** cache and return formatted state. |
| **stori_set_track_color** | Set track color | `trackId`, `color` (e.g. red, blue, green) | `result: { success: true }` or `{ success: false, error: "..." }` |
| **stori_set_track_icon** | Set track icon (SF Symbol name) | `trackId`, `icon` (e.g. pianokeys, guitars, music.note) | `result: { success: true }` or `{ success: false, error: "..." }` |
| **stori_play** | Start playback | (none) | `result: { success: true }` |
| **stori_stop** | Stop playback | (none) | `result: { success: true }` |

Full list of DAW tools and parameters: [api.md](api.md). Parameter names and types (e.g. `volumeDb`, `pan` -100..100, `trackId`) are in that doc and in `app/mcp/tools.py`.

---

## 6. Backend behavior (for your reference)

- The backend **waits up to 30 seconds** for a **tool_response** per **tool_call**. If none arrives, it returns to the MCP client: “DAW did not respond in time.”
- For **stori_read_project** only: if the app has sent **project_state** before, the backend can answer immediately from cache (no WebSocket round-trip). Otherwise it forwards **tool_call** to the app; you should respond with **tool_response** with a `result` that includes the project summary (or send **project_state** and let the backend format it on the next call).
- Only **one** DAW connection is active; the latest connection wins.

---

## 7. Testing checklist

**Before WebSocket (current state):**  
In Cursor, “Call stori_read_project” → **“No DAW connected. Please open Stori and connect.”**

**After implementation:**

1. **App:** Store a valid JWT (access token). Connect to `wss://<host>/api/v1/mcp/daw?token=<jwt>`. Show “Connected to Composer” (or similar) when the WebSocket is accepted.
2. **App (optional):** On project load / change, send **project_state** so the backend has current tracks/regions.
3. **Cursor:** In a Composer chat, say **“Call stori_read_project.”**  
   - Expected: You get a JSON project summary (tempo, key, tracks, regions). If you didn’t send **project_state**, the app should still receive a **tool_call** for `stori_read_project` and respond with **tool_response** containing the project data.
4. **Cursor:** “Set the first track’s color to blue” (or “Call stori_set_track_color for trackId X with color blue”).  
   - Expected: DAW updates the track color; Cursor gets `success: true`.
5. **Cursor:** “Call stori_play” then “Call stori_stop.”  
   - Expected: Playback starts and stops; both return success.

If any of these fail, check: token valid and in query string, WebSocket accepted, every **tool_call** answered with **tool_response** with the same `request_id` and `result.success`.

---

## 8. Summary: what to build

1. **WebSocket client** in the Stori app: connect to `wss://<host>/api/v1/mcp/daw?token=<jwt>` with the app’s access token.
2. **Message loop:** On receiving a JSON message with `type: "tool_call"`, dispatch by `tool` to your DAW logic, then send one **tool_response** with the same `request_id` and a `result` object that includes `success`.
3. **Handlers** for at least: `stori_read_project`, `stori_set_track_color`, `stori_set_track_icon`, `stori_play`, `stori_stop`.
4. **Optional:** Send **project_state** when the project or tracks change so `stori_read_project` and Cursor have up-to-date context.
5. **Optional:** Handle `ping` / `pong` for keepalive.

After this, you can run the tests above from Cursor and wire more DAW tools as needed using [api.md](api.md).
