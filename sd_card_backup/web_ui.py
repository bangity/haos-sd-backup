#!/usr/bin/env python3
import os
import json
import subprocess
import glob
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

PORT = 8099
OPTIONS_FILE = "/data/options.json"

DEFAULT_CONFIG = {
    "source_dev": "auto",
    "target_dir": "/backup",
    "retention_count": 3,
    "enable_rescue": False,
    "run_backup_on_start": False,
    "safe_wear_threshold": 80,
    "backup_cron": "0 3 * * 0",
    "wear_cron": "0 12 * * 1",
    "rclone_sync_enabled": False,
    "rclone_remote_target": "",
    "smtp_enabled": False,
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_user": "",
    "smtp_pass": "",
    "smtp_to": ""
}

def get_config():
    if os.path.exists(OPTIONS_FILE):
        try:
            with open(OPTIONS_FILE, "r") as f:
                data = json.load(f)
                cfg = DEFAULT_CONFIG.copy()
                cfg.update(data)
                return cfg
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()

def save_config(new_opts):
    cfg = get_config()
    cfg.update(new_opts)
    os.makedirs(os.path.dirname(OPTIONS_FILE), exist_ok=True)
    with open(OPTIONS_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
    return cfg

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
  <title>HAOS Hardware Recovery Suite</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&family=Google+Sans:wght@400;500;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@24,400,0,0" />
  <style>
    :root {{
      --md-sys-color-primary: #8AB4F8;
      --md-sys-color-on-primary: #002A5A;
      --md-sys-color-surface: #1E1F22;
      --md-sys-color-surface-container: #2B2D31;
      --md-sys-color-surface-container-high: #383A40;
      --md-sys-color-outline: #4E5058;
      --md-sys-color-outline-variant: #3F4147;
      --md-sys-color-on-surface: #F2F3F5;
      --md-sys-color-on-surface-variant: #B5BAC1;
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
      padding: 1.5rem;
      display: flex;
      justify-content: center;
    }}
    .app-frame {{ width: 100%; max-width: 1020px; display: flex; flex-direction: column; gap: 1.5rem; }}

    /* --- UNIQUE CODE-GENERATED MATERIAL HEADER --- */
    .hero-banner {{
      position: relative;
      background: linear-gradient(135deg, #1A237E 0%, #0D47A1 45%, #1565C0 100%);
      border-radius: 24px;
      padding: 2rem;
      overflow: hidden;
      display: flex;
      justify-content: space-between;
      align-items: center;
      border: 1px solid rgba(255, 255, 255, 0.15);
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.35);
    }}
    .hero-svg-bg {{
      position: absolute;
      top: 0; right: 0; bottom: 0; left: 0;
      pointer-events: none;
      opacity: 0.18;
    }}
    .hero-content {{ position: relative; z-index: 2; }}
    .hero-title {{
      font-family: 'Google Sans', sans-serif;
      font-size: 1.75rem;
      font-weight: 700;
      letter-spacing: -0.02em;
      color: #FFFFFF;
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }}
    .hero-subtitle {{
      margin-top: 0.35rem;
      font-size: 0.95rem;
      color: #E3F2FD;
      opacity: 0.9;
    }}
    .hero-badges {{ display: flex; gap: 0.6rem; margin-top: 1rem; }}
    .hero-chip {{
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      padding: 0.3rem 0.75rem;
      border-radius: 12px;
      font-size: 0.78rem;
      font-weight: 500;
      background: rgba(255, 255, 255, 0.18);
      backdrop-filter: blur(8px);
      color: #FFFFFF;
      border: 1px solid rgba(255, 255, 255, 0.25);
    }}

    .grid-stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; }}
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
    .stat-value {{ font-family: 'Google Sans', 'Roboto', sans-serif; font-size: 1.5rem; font-weight: 500; }}

    .actions-shelf {{ display: flex; gap: 0.75rem; flex-wrap: wrap; }}
    .m3-button {{
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      height: 42px;
      padding: 0 1.25rem;
      border-radius: 21px;
      font-family: 'Google Sans', 'Roboto', sans-serif;
      font-size: 0.9rem;
      font-weight: 500;
      border: none;
      cursor: pointer;
      transition: all 0.2s cubic-bezier(0.2, 0, 0, 1);
    }}
    .btn-filled {{ background: var(--google-blue); color: #FFFFFF; }}
    .btn-filled:hover {{ filter: brightness(1.1); box-shadow: 0 2px 8px rgba(66, 133, 244, 0.4); }}
    .btn-tonal {{ background: var(--md-sys-color-surface-container-high); color: var(--md-sys-color-on-surface); border: 1px solid var(--md-sys-color-outline-variant); }}
    .btn-tonal:hover {{ background: var(--md-sys-color-outline); }}
    .btn-config {{ background: linear-gradient(135deg, #34A853 0%, #1E8E3E 100%); color: #FFFFFF; }}
    .btn-config:hover {{ filter: brightness(1.1); box-shadow: 0 2px 8px rgba(52, 168, 83, 0.4); }}

    .progress-bar-container {{
      width: 100%;
      height: 8px;
      background: var(--md-sys-color-surface-container-high);
      border-radius: 4px;
      overflow: hidden;
      display: none;
    }}
    .progress-bar-fill {{
      height: 100%;
      width: 0%;
      background: linear-gradient(90deg, var(--google-blue), #8AB4F8);
      border-radius: 4px;
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
    pre.console-box {{
      background: #141517;
      border: 1px solid var(--md-sys-color-outline-variant);
      border-radius: 12px;
      padding: 1rem;
      font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
      font-size: 0.82rem;
      line-height: 1.45;
      color: #D1D5DB;
      max-height: 340px;
      overflow-y: auto;
      white-space: pre-wrap;
      word-break: break-all;
    }}

    /* --- MATERIAL MODAL DIALOGS --- */
    .dialog-overlay {{
      display: none;
      position: fixed;
      top: 0; left: 0; width: 100vw; height: 100vh;
      background: rgba(0, 0, 0, 0.72);
      backdrop-filter: blur(4px);
      align-items: center;
      justify-content: center;
      z-index: 1000;
    }}
    .m3-dialog {{
      background: var(--md-sys-color-surface-container);
      border-radius: 28px;
      width: 92%;
      max-width: 680px;
      max-height: 88vh;
      display: flex;
      flex-direction: column;
      box-shadow: 0 16px 48px rgba(0, 0, 0, 0.6);
      border: 1px solid var(--md-sys-color-outline-variant);
      overflow: hidden;
    }}
    .dialog-header {{
      padding: 1.5rem;
      border-bottom: 1px solid var(--md-sys-color-outline-variant);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}
    .dialog-body {{
      padding: 1.5rem;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 1.25rem;
    }}
    .dialog-footer {{
      padding: 1rem 1.5rem;
      border-top: 1px solid var(--md-sys-color-outline-variant);
      display: flex;
      justify-content: flex-end;
      gap: 0.75rem;
      background: var(--md-sys-color-surface-container-high);
    }}
    .form-group {{ display: flex; flex-direction: column; gap: 0.4rem; }}
    .form-label {{ font-size: 0.88rem; font-weight: 500; color: var(--md-sys-color-on-surface); }}
    .form-desc {{ font-size: 0.78rem; color: var(--md-sys-color-on-surface-variant); line-height: 1.35; }}
    .form-input, .form-select {{
      background: #1E1F22;
      border: 1px solid var(--md-sys-color-outline);
      color: #FFFFFF;
      padding: 0.65rem 0.85rem;
      border-radius: 8px;
      font-size: 0.9rem;
      outline: none;
    }}
    .form-input:focus, .form-select:focus {{
      border-color: var(--google-blue);
      box-shadow: 0 0 0 2px rgba(66, 133, 244, 0.25);
    }}
    .guide-box {{
      background: rgba(66, 133, 244, 0.08);
      border-left: 4px solid var(--google-blue);
      padding: 1rem;
      border-radius: 0 8px 8px 0;
      font-size: 0.84rem;
      line-height: 1.45;
      color: #D1E3FF;
    }}
    .code-pill {{
      background: #141517;
      padding: 0.2rem 0.4rem;
      border-radius: 4px;
      font-family: monospace;
      color: #8AB4F8;
    }}
  </style>
</head>
<body>
  <div class="app-frame">

    <!-- UNIQUE CODE-GENERATED SVG HERO HEADER -->
    <div class="hero-banner">
      <svg class="hero-svg-bg" viewBox="0 0 800 300" xmlns="http://www.w3.org/2000/svg">
        <path d="M0,100 Q200,50 400,120 T800,80 L800,300 L0,300 Z" fill="#FFFFFF" />
        <circle cx="680" cy="80" r="140" fill="none" stroke="#FFFFFF" stroke-width="2" stroke-dasharray="8 8" />
        <circle cx="680" cy="80" r="90" fill="none" stroke="#FFFFFF" stroke-width="1.5" />
        <path d="M600,140 L760,140 M680,60 L680,220" stroke="#FFFFFF" stroke-width="1.5" />
      </svg>

      <div class="hero-content">
        <div class="hero-title">
          <span class="material-symbols-outlined" style="font-size: 32px;">hard_drive_2</span>
          HAOS Master Backup &amp; Health Suite
        </div>
        <div class="hero-subtitle">Production sector-cloning, JEDEC flash diagnostics, and zero-downtime disaster recovery</div>
        <div class="hero-badges">
          <span class="hero-chip"><span class="material-symbols-outlined" style="font-size: 15px;">shield</span> Raw Block Access</span>
          <span class="hero-chip"><span class="material-symbols-outlined" style="font-size: 15px;">compress</span> Multi-Thread pigz</span>
          <span class="hero-chip"><span class="material-symbols-outlined" style="font-size: 15px;">cloud_sync</span> 3-2-1 Rclone Ready</span>
        </div>
      </div>

      <div style="z-index: 2;">
        <button class="m3-button btn-config" onclick="openConfigModal()">
          <span class="material-symbols-outlined">settings</span> Configure Settings
        </button>
      </div>
    </div>

    <!-- PROGRESS INDICATOR -->
    <div class="progress-bar-container" id="global-progress">
      <div class="progress-bar-fill" id="global-progress-fill"></div>
    </div>

    <!-- LIVE METRICS -->
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
        <p id="sub-wear" style="font-size: 0.8rem; color: var(--md-sys-color-on-surface-variant);">Safe Threshold: {cfg['safe_wear_threshold']}%</p>
      </div>
      <div class="m3-card">
        <div class="m3-card-accent" style="background: var(--google-green);"></div>
        <div class="stat-label">JEDEC Life Health</div>
        <div class="stat-value" id="val-health">--</div>
        <p id="sub-health" style="font-size: 0.8rem; color: var(--md-sys-color-on-surface-variant);">Reserve Blocks Normal</p>
      </div>
    </div>

    <!-- ACTION BUTTONS -->
    <div class="actions-shelf">
      <button class="m3-button btn-filled" onclick="openConfirmationModal()">
        <span class="material-symbols-outlined">play_arrow</span> Run Backup Now
      </button>
      <button class="m3-button btn-tonal" onclick="triggerAction('wear')">
        <span class="material-symbols-outlined">monitor_heart</span> Refresh Wear Telemetry
      </button>
      <button class="m3-button btn-tonal" onclick="openRecoveryGuideModal()">
        <span class="material-symbols-outlined">menu_book</span> How to Restore (Disaster Recovery)
      </button>
      <button class="m3-button btn-tonal" onclick="fetchStatus()">
        <span class="material-symbols-outlined">refresh</span> Refresh Status
      </button>
    </div>

    <!-- STORAGE ARTIFACTS TABLE -->
    <div class="m3-card">
      <span class="stat-label">Disaster Recovery Packages ({target_dir})</span>
      <table class="data-table">
        <thead>
          <tr>
            <th>Artifact Filename</th>
            <th>Size</th>
            <th style="text-align: right;">Action</th>
          </tr>
        </thead>
        <tbody id="table-artifacts">
          <tr><td colspan="3" style="text-align: center; color: var(--md-sys-color-on-surface-variant);">Scanning storage directory...</td></tr>
        </tbody>
      </table>
    </div>

    <!-- CONSOLE LOG -->
    <div class="m3-card">
      <div style="display: flex; align-items: center; justify-content: space-between;">
        <span class="stat-label">Execution Console Log</span>
        <button class="m3-button btn-tonal" style="height: 28px; padding: 0 0.75rem; font-size: 0.75rem;" onclick="copyConsoleLog()">
          <span class="material-symbols-outlined" style="font-size: 14px;">content_copy</span> Copy Log
        </button>
      </div>
      <pre class="console-box" id="console-output">Loading log stream...</pre>
    </div>

  </div>

  <!-- MODAL: INTERACTIVE SETTINGS POPUP -->
  <div class="dialog-overlay" id="config-modal">
    <div class="m3-dialog">
      <div class="dialog-header">
        <h2 style="font-family: 'Google Sans', sans-serif; font-size: 1.25rem;">Suite Configuration</h2>
        <span class="material-symbols-outlined" style="cursor: pointer;" onclick="closeConfigModal()">close</span>
      </div>
      <div class="dialog-body">
        
        <div class="form-group">
          <label class="form-label">Source Storage Drive</label>
          <select class="form-select" id="cfg-source-dev">
            <option value="auto">auto - Auto-Detect Boot Drive (Recommended)</option>
          </select>
          <p class="form-desc">Select the physical root block device to clone. Set to <b>auto</b> to automatically resolve /dev/mmcblk0 (SD), /dev/sda (SSD), or /dev/nvme0n1 (NVMe).</p>
        </div>

        <div class="form-group">
          <label class="form-label">Target Storage Directory (SMB / Local)</label>
          <div style="display: flex; gap: 0.5rem;">
            <input type="text" class="form-input" style="flex: 1;" id="cfg-target-dir" value="{cfg['target_dir']}">
            <button class="m3-button btn-tonal" style="height: 38px;" onclick="probeTargetDir()">Probe Access</button>
          </div>
          <p class="form-desc">Typically <b>/backup</b>. Ensure your network storage (OMV/TrueNAS) is mounted under Settings > System > Storage.</p>
        </div>

        <div class="form-group">
          <label class="form-label">Backup Retention Count</label>
          <input type="number" class="form-input" id="cfg-retention" min="1" max="50" value="{cfg['retention_count']}">
          <p class="form-desc">Maximum number of complete backup sets to keep before automatically pruning older copies.</p>
        </div>

        <div class="form-group">
          <label class="form-label">Automated Backup Schedule Preset</label>
          <select class="form-select" id="cfg-sched-preset" onchange="applySchedulePreset(this.value)">
            <option value="0 3 * * 0">Weekly - Every Sunday at 3:00 AM (Recommended)</option>
            <option value="0 3 * * *">Daily - Every night at 3:00 AM</option>
            <option value="0 3 1 * *">Monthly - 1st of every month at 3:00 AM</option>
            <option value="custom">Custom Cron Expression</option>
          </select>
          <input type="text" class="form-input" id="cfg-backup-cron" value="{cfg['backup_cron']}" style="margin-top: 0.4rem;">
          <p class="form-desc">5-part cron syntax (Minute Hour Day-of-Month Month Day-of-Week).</p>
        </div>

        <div class="form-group">
          <label class="form-label">Rclone 3-2-1 Cloud Offsite Replication</label>
          <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.4rem;">
            <input type="checkbox" id="cfg-rclone-enabled" {"checked" if cfg['rclone_sync_enabled'] else ""}>
            <label for="cfg-rclone-enabled" style="font-size: 0.9rem;">Enable secondary cloud sync</label>
          </div>
          <input type="text" class="form-input" id="cfg-rclone-target" placeholder="e.g., b2:haos-bucket/backups or gdrive:Backups" value="{cfg['rclone_remote_target']}">
          <p class="form-desc">To use this feature, place your <span class="code-pill">rclone.conf</span> in your Home Assistant <span class="code-pill">/config/rclone/</span> directory.</p>
        </div>

      </div>
      <div class="dialog-footer">
        <button class="m3-button btn-tonal" onclick="closeConfigModal()">Cancel</button>
        <button class="m3-button btn-filled" onclick="saveSettings()">Save &amp; Apply Changes</button>
      </div>
    </div>
  </div>

  <!-- MODAL: DISASTER RECOVERY GUIDE -->
  <div class="dialog-overlay" id="recovery-guide-modal">
    <div class="m3-dialog">
      <div class="dialog-header">
        <h2 style="font-family: 'Google Sans', sans-serif; font-size: 1.25rem;">Disaster Recovery Guide</h2>
        <span class="material-symbols-outlined" style="cursor: pointer;" onclick="closeRecoveryGuideModal()">close</span>
      </div>
      <div class="dialog-body">
        <div class="guide-box">
          <b>How Restoration Works:</b> Every backup cycle generates companion self-executing restore scripts (<span class="code-pill">restore.sh</span> and <span class="code-pill">restore.ps1</span>). They check hash integrity, flash raw sectors, relocate backup GPT structures to the end of the drive, and auto-expand Partition 8 (<span class="code-pill">hassos-data</span>) to 100% of the replacement drive's capacity.
        </div>

        <h3 style="font-size: 1rem; color: var(--google-blue); margin-top: 0.5rem;">Restoring on Linux (or OpenMediaVault)</h3>
        <p class="form-desc">Insert your replacement SD card or SSD into your Linux host or OMV server and run:</p>
        <pre class="console-box" style="padding: 0.75rem;">sudo ./restore_YYYY-MM-DD_HH-MM-SS.sh /dev/sdX</pre>
        <p class="form-desc">The script prompts for confirmation, verifies SHA256 integrity, streams sectors with multi-core decompression, and automatically executes <span class="code-pill">resize2fs</span> on Partition 8.</p>

        <h3 style="font-size: 1rem; color: var(--google-yellow); margin-top: 0.5rem;">Restoring on Windows (PowerShell)</h3>
        <p class="form-desc">Open Windows Terminal as Administrator, navigate to the backup folder on your SMB share, and run:</p>
        <pre class="console-box" style="padding: 0.75rem;">Set-ExecutionPolicy Bypass -Scope Process
.\restore_YYYY-MM-DD_HH-MM-SS.ps1</pre>
        <p class="form-desc">Select your target USB disk number from the detected list. The script strips Windows drive letter locks, flashes the disk, and confirms completion.</p>
      </div>
      <div class="dialog-footer">
        <button class="m3-button btn-filled" onclick="closeRecoveryGuideModal()">Got It</button>
      </div>
    </div>
  </div>

  <!-- MODAL: CONFIRMATION PROMPT -->
  <div class="dialog-overlay" id="confirm-modal">
    <div class="m3-dialog" style="max-width: 440px;">
      <div class="dialog-header">
        <h2 style="font-family: 'Google Sans', sans-serif; font-size: 1.15rem;">Start Live Disk Backup?</h2>
        <span class="material-symbols-outlined" style="cursor: pointer;" onclick="closeConfirmationModal()">close</span>
      </div>
      <div class="dialog-body">
        <p style="font-size: 0.88rem; color: var(--md-sys-color-on-surface-variant); line-height: 1.45;">
          This initiates an on-demand, live sector clone to your storage folder. SQLite transactions will be flushed to disk via WAL checkpointing before reading raw sectors.
        </p>
      </div>
      <div class="dialog-footer">
        <button class="m3-button btn-tonal" onclick="closeConfirmationModal()">Cancel</button>
        <button class="m3-button btn-filled" onclick="executeConfirmedBackup()">Confirm &amp; Run</button>
      </div>
    </div>
  </div>

  <script>
    const basePath = "{ingress_path}";

    function openConfigModal() {{
      fetchDisks();
      document.getElementById('config-modal').style.display = 'flex';
    }}
    function closeConfigModal() {{ document.getElementById('config-modal').style.display = 'none'; }}

    function openRecoveryGuideModal() {{ document.getElementById('recovery-guide-modal').style.display = 'flex'; }}
    function closeRecoveryGuideModal() {{ document.getElementById('recovery-guide-modal').style.display = 'none'; }}

    function openConfirmationModal() {{ document.getElementById('confirm-modal').style.display = 'flex'; }}
    function closeConfirmationModal() {{ document.getElementById('confirm-modal').style.display = 'none'; }}

    function executeConfirmedBackup() {{
      closeConfirmationModal();
      triggerAction('backup');
    }}

    function applySchedulePreset(val) {{
      if (val !== 'custom') {{
        document.getElementById('cfg-backup-cron').value = val;
      }}
    }}

    async function fetchDisks() {{
      try {{
        const res = await fetch(basePath + "/api/system/disks");
        const data = await res.json();
        const select = document.getElementById("cfg-source-dev");
        select.innerHTML = '<option value="auto">auto - Auto-Detect Boot Drive (Recommended)</option>';
        data.disks.forEach(d => {{
          const opt = document.createElement("option");
          opt.value = d.path;
          opt.innerText = `${{d.path}} (${{d.size}} - ${{d.model || d.tran || 'Disk'}})` ;
          select.appendChild(opt);
        }});
        select.value = "{cfg['source_dev']}";
      }} catch (e) {{
        console.warn("Could not load hardware disks", e);
      }}
    }}

    async function probeTargetDir() {{
      const dir = document.getElementById("cfg-target-dir").value;
      try {{
        const res = await fetch(basePath + "/api/system/probe?dir=" + encodeURIComponent(dir));
        const data = await res.json();
        alert(data.message);
      }} catch (e) {{
        alert("Directory probe failed.");
      }}
    }}

    async function saveSettings() {{
      const payload = {{
        source_dev: document.getElementById("cfg-source-dev").value,
        target_dir: document.getElementById("cfg-target-dir").value,
        retention_count: parseInt(document.getElementById("cfg-retention").value, 10),
        backup_cron: document.getElementById("cfg-backup-cron").value,
        rclone_sync_enabled: document.getElementById("cfg-rclone-enabled").checked,
        rclone_remote_target: document.getElementById("cfg-rclone-target").value
      }};

      try {{
        const res = await fetch(basePath + "/api/config", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify(payload)
        }});
        const data = await res.json();
        alert(data.message);
        closeConfigModal();
        setTimeout(() => location.reload(), 1200);
      }} catch (e) {{
        alert("Failed to save configuration.");
      }}
    }}

    function copyToClipboard(text) {{
      navigator.clipboard.writeText(text).then(() => alert("SHA256 Hash copied to clipboard."));
    }}

    function copyConsoleLog() {{
      const text = document.getElementById("console-output").innerText;
      navigator.clipboard.writeText(text).then(() => alert("Console log copied to clipboard."));
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
        }} else {{
          pBar.style.display = "none";
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

        elif path == "/api/system/disks":
            disks = []
            try:
                cmd = subprocess.run(["lsblk", "-d", "-b", "-n", "-o", "NAME,SIZE,TYPE,MODEL,TRAN"], capture_output=True, text=True)
                for line in cmd.stdout.strip().split("\n"):
                    parts = line.split()
                    if len(parts) >= 3 and parts[2] == "disk":
                        d_name = parts[0]
                        bytes_sz = int(parts[1]) if parts[1].isdigit() else 0
                        human_sz = f"{bytes_sz / 1073741824:.1f} GB"
                        model = parts[3] if len(parts) > 3 else ""
                        tran = parts[4] if len(parts) > 4 else ""
                        disks.append({
                            "path": f"/dev/{d_name}",
                            "size": human_sz,
                            "model": model,
                            "tran": tran
                        })
            except Exception:
                pass
            self.send_json({"disks": disks})
            return

        elif path == "/api/system/probe":
            qs = parse_qs(parsed.query)
            target = qs.get("dir", ["/backup"])[0].rstrip("/")
            if not os.path.exists(target):
                self.send_json({"message": f"Path '{target}' does not exist on host filesystem."}, status=404)
                return
            probe_file = os.path.join(target, ".probe_test")
            try:
                with open(probe_file, "w") as f:
                    f.write("ok")
                os.remove(probe_file)
                self.send_json({"message": f"✔ Success! Directory '{target}' is reachable and writable."})
            except Exception as ex:
                self.send_json({"message": f"✖ Write failed on '{target}': {str(ex)}"}, status=500)
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

        if path == "/api/config":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")
                new_opts = json.loads(body)
                save_config(new_opts)
                # Inform daemon to update crontab
                subprocess.Popen(["/run.sh", "--update-cron"])
                self.send_json({"message": "✔ Configuration successfully saved and crontab updated."})
            except Exception as ex:
                self.send_json({"message": f"Failed to save settings: {str(ex)}"}, status=500)
            return

        elif path == "/api/backup":
            if os.path.exists("/var/run/sd_backup.lock"):
                self.send_json({"message": "A backup task is already actively executing."}, status=409)
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