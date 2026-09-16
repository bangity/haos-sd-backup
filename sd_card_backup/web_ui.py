#!/usr/bin/env python3
import os
import json
import subprocess
import glob
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

PORT = 8099
OPTIONS_FILE = "/data/options.json"

def get_config():
    if os.path.exists(OPTIONS_FILE):
        try:
            with open(OPTIONS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"target_dir": "/backup", "source_dev": "/dev/mmcblk0"}

class MaterialIngressHandler(BaseHTTPRequestHandler):
    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        ingress_path = self.headers.get("X-Ingress-Path", "").rstrip("/")
        cfg = get_config()
        target_dir = cfg.get("target_dir", "/backup").rstrip("/")

        if path in ["", "/", "/index.html"]:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()

            html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>SD Card Backup & Health</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&family=Google+Sans:wght@400;500;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@24,400,0,0" />
  <style>
    :root {{
      --md-sys-color-primary: #8AB4F8;
      --md-sys-color-on-primary: #002A5A;
      --md-sys-color-surface: #202124;
      --md-sys-color-surface-container: #292A2D;
      --md-sys-color-surface-container-high: #333538;
      --md-sys-color-outline: #5F6368;
      --md-sys-color-outline-variant: #3C4043;
      --md-sys-color-on-surface: #E8EAED;
      --md-sys-color-on-surface-variant: #9AA0A6;
      --google-blue: #4285F4;
      --google-red: #EA4335;
      --google-yellow: #FBBC05;
      --google-green: #34A853;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Roboto', -apple-system, BlinkMacSystemFont, sans-serif;
      background-color: var(--md-sys-color-surface);
      color: var(--md-sys-color-on-surface);
      padding: 2rem 1.5rem;
      display: flex;
      justify-content: center;
    }}
    .app-frame {{ width: 100%; max-width: 960px; display: flex; flex-direction: column; gap: 1.5rem; }}
    header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding-bottom: 1.25rem;
      border-bottom: 1px solid var(--md-sys-color-outline-variant);
    }}
    .brand {{ display: flex; align-items: center; gap: 0.75rem; }}
    .brand h1 {{
      font-family: 'Google Sans', 'Roboto', sans-serif;
      font-size: 1.35rem;
      font-weight: 500;
      letter-spacing: -0.01em;
    }}
    .brand-icon {{
      width: 40px;
      height: 40px;
      border-radius: 10px;
      background: rgba(66, 133, 244, 0.15);
      color: var(--google-blue);
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .status-chip {{
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      padding: 0.35rem 0.85rem;
      border-radius: 16px;
      font-size: 0.82rem;
      font-weight: 500;
      background: var(--md-sys-color-surface-container-high);
      border: 1px solid var(--md-sys-color-outline-variant);
    }}
    .status-chip.active {{ border-color: var(--google-green); color: var(--google-green); }}
    .status-chip.alert {{ border-color: var(--google-yellow); color: var(--google-yellow); }}
    .grid-stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 1rem; }}
    .m3-card {{
      background: var(--md-sys-color-surface-container);
      border-radius: 16px;
      border: 1px solid var(--md-sys-color-outline-variant);
      padding: 1.25rem;
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
      position: relative;
      overflow: hidden;
    }}
    .m3-card-accent {{ position: absolute; top: 0; left: 0; right: 0; height: 3px; }}
    .stat-label {{ font-size: 0.78rem; font-weight: 500; color: var(--md-sys-color-on-surface-variant); text-transform: uppercase; letter-spacing: 0.05em; }}
    .stat-value {{ font-family: 'Google Sans', 'Roboto', sans-serif; font-size: 1.45rem; font-weight: 500; }}
    .actions-shelf {{ display: flex; gap: 0.75rem; flex-wrap: wrap; }}
    .m3-button {{
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      height: 40px;
      padding: 0 1.25rem;
      border-radius: 20px;
      font-family: 'Google Sans', 'Roboto', sans-serif;
      font-size: 0.88rem;
      font-weight: 500;
      border: none;
      cursor: pointer;
      transition: all 0.2s cubic-bezier(0.2, 0, 0, 1);
    }}
    .btn-filled {{ background: var(--google-blue); color: #FFFFFF; }}
    .btn-filled:hover {{ box-shadow: 0 1px 3px rgba(0,0,0,0.3); filter: brightness(1.08); }}
    .btn-tonal {{ background: var(--md-sys-color-surface-container-high); color: var(--md-sys-color-on-surface); border: 1px solid var(--md-sys-color-outline-variant); }}
    .btn-tonal:hover {{ background: var(--md-sys-color-outline-variant); }}
    .progress-bar-container {{
      width: 100%;
      height: 6px;
      background: var(--md-sys-color-surface-container-high);
      border-radius: 3px;
      overflow: hidden;
      display: none;
    }}
    .progress-bar-fill {{
      height: 100%;
      width: 0%;
      background: var(--google-blue);
      border-radius: 3px;
      transition: width 0.3s ease;
    }}
    .data-table {{ width: 100%; border-collapse: collapse; margin-top: 0.5rem; }}
    .data-table th {{
      text-align: left;
      font-size: 0.75rem;
      text-transform: uppercase;
      color: var(--md-sys-color-on-surface-variant);
      padding: 0.75rem 0.5rem;
      border-bottom: 1px solid var(--md-sys-color-outline-variant);
    }}
    .data-table td {{
      padding: 0.75rem 0.5rem;
      font-size: 0.85rem;
      border-bottom: 1px solid var(--md-sys-color-outline-variant);
    }}
    .console-header {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.5rem; }}
    pre.console-box {{
      background: #18191A;
      border: 1px solid var(--md-sys-color-outline-variant);
      border-radius: 12px;
      padding: 1rem;
      font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
      font-size: 0.82rem;
      line-height: 1.45;
      color: #D1D5DB;
      max-height: 380px;
      overflow-y: auto;
      white-space: pre-wrap;
      word-break: break-all;
    }}
    .dialog-overlay {{
      display: none;
      position: fixed;
      top: 0; left: 0; width: 100vw; height: 100vh;
      background: rgba(0, 0, 0, 0.6);
      align-items: center;
      justify-content: center;
      z-index: 1000;
    }}
    .m3-dialog {{
      background: var(--md-sys-color-surface-container-high);
      border-radius: 24px;
      width: 90%;
      max-width: 440px;
      padding: 1.5rem;
      display: flex;
      flex-direction: column;
      gap: 1rem;
      box-shadow: 0 10px 25px rgba(0, 0, 0, 0.5);
      border: 1px solid var(--md-sys-color-outline-variant);
    }}
  </style>
</head>
<body>
  <div class="app-frame">
    <header>
      <div class="brand">
        <div class="brand-icon"><span class="material-symbols-outlined">memory</span></div>
        <div>
          <h1>SD Backup &amp; Storage Health</h1>
          <p style="font-size: 0.8rem; color: var(--md-sys-color-on-surface-variant);">Automated HAOS Hardware Reliability Suite</p>
        </div>
      </div>
      <div class="status-chip" id="chip-daemon">
        <span class="material-symbols-outlined" style="font-size: 16px;">sync</span>
        <span id="daemon-state">Checking...</span>
      </div>
    </header>

    <div class="progress-bar-container" id="global-progress">
      <div class="progress-bar-fill" id="global-progress-fill"></div>
    </div>

    <div class="grid-stats">
      <div class="m3-card">
        <div class="m3-card-accent" style="background: var(--google-blue);"></div>
        <div class="stat-label">Backup Engine State</div>
        <div class="stat-value" id="val-status">--</div>
        <p id="sub-status" style="font-size: 0.8rem; color: var(--md-sys-color-on-surface-variant);">Process Idle</p>
      </div>
      <div class="m3-card">
        <div class="m3-card-accent" style="background: var(--google-yellow);"></div>
        <div class="stat-label">Flash Memory Wear</div>
        <div class="stat-value" id="val-wear" style="color: var(--google-yellow);">--%</div>
        <p id="sub-wear" style="font-size: 0.8rem; color: var(--md-sys-color-on-surface-variant);">Safe Threshold: 80%</p>
      </div>
      <div class="m3-card">
        <div class="m3-card-accent" style="background: var(--google-green);"></div>
        <div class="stat-label">JEDEC Life Health</div>
        <div class="stat-value" id="val-health">--</div>
        <p id="sub-health" style="font-size: 0.8rem; color: var(--md-sys-color-on-surface-variant);">Reserve Blocks Normal</p>
      </div>
    </div>

    <div class="actions-shelf">
      <button class="m3-button btn-filled" onclick="openConfirmationModal()">
        <span class="material-symbols-outlined" style="font-size: 18px;">backup</span> Run Backup Now
      </button>
      <button class="m3-button btn-tonal" onclick="triggerAction('wear')">
        <span class="material-symbols-outlined" style="font-size: 18px;">monitor_heart</span> Refresh Wear Telemetry
      </button>
      <button class="m3-button btn-tonal" onclick="fetchStatus()">
        <span class="material-symbols-outlined" style="font-size: 18px;">refresh</span> Poll State
      </button>
    </div>

    <div class="m3-card">
      <span class="stat-label">Disaster Recovery Packages ({target_dir})</span>
      <table class="data-table">
        <thead>
          <tr>
            <th>Filename</th>
            <th>Size</th>
            <th style="text-align: right;">Action</th>
          </tr>
        </thead>
        <tbody id="table-artifacts">
          <tr><td colspan="3" style="text-align: center; color: var(--md-sys-color-on-surface-variant);">Scanning storage directory...</td></tr>
        </tbody>
      </table>
    </div>

    <div class="m3-card">
      <div class="console-header">
        <span class="stat-label">Live Output Log</span>
        <button class="m3-button btn-tonal" style="height: 28px; padding: 0 0.75rem; font-size: 0.75rem;" onclick="copyConsoleLog()">
          <span class="material-symbols-outlined" style="font-size: 14px;">content_copy</span> Copy Log
        </button>
      </div>
      <pre class="console-box" id="console-output">Loading log stream...</pre>
    </div>
  </div>

  <div class="dialog-overlay" id="confirm-modal">
    <div class="m3-dialog">
      <h2 style="font-family: 'Google Sans', sans-serif; font-size: 1.15rem; font-weight: 500;">Start Live SD Card Backup?</h2>
      <p style="font-size: 0.88rem; color: var(--md-sys-color-on-surface-variant); line-height: 1.4;">
        This triggers a raw block-level image copy of the SD card to your storage directory. SQLite transactions will be flushed and checkpointed before cloning begins.
      </p>
      <div style="display: flex; justify-content: flex-end; gap: 0.5rem; margin-top: 0.5rem;">
        <button class="m3-button btn-tonal" onclick="closeConfirmationModal()">Cancel</button>
        <button class="m3-button btn-filled" onclick="executeConfirmedBackup()">Confirm &amp; Run</button>
      </div>
    </div>
  </div>

  <script>
    const basePath = "{ingress_path}";

    function openConfirmationModal() {{
      document.getElementById('confirm-modal').style.display = 'flex';
    }}

    function closeConfirmationModal() {{
      document.getElementById('confirm-modal').style.display = 'none';
    }}

    function executeConfirmedBackup() {{
      closeConfirmationModal();
      triggerAction('backup');
    }}

    function copyToClipboard(text) {{
      navigator.clipboard.writeText(text).then(() => alert("SHA256 Checksum copied."));
    }}

    function copyConsoleLog() {{
      const text = document.getElementById("console-output").innerText;
      navigator.clipboard.writeText(text).then(() => alert("Console log copied."));
    }}

    async function fetchStatus() {{
      try {{
        const res = await fetch(basePath + "/api/status");
        const data = await res.json();

        document.getElementById("val-status").innerText = data.status;
        document.getElementById("sub-status").innerText = data.status === "Running" ? "Streaming Disk Sectors..." : "Waiting for Next Schedule";
        document.getElementById("val-wear").innerText = data.wear;
        document.getElementById("val-health").innerText = data.health;
        document.getElementById("console-output").innerText = data.log || "No log transactions recorded.";

        const pBar = document.getElementById("global-progress");
        const pFill = document.getElementById("global-progress-fill");
        if (data.status === "Running") {{
          pBar.style.display = "block";
          pFill.style.width = data.progress;
          document.getElementById("chip-daemon").className = "status-chip alert";
          document.getElementById("daemon-state").innerText = "Backup Running (" + data.progress + ")";
        }} else {{
          pBar.style.display = "none";
          document.getElementById("chip-daemon").className = "status-chip active";
          document.getElementById("daemon-state").innerText = "Daemon Active";
        }}

        const tbody = document.getElementById("table-artifacts");
        tbody.innerHTML = "";
        if (data.artifacts && data.artifacts.length > 0) {{
          data.artifacts.forEach(item => {{
            const row = document.createElement("tr");
            let actionHtml = `<a href="${{basePath}}/api/download?file=${{encodeURIComponent(item.name)}}" class="m3-button btn-tonal" style="height: 28px; padding: 0 0.75rem; text-decoration: none; font-size: 0.75rem;">Download</a>`;
            if (item.name.endsWith(".sha256")) {{
              actionHtml += ` <button class="m3-button btn-tonal" style="height: 28px; padding: 0 0.5rem;" onclick="copyToClipboard('${{item.hash || item.name}}')"><span class="material-symbols-outlined" style="font-size: 14px;">content_copy</span></button>`;
            }}
            row.innerHTML = `
              <td style="font-family: monospace;">${{item.name}}</td>
              <td>${{item.size}}</td>
              <td style="text-align: right;">${{actionHtml}}</td>
            `;
            tbody.appendChild(row);
          }});
        }} else {{
          tbody.innerHTML = `<tr><td colspan="3" style="text-align: center; color: var(--md-sys-color-on-surface-variant);">No backup archives found in destination folder.</td></tr>`;
        }}
      }} catch (e) {{
        console.error("Failed to query status", e);
      }}
    }}

    async function triggerAction(endpoint) {{
      try {{
        const res = await fetch(basePath + "/api/" + endpoint, {{ method: "POST" }});
        const resp = await res.json();
        alert(resp.message);
        setTimeout(fetchStatus, 1500);
      }} catch (e) {{
        alert("Action request failed.");
      }}
    }}

    fetchStatus();
    setInterval(fetchStatus, 4000);
  </script>
</body>
</html>"""
            self.wfile.write(html.encode("utf-8"))
            return

        elif path == "/api/status":
            wear_out = "N/A"
            health_out = "Unsupported"
            try:
                cmd = subprocess.run(["/run.sh", "--wear-metrics"], capture_output=True, text=True, timeout=5)
                if cmd.returncode == 0:
                    parts = cmd.stdout.strip().split("|")
                    if len(parts) >= 2:
                        wear_out = parts[0] + ("%" if parts[0] != "N/A" else "")
                        health_out = parts[1]
            except Exception:
                pass

            artifacts = []
            if os.path.exists(target_dir):
                for p in sorted(glob.glob(f"{target_dir}/*"), key=os.path.getmtime, reverse=True):
                    fname = os.path.basename(p)
                    if any(fname.endswith(ext) for ext in [".img.gz", ".sha256", ".log", ".sh", ".ps1"]):
                        try:
                            size = os.path.getsize(p)
                            human_size = f"{size / 1048576:.1f} MB" if size < 1073741824 else f"{size / 1073741824:.2f} GB"
                            hash_val = ""
                            if fname.endswith(".sha256"):
                                with open(p, "r") as hf:
                                    hash_val = hf.read().strip().split()[0]
                            artifacts.append({"name": fname, "size": human_size, "hash": hash_val})
                        except Exception:
                            pass

            latest_log = ""
            log_files = sorted(glob.glob(f"{target_dir}/haos_backup_*.log"), key=os.path.getmtime, reverse=True)
            if log_files and os.path.exists(log_files[0]):
                try:
                    with open(log_files[0], "r", encoding="utf-8", errors="ignore") as f:
                        latest_log = f.read()
                except Exception:
                    latest_log = "Unable to read log stream."

            status_val = "Idle"
            prog_val = "0%"
            if os.path.exists("/var/run/sd_backup.lock"):
                status_val = "Running"
                prog_file = "/var/run/sd_backup.progress"
                if os.path.exists(prog_file):
                    try:
                        with open(prog_file, "r") as pf:
                            prog_val = pf.read().strip()
                    except Exception:
                        pass

            self.send_json({
                "status": status_val,
                "progress": prog_val,
                "wear": wear_out,
                "health": health_out,
                "artifacts": artifacts[:15],
                "log": latest_log[-8000:]
            })
            return

        elif path == "/api/download":
            qs = parse_qs(parsed.query)
            filename = qs.get("file", [""])[0]
            safe_name = os.path.basename(filename)
            full_path = os.path.join(target_dir, safe_name)

            if os.path.exists(full_path) and os.path.isfile(full_path):
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", f'attachment; filename="{safe_name}"')
                self.send_header("Content-Length", str(os.path.getsize(full_path)))
                self.end_headers()
                with open(full_path, "rb") as f:
                    while chunk := f.read(65536):
                        self.wfile.write(chunk)
                return
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"File not found.")
                return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/backup":
            if os.path.exists("/var/run/sd_backup.lock"):
                self.send_json({"message": "A backup task is already actively running."}, status=409)
                return
            subprocess.Popen(["/run.sh", "--backup"])
            self.send_json({"message": "Disk backup process successfully triggered in background."})
            return

        elif path == "/api/wear":
            subprocess.Popen(["/run.sh", "--wear"])
            self.send_json({"message": "Wear diagnostics dispatched to Home Assistant entities."})
            return

        self.send_response(404)
        self.end_headers()

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), MaterialIngressHandler)
    server.serve_forever()