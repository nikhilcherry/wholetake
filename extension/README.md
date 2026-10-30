# Wholetake Clip Verifier — Browser Extension (Manifest V3)

The **Wholetake Clip Verifier** (`extension/`) is a cross-browser Manifest V3 extension for **Google Chrome**, **Microsoft Edge**, and **Mozilla Firefox**. On any webpage containing a video/audio element or a link to X (`x.com` / `twitter.com`), YouTube (`youtube.com` / `youtu.be`), Instagram (`instagram.com`), or direct media files, anyone can trigger **"Check this clip with Wholetake"** from the right-click context menu or the toolbar popup.

The extension submits the clip URL to the Wholetake Verdict Service HTTP API (`POST /api/verdict`), polls job progress (`GET /api/verdict/<id>`), and displays:
- **Witness Verdict**: `cut`, `faithful`, `spliced`, or `not_found`
- **What Was Cut**: omitted transcript words and exact clip/source timestamps
- **Roomstamp Verification**: acoustic watermark lock (`present`), `venue`, `recorded_at_utc`, and Layer-1 / Layer-2 scores
- **Proof Link**: one-click link to the full interactive proof and restored-audio page (`/v/<id>`)

---

## 1. Unpacked Installation Instructions

### Google Chrome
1. Open `chrome://extensions` in the Chrome address bar.
2. Enable **Developer mode** using the toggle in the top-right corner.
3. Click **Load unpacked** in the top-left action bar.
4. Select the `extension/` directory inside this repository.
5. *(Optional)* Click the puzzle-piece **Extensions** icon in the Chrome toolbar and pin **Wholetake Clip Verifier**.

### Microsoft Edge
1. Open `edge://extensions` in the Edge address bar.
2. Enable **Developer mode** using the toggle in the left navigation pane.
3. Click **Load unpacked**.
4. Select the `extension/` directory inside this repository.

### Mozilla Firefox
1. Open `about:debugging#/runtime/this-firefox` in the Firefox address bar.
2. Click **Load Temporary Add-on...**.
3. Select `extension/manifest.json` from this repository.
4. Note: `manifest.json` declares both `background.service_worker` (for Chrome/Edge MV3) and `background.scripts` + `browser_specific_settings.gecko` (for Firefox MV3).

---

## 2. Configuring the Verdict Service URL

### Local Verdict Service (Default: `http://127.0.0.1:8790`)
1. From the repository root, start the Verdict Service HTTP server:
   ```bash
   ./py -m verdict serve --port 8790
   ```
2. Click the **Wholetake Clip Verifier** icon in the browser toolbar.
3. Verify that **Verdict Service URL** is set to `http://127.0.0.1:8790` and click **Save**.

### Remote Tunnel (Venue Demo / Remote Judge Laptop)
1. If the Verdict Service is running on another machine or behind an HTTPS tunnel (for example, Cloudflare Tunnel `https://<subdomain>.trycloudflare.com` or ngrok), copy the public tunnel base URL.
2. Open the extension toolbar popup, paste the tunnel URL into **Verdict Service URL**, and click **Save**.
3. The extension normalizes the URL (adds `http://` if omitted and strips trailing slashes) and saves it in `chrome.storage.local`.

---

## 3. Using the Extension

### Option A: Right-Click Context Menu
1. Browse to any post or video on **X (`x.com`)**, **YouTube (`youtube.com`)**, **Instagram (`instagram.com`)**, or any page with `<video>` / `<audio>` elements.
2. Right-click directly on a post link, media player, or page background.
3. Select **"Check this clip with Wholetake"** from the context menu.
4. The background worker extracts the best clip URL candidate (`linkUrl` -> `srcUrl` -> content-script media discovery -> `pageUrl`), submits it to `POST /api/verdict`, polls `GET /api/verdict/<id>`, and persists the result.
5. Click the extension toolbar icon at any time to view live polling progress and the final verdict card.

### Option B: Toolbar Popup
1. On any media or social page, click the **Wholetake Clip Verifier** toolbar icon.
2. The popup queries the active tab via `content.js` to auto-fill any discovered `<video>`, `<audio>`, or X/YouTube/Instagram clip URL (or you can paste a clip URL manually).
3. Click **Check Clip**.
4. Watch the status progress (`queued` -> `fetching` -> `detecting` -> `labeling` -> `done`) and inspect the verdict badge, cut words, Roomstamp status, and **Open Full Proof Page (`/v/<id>`)** link.

---

## 4. Troubleshooting (Offline, CORS, and SSRF Errors)

### Offline / Unreachable Server (`offline: true`)
- **Symptom**: The popup reports that the Verdict Service is unreachable (e.g., `ECONNREFUSED` or network fetch failure).
- **Cause**: `./py -m verdict serve --port 8790` is not running, the port does not match, or the remote tunnel is offline.
- **Fix**: Start the local server (`./py -m verdict serve --port 8790`), verify `http://127.0.0.1:8790/api/status` responds, and check the **Verdict Service URL** in the popup.

### CORS & MV3 Host Permissions (`POST /api/verdict` has `cors=False`)
- **Why this happens**: In `verdict/service.py`, `POST /api/verdict` intentionally sends responses with `cors=False` (no `Access-Control-Allow-Origin` header) so malicious third-party webpages cannot silently CSRF-submit jobs to a user's local Verdict Service. Meanwhile, `GET /api/verdict/<id>` sends `cors=True` (`Access-Control-Allow-Origin: *`).
- **How the extension works**: The extension declares `host_permissions: ["http://*/*", "https://*/*"]` in `manifest.json` and performs API calls from privileged extension contexts (`background.js` / `popup.js`), bypassing webpage CORS restrictions.
- **Fix if CORS errors occur**: Ensure the extension has **Site access -> On all sites** enabled in `chrome://extensions` / `edge://extensions` / `about:addons`, and never call `POST /api/verdict` from an unprivileged webpage script.

### HTTP 400 SSRF Rejection (`SSRFError`)
- **Symptom**: Submitting a clip URL fails immediately with `HTTP 400` and an SSRF / private address error message.
- **Cause**: `verdict/service.py` enforces `validate_public_url()` on `POST /api/verdict` and rejects loopback (`127.0.0.1`, `localhost`, `::1`), private RFC1918 (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), link-local/metadata (`169.254.169.254`), and non-HTTP(S) (`file://`, `ftp://`) URLs to prevent Server-Side Request Forgery. Requests with bodies over 65,536 bytes also return `HTTP 400`.
- **Fix**: Submit a public `http://` or `https://` clip URL, or upload local audio/video files directly via the Verdict Dashboard at `http://127.0.0.1:8790/dashboard` (`POST /api/verdict/upload`).

### HTTP 404 Unknown Job (`verdict job not found`)
- **Symptom**: Polling `GET /api/verdict/<id>` returns `HTTP 404` (`{"error": "verdict job not found"}`).
- **Cause**: The job ID does not exist on the configured Verdict Service (for example, after switching the service URL or clearing `data/verdicts/`).
- **Fix**: Submit the clip URL again using **Check Clip**.

### Job `status: "error"`
- **Symptom**: Polling finishes with `status: "error"` and displays a pipeline failure message.
- **Cause**: Downloading the media (`fetch`) or running the detector (`$WT_DETECT`) failed (for example, a webpage URL with no downloadable audio stream or a clip outside the 2 s – 600 s duration bounds).
- **Fix**: Pass a direct video/audio post URL or upload the media file directly at `http://127.0.0.1:8790/dashboard`.

---

## 5. Security & Zero-Secret Guarantee

- **No Secrets in Extension**: `extension/` contains zero API keys, tokens, or Roomstamp venue private keys. All cryptographic key material (`venue.key`), witness logs, and DSP verification remain strictly on the Verdict Service host.
- **Run Verification Suite**:
  ```bash
  ./py extension/test_extension.py
  ```
