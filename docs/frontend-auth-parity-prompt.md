# Front-end implementation prompt: Auth & identity parity with Composer

Use this as a **single comprehensive prompt** for a Swift (or other front-end) agent to implement authentication and identity so the app matches the Stori Composer backend’s **single-identifier architecture**. The backend expects one identity everywhere: the **device UUID**.

---

## Objective

Implement auth and identity in the Stori app so that:

1. **One identifier everywhere:** The app uses a single UUID (device UUID) for registration, for the JWT’s subject, and for asset requests. The backend uses this same value as the user id in the DB and in the JWT.
2. **Registration** creates or updates the user on the backend using that device UUID.
3. **Access code (JWT)** is validated and stored; the backend issues tokens whose `sub` is the device UUID when using the recommended flow (see backend docs).
4. **Composer, MCP, validate-token, users/me, conversations** use the JWT only (Bearer). No device UUID is sent on these endpoints.
5. **Assets** (drum kits, soundfonts, download URLs) use **only** `X-Device-ID: <device-uuid>` (no JWT).

Until this is implemented correctly, budget, usage, and asset rate limits may not align with a single user identity.

---

## 1. Device UUID (single identifier)

- **What:** A single UUID that identifies this app install. It is the **only** user/device identity the backend uses for you.
- **Where to create it:** Generate once per install, e.g. `UUID().uuidString`, and store it in **UserDefaults** (or equivalent) so it persists across launches and is the same for the lifetime of the install.
- **Where to use it:**
  - In the body of `POST /api/v1/users/register` as `user_id`.
  - In the header `X-Device-ID` on **every** request to asset endpoints (drum kits, soundfonts, download URLs). Do **not** send the JWT on asset requests.
  - The backend will issue JWTs whose `sub` claim equals this same UUID when using the documented flow (support/issues an access code with `--user-id <device_uuid>` after you register). So after the user enters an access code, the token’s `sub` and your device UUID are the same.

**Rule:** Use one UUID. Send it as `user_id` at register and as `X-Device-ID` on assets. Use the JWT (whose `sub` is that UUID in the app flow) for everything else.

---

## 2. First run / registration

1. **Ensure you have a device UUID** (create and store in UserDefaults if not already present).
2. **Register with the backend** so the user exists and has a default budget:
   - `POST /api/v1/users/register`
   - Body: `{ "user_id": "<device-uuid>" }`
   - **No JWT required** for this request.
   - If the user already exists (e.g. reinstall with same UUID), the backend returns 200 with their info; otherwise it creates the user.

Do this on first launch (or when you don’t yet have a valid token), so the backend has a user row for this device UUID before anyone issues an access code for it.

---

## 3. Access code (JWT) flow

1. **User enters an access code** (e.g. pasted from support or from the script). The code is a JWT string.
2. **Validate it** with the backend: `GET /api/v1/validate-token` with `Authorization: Bearer <token>`. If the response is 200 and `valid: true`, the token is good.
3. **Store the JWT securely** (e.g. Keychain). Use it for all authenticated requests except asset requests.
4. **Optional:** Decode the JWT and verify that `sub` equals your device UUID (for single-identifier parity). If your backend is set up as in [integrate.md](integrate.md), tokens issued for this app will have `sub` = device UUID.

The backend does **not** require the JWT on `POST /api/v1/users/register` or on asset endpoints. It **does** require the JWT on: compose, MCP (HTTP and WebSocket), validate-token, users/me, conversations, variation.

---

## 4. Per-request behavior (parity checklist)

| Request type | Send JWT? | Send X-Device-ID? | Notes |
|--------------|-----------|--------------------|------|
| `GET /api/v1/health` | No | No | No auth. |
| `POST /api/v1/users/register` | No | No | Body: `{ "user_id": "<device-uuid>" }`. |
| `GET /api/v1/validate-token` | Yes (Bearer) | No | Validate token before storing. |
| `POST /api/v1/compose/stream` | Yes (Bearer) | No | Composer uses JWT only. |
| `GET /api/v1/users/me` | Yes (Bearer) | No | Profile and budget. |
| Conversations (list/create/update/…) | Yes (Bearer) | No | JWT only. |
| MCP HTTP (`/api/v1/mcp/tools`, etc.) | Yes (Bearer) | No | JWT only. |
| MCP WebSocket (`/api/v1/mcp/daw?token=<jwt>`) | Token in query only | No | No Bearer header; token in URL. |
| **Assets:** drum-kits, soundfonts, download-url | **No** | **Yes** | `X-Device-ID: <device-uuid>`. No JWT. |

**Common mistake:** Sending the JWT on asset endpoints. The backend expects **only** `X-Device-ID` for assets so the app can access kits/soundfonts without touching Keychain. Use the same device UUID you used at register.

---

## 5. Summary for the Swift agent

- **Single identifier:** One UUID (device UUID), generated once per install, stored in UserDefaults.
- **Register:** `POST /api/v1/users/register` with `{ "user_id": "<device-uuid>" }` (no JWT). Call on first run or when “user” not yet registered.
- **Access code:** User enters JWT → validate with `GET /api/v1/validate-token` (Bearer) → store in Keychain. Use that JWT for compose, MCP, users/me, conversations (Bearer). Do **not** send JWT on asset requests.
- **Assets:** Every asset request must send `X-Device-ID: <device-uuid>` and **must not** send the JWT. Use the same device UUID as in register.
- **Parity:** Backend and app both use the same UUID as the user id (JWT `sub` = device UUID when tokens are issued with `--user-id <device_uuid>` after register). One identity everywhere.

Backend reference: [integrate.md](integrate.md) (single identifier, access codes, asset endpoints). MCP WebSocket details: [frontend-mcp-websocket-prompt.md](frontend-mcp-websocket-prompt.md).
