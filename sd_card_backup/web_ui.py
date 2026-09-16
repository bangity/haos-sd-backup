#!/usr/bin/env python3
import os
import sys
import json
import subprocess
import glob
import shutil
import urllib.request
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
    "smtp_host": "",
    "smtp_port": 587,
    "smtp_user": "",
    "smtp_pass": "",
    "smtp_to": ""
}

def get_config():
    cfg = DEFAULT_CONFIG.copy()
    if os.path.exists(OPTIONS_FILE):
        try:
            with open(OPTIONS_FILE, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg

def save_config(new_opts):
    cfg = get_config()
    cfg.update(new_opts)
    os.makedirs(os.path.dirname(OPTIONS_FILE), exist_ok=True)
    with open(OPTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

    token = os.environ.get("SUPERVISOR_TOKEN")
    if token:
        try:
            req = urllib.request.Request(
                "http://supervisor/addons/self/options",
                data=json.dumps({"options": cfg}).encode("utf-8"),
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=3):
                pass
        except Exception:
            pass
    return cfg

def get_mount_stats(target_path):
    """Calculates disk capacity, checking bridged network paths."""
    candidates = [target_path]
    # If target is /backup, check bridged host paths
    if target_path in ["/backup", "/backup/"]:
        candidates.extend([
            "/mnt/network_storage/Pi4HomeAssistant",
            "/proc/1/root/mnt/data/supervisor/mounts/Pi4HomeAssistant"
        ])

    for p in candidates:
        try:
            if os.path.exists(p):
                total, used, free = shutil.disk_usage(p)
                # If path has > 40 GB, it is the external SMB volume
                if total > 40 * 1073741824:
                    pct_used = int((used / total) * 100) if total > 0 else 0
                    return total, used, free, pct_used
        except Exception:
            pass

    try:
        if os.path.exists(target_path):
            total, used, free = shutil.disk_usage(target_path)
            pct_used = int((used / total) * 100) if total > 0 else 0
            return total, used, free, pct_used
    except Exception:
        pass
    return 0, 0, 0, 0

class WebDashboardHandler(BaseHTTPRequestHandler):
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
  <title>HAOS SD Backup &amp; Storage Suite</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&family=Google+Sans:wght@400;500;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@24,400,0,0" />
  <style>
    :root {{
      --md-primary: #8AB4F8;
      --md-surface: #121316;
      --md-surface-container: #1E1F24;
      --md-surface-container-high: #2B2D33;
      --md-surface-container-highest: #373A42;
      --md-outline: #52555E;
      --md-outline-variant: #3F424A;
      --md-on-surface: #E3E4E8;
      --md-on-surface-variant: #9DA1AA;
      --google-blue: #4285F4;
      --google-red: #EA4335;
      --google-yellow: #FBBC05;
      --google-green: #34A853;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Roboto', -apple-system, BlinkMacSystemFont, sans-serif;
      background-color: var(--md-surface);
      color: var(--md-on-surface);
      padding: 1.5rem;
      display: flex;
      justify-content: center;
      line-height: 1.5;
    }}
    .app-frame {{ width: 100%; max-width: 1080px; display: flex; flex-direction: column; gap: 1.5rem; }}

    .hero-banner {{
      position: relative;
      background: linear-gradient(135deg, #0D214F 0%, #153E7E 50%, #1A56A6 100%);
      border-radius: 24px;
      padding: 2rem 2.25rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
      border: 1px solid rgba(255, 255, 255, 0.14);
      box-shadow: 0 12px 32px rgba(0, 0, 0, 0.45);
    }}
    .hero-title {{
      font-family: 'Google Sans', sans-serif;
      font-size: 1.75rem;
      font-weight: 700;
      color: #FFFFFF;
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }}
    .hero-subtitle {{ margin-top: 0.35rem; font-size: 0.92rem; color: #D2E3FC; }}

    .nav-tabs {{
      display: flex;
      gap: 0.5rem;
      border-bottom: 2px solid var(--md-outline-variant);
      padding-bottom: 0.5rem;
    }}
    .nav-tab {{
      background: transparent;
      border: none;
      color: var(--md-on-surface-variant);
      padding: 0.65rem 1.25rem;
      font-size: 0.95rem;
      font-weight: 500;
      border-radius: 12px;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 0.5rem;
      transition: all 0.2s;
    }}
    .nav-tab:hover {{ background: var(--md-surface-container); color: var(--md-on-surface); }}
    .nav-tab.active {{ background: var(--md-surface-container-high); color: var(--md-primary); font-weight: 700; }}

    .m3-card {{
      background: var(--md-surface-container);
      border-radius: 20px;
      border: 1px solid var(--md-outline-variant);
      padding: 1.5rem;
      display: flex;
      flex-direction: column;
      gap: 1rem;
      box-shadow: 0 4px 16px rgba(0,0,0,0.25);
    }}
    .card-title {{
      font-family: 'Google Sans', sans-serif;
      font-size: 1.15rem;
      font-weight: 600;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }}

    .m3-button {{
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      height: 42px;
      padding: 0 1.25rem;
      border-radius: 21px;
      font-family: 'Google Sans', sans-serif;
      font-size: 0.9rem;
      font-weight: 500;
      border: none;
      cursor: pointer;
      transition: all 0.2s;
    }}
    .btn-filled {{ background: var(--google-blue); color: #FFFFFF; }}
    .btn-filled:hover {{ filter: brightness(1.12); box-shadow: 0 4px 12px rgba(66, 133, 244, 0.4); }}
    .btn-tonal {{ background: var(--md-surface-container-high); color: var(--md-on-surface); border: 1px solid var(--md-outline-variant); }}
    .btn-tonal:hover {{ background: var(--md-surface-container-highest); }}

    .progress-bar-container {{
      width: 100%;
      height: 10px;
      background: var(--md-surface-container-high);
      border-radius: 5px;
      overflow: hidden;
      margin-top: 0.5rem;
      display: none;
    }}
    .progress-bar-fill {{
      height: 100%;
      width: 0%;
      background: linear-gradient(90deg, var(--google-blue), #8AB4F8);
      border-radius: 5px;
      transition: width 0.3s;
    }}

    .selection-grid {{ display: grid; grid-template-columns: 1fr; gap: 0.75rem; margin-top: 0.5rem; }}
    .selectable-card {{
      background: #15161A;
      border: 2px solid var(--md-outline-variant);
      border-radius: 14px;
      padding: 1.15rem 1.35rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
      cursor: pointer;
      transition: border-color 0.2s, background 0.2s;
    }}
    .selectable-card:hover {{ border-color: var(--md-primary); background: #1B1D23; }}
    .selectable-card.selected {{
      border-color: var(--google-blue);
      background: rgba(66, 133, 244, 0.12);
    }}

    .badge {{
      display: inline-block;
      padding: 0.2rem 0.6rem;
      border-radius: 8px;
      font-size: 0.75rem;
      font-weight: 600;
      background: rgba(255, 255, 255, 0.1);
      color: var(--md-primary);
    }}
    .badge-cifs {{ background: rgba(52, 168, 83, 0.2); color: var(--google-green); }}
    .badge-usb {{ background: rgba(251, 188, 5, 0.2); color: var(--google-yellow); }}

    .meter-bar {{
      width: 100%;
      height: 8px;
      background: var(--md-surface-container-highest);
      border-radius: 4px;
      margin-top: 0.6rem;
      overflow: hidden;
    }}
    .meter-bar-fill {{
      height: 100%;
      background: var(--google-green);
      border-radius: 4px;
    }}

    .form-input, .form-select {{
      background: #15161A;
      border: 1px solid var(--md-outline);
      color: #FFFFFF;
      padding: 0.75rem 1rem;
      border-radius: 10px;
      font-size: 0.92rem;
      outline: none;
    }}
    .form-input:focus, .form-select:focus {{
      border-color: var(--md-primary);
      box-shadow: 0 0 0 2px rgba(138, 180, 248, 0.25);
    }}

    .filter-chip-group {{ display: flex; gap: 0.4rem; flex-wrap: wrap; margin-top: 0.3rem; }}
    .filter-chip {{
      padding: 0.4rem 0.9rem;
      border-radius: 16px;
      background: #15161A;
      border: 1px solid var(--md-outline-variant);
      color: var(--md-on-surface-variant);
      font-size: 0.82rem;
      font-weight: 500;
      cursor: pointer;
      user-select: none;
    }}
    .filter-chip.active {{
      background: var(--google-blue);
      border-color: var(--md-primary);
      color: #FFFFFF;
    }}

    .data-table {{ width: 100%; border-collapse: collapse; }}
    .data-table th {{
      text-align: left;
      font-size: 0.75rem;
      text-transform: uppercase;
      color: var(--md-on-surface-variant);
      padding: 0.75rem 0.5rem;
      border-bottom: 1px solid var(--md-outline-variant);
    }}
    .data-table td {{
      padding: 0.75rem 0.5rem;
      font-size: 0.85rem;
      border-bottom: 1px solid var(--md-outline-variant);
    }}
    pre.console-box {{
      background: #0E0F12;
      border: 1px solid var(--md-outline-variant);
      border-radius: 12px;
      padding: 1rem;
      font-family: monospace;
      font-size: 0.82rem;
      line-height: 1.45;
      color: #D1D5DB;
      max-height: 380px;
      overflow-y: auto;
      white-space: pre-wrap;
    }}
    .guide-box {{
      background: rgba(66, 133, 244, 0.08);
      border-left: 4px solid var(--google-blue);
      padding: 1rem 1.25rem;
      border-radius: 0 10px 10px 0;
      font-size: 0.86rem;
      line-height: 1.5;
      color: #D2E3FC;
    }}
    .toast {{
      position: fixed;
      bottom: 24px;
      left: 50%;
      transform: translateX(-50%);
      background: #2D2F36;
      color: #FFFFFF;
      padding: 0.8rem 1.6rem;
      border-radius: 24px;
      font-size: 0.9rem;
      display: none;
      z-index: 2000;
      box-shadow: 0 8px 24px rgba(0,0,0,0.5);
    }}
  </style>
</head>
<body>
  <div class="app-frame">

    <div class="hero-banner">
      <div>
        <div class="hero-title">
          <span class="material-symbols-outlined" style="font-size: 32px; color: #8AB4F8;">sd_storage</span>
          HAOS SD Backup &amp; Storage Health
        </div>
        <div class="hero-subtitle">Production sector-level disk replication, flash wear analytics, and disaster recovery</div>
      </div>
      <span style="font-size: 0.85rem; background: rgba(255,255,255,0.15); padding: 0.4rem 0.8rem; border-radius: 12px;">v1.7.0</span>
    </div>

    <!-- MAIN APP TABS -->
    <div class="nav-tabs">
      <button class="nav-tab active" id="tab-btn-backups" onclick="switchMainTab('backups')">
        <span class="material-symbols-outlined">backup</span> Backups &amp; Execution
      </button>
      <button class="nav-tab" id="tab-btn-config" onclick="switchMainTab('config')">
        <span class="material-symbols-outlined">tune</span> Storage &amp; Schedule Config
      </button>
      <button class="nav-tab" id="tab-btn-health" onclick="switchMainTab('health')">
        <span class="material-symbols-outlined">monitor_heart</span> Wear Health &amp; Logs
      </button>
    </div>

    <!-- TAB 1: BACKUPS & MANUAL SNAPSHOT -->
    <div id="tab-content-backups" style="display: flex; flex-direction: column; gap: 1.5rem;">
      <div class="m3-card">
        <div class="card-title">
          <span class="material-symbols-outlined" style="color: var(--google-blue);">play_circle</span>
          On-Demand Full Disk Backup
        </div>
        <p style="font-size: 0.9rem; color: var(--md-on-surface-variant);">
          Triggers a live raw block clone of your boot drive directly to <b id="display-target-dir">{target_dir}</b>. SQLite write-ahead transactions are checkpointed and trimmed prior to streaming.
        </p>

        <div style="display: flex; gap: 0.75rem; align-items: center; margin-top: 0.25rem;">
          <button class="m3-button btn-filled" id="btn-run-backup" onclick="triggerAction('backup')">
            <span class="material-symbols-outlined">play_arrow</span> Start Backup Now
          </button>
          <span id="backup-status-text" style="font-size: 0.9rem; font-weight: 500;">Status: Idle</span>
        </div>

        <div class="progress-bar-container" id="global-progress">
          <div class="progress-bar-fill" id="global-progress-fill"></div>
        </div>
      </div>

      <div class="m3-card">
        <div class="card-title">
          <span class="material-symbols-outlined" style="color: var(--google-green);">folder_zip</span>
          Available Backup Packages &amp; Recovery Scripts
        </div>
        <table class="data-table">
          <thead>
            <tr>
              <th>Artifact Filename</th>
              <th>Size</th>
              <th style="text-align: right;">Actions</th>
            </tr>
          </thead>
          <tbody id="table-artifacts">
            <tr><td colspan="3" style="text-align: center; color: var(--md-on-surface-variant);">Scanning target storage...</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- TAB 2: STORAGE & SCHEDULE CONFIG -->
    <div id="tab-content-config" style="display: none; flex-direction: column; gap: 1.5rem;">
      
      <!-- DETECTED HOST STORAGE TARGETS (SMB & USB) -->
      <div class="m3-card">
        <div class="card-title">
          <span class="material-symbols-outlined" style="color: var(--google-green);">cloud_done</span>
          Detected Host Storage Targets (SMB Shares &amp; USB Drives)
        </div>
        <p style="font-size: 0.88rem; color: var(--md-on-surface-variant);">
          Remote SMB shares and mounted USB drives discovered on Home Assistant OS. Click on any card to select it as your backup destination:
        </p>
        <div class="selection-grid" id="smb-mounts-list">
          <div style="color: var(--md-on-surface-variant); font-size: 0.85rem;">Scanning storage mounts...</div>
        </div>
      </div>

      <!-- TARGET DIRECTORY WITH DROPDOWN PICKER -->
      <div class="m3-card">
        <div class="card-title">
          <span class="material-symbols-outlined" style="color: var(--google-blue);">folder</span>
          Target Storage Destination
        </div>
        <p style="font-size: 0.88rem; color: var(--md-on-surface-variant);">
          Choose from detected storage targets or specify a custom local folder/subpath:
        </p>

        <div style="display: flex; gap: 0.75rem; flex-wrap: wrap;">
          <select class="form-select" id="storage-quick-select" style="flex: 1; min-width: 260px;" onchange="applyQuickStorageSelect(this.value)">
            <option value="">-- Choose from Detected Storage / SMB / USB --</option>
          </select>
        </div>

        <div style="display: flex; gap: 0.75rem; margin-top: 0.25rem;">
          <input type="text" class="form-input" style="flex: 1;" id="cfg-target-dir" value="{cfg['target_dir']}">
          <button class="m3-button btn-tonal" onclick="probeDirectory()">Test Writable Access</button>
        </div>
        <div id="probe-msg" style="font-size: 0.85rem; display: none;"></div>
      </div>

      <!-- SOURCE DRIVE DETECTION -->
      <div class="m3-card">
        <div class="card-title">
          <span class="material-symbols-outlined" style="color: var(--google-blue);">hard_drive_2</span>
          Source Storage Drive Selection
        </div>
        <p style="font-size: 0.88rem; color: var(--md-on-surface-variant);">
          Select which disk device is cloned. We automatically inspect hardware bus topologies connected to the host:
        </p>
        <div class="selection-grid" id="drive-list"></div>
        <input type="hidden" id="cfg-source-dev" value="{cfg['source_dev']}">
      </div>

      <!-- RCLONE 3-2-1 CLOUD REPLICATION -->
      <div class="m3-card">
        <div class="card-title">
          <span class="material-symbols-outlined" style="color: var(--google-yellow);">cloud_sync</span>
          Rclone 3-2-1 Offsite Cloud Replication
        </div>
        <div class="guide-box">
          <b>Rclone Cloud Mirroring:</b> Automatically syncs completed archives to Backblaze B2, Google Drive, OneDrive, or AWS S3. Place your existing <code style="color: #FFFFFF;">rclone.conf</code> into <code style="color: #FFFFFF;">/config/rclone/rclone.conf</code>.
        </div>

        <div style="display: flex; align-items: center; gap: 0.65rem; margin-top: 0.25rem;">
          <input type="checkbox" id="cfg-rclone-enabled" {"checked" if cfg['rclone_sync_enabled'] else ""} style="width: 18px; height: 18px;">
          <label for="cfg-rclone-enabled" style="font-size: 0.95rem; font-weight: 500;">Enable automated offsite cloud sync</label>
        </div>

        <div style="display: flex; flex-direction: column; gap: 0.4rem; margin-top: 0.5rem;">
          <label style="font-size: 0.85rem; font-weight: 500;">Rclone Remote Target Path</label>
          <input type="text" class="form-input" id="cfg-rclone-target" placeholder="e.g. b2:my-haos-bucket/backups or gdrive:HAOS_Backups" value="{cfg['rclone_remote_target']}">
          <span style="font-size: 0.8rem; color: var(--md-on-surface-variant);">Format: <code style="color: var(--md-primary);">RemoteName:Path/To/Folder</code></span>
        </div>
      </div>

      <!-- SCHEDULE & RETENTION -->
      <div class="m3-card">
        <div class="card-title">
          <span class="material-symbols-outlined" style="color: var(--google-blue);">calendar_month</span>
          Automated Schedule &amp; Retention Policy
        </div>

        <div style="display: flex; gap: 1rem; flex-wrap: wrap;">
          <div style="flex: 1; min-width: 220px;">
            <label style="font-size: 0.85rem; font-weight: 500;">Recurrence</label>
            <select class="form-select" style="width: 100%; margin-top: 0.35rem;" id="cfg-sched-freq" onchange="handleFrequencyChange(this.value)">
              <option value="weekly">Weekly on Selected Days</option>
              <option value="daily">Daily at Fixed Time</option>
              <option value="monthly">Monthly (1st Day of Month)</option>
              <option value="custom">Advanced (Custom Cron Syntax)</option>
            </select>
          </div>

          <div id="time-picker-block" style="display: flex; gap: 0.5rem; align-items: flex-end;">
            <div>
              <label style="font-size: 0.85rem; font-weight: 500;">Run Time (24h Clock)</label>
              <div style="display: flex; gap: 0.35rem; margin-top: 0.35rem;">
                <select class="form-select" id="cfg-sched-hour"></select>
                <select class="form-select" id="cfg-sched-min">
                  <option value="00">00</option>
                  <option value="15">15</option>
                  <option value="30">30</option>
                  <option value="45">45</option>
                </select>
              </div>
            </div>
          </div>
        </div>

        <div id="days-chip-block" style="margin-top: 0.5rem;">
          <label style="font-size: 0.85rem; font-weight: 500;">Days of Week</label>
          <div class="filter-chip-group">
            <div class="filter-chip" data-day="0" onclick="toggleChip(this)">Sun</div>
            <div class="filter-chip" data-day="1" onclick="toggleChip(this)">Mon</div>
            <div class="filter-chip" data-day="2" onclick="toggleChip(this)">Tue</div>
            <div class="filter-chip" data-day="3" onclick="toggleChip(this)">Wed</div>
            <div class="filter-chip" data-day="4" onclick="toggleChip(this)">Thu</div>
            <div class="filter-chip" data-day="5" onclick="toggleChip(this)">Fri</div>
            <div class="filter-chip" data-day="6" onclick="toggleChip(this)">Sat</div>
          </div>
        </div>

        <div id="custom-cron-block" style="display: none; margin-top: 0.5rem;">
          <label style="font-size: 0.85rem; font-weight: 500;">Cron String</label>
          <input type="text" class="form-input" style="width: 100%; margin-top: 0.35rem;" id="cfg-backup-cron" value="{cfg['backup_cron']}">
        </div>

        <div style="margin-top: 0.5rem;">
          <label style="font-size: 0.85rem; font-weight: 500;">Retention Count (Keep Last N Backups)</label>
          <div style="display: flex; align-items: center; gap: 1rem; margin-top: 0.35rem;">
            <input type="range" id="cfg-retention" min="1" max="15" value="{cfg['retention_count']}" style="flex: 1;" oninput="document.getElementById('ret-val').innerText = this.value">
            <span id="ret-val" style="font-size: 1.25rem; font-weight: bold; min-width: 30px;">{cfg['retention_count']}</span>
          </div>
        </div>

        <div style="display: flex; justify-content: flex-end; margin-top: 1rem;">
          <button class="m3-button btn-filled" onclick="saveConfiguration()">
            <span class="material-symbols-outlined">save</span> Save Configuration
          </button>
        </div>
      </div>
    </div>

    <!-- TAB 3: HEALTH & LOGS -->
    <div id="tab-content-health" style="display: none; flex-direction: column; gap: 1.5rem;">
      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem;">
        <div class="m3-card">
          <span style="font-size: 0.8rem; text-transform: uppercase; color: var(--md-on-surface-variant);">Flash Memory Wear</span>
          <div style="font-size: 1.7rem; font-weight: bold; color: var(--google-yellow);" id="val-wear">--</div>
          <span style="font-size: 0.8rem; color: var(--md-on-surface-variant);">Warning Limit: {cfg['safe_wear_threshold']}%</span>
        </div>
        <div class="m3-card">
          <span style="font-size: 0.8rem; text-transform: uppercase; color: var(--md-on-surface-variant);">Hardware Diagnostics</span>
          <div style="font-size: 1.05rem; font-weight: 600; color: var(--google-green);" id="val-health">--</div>
          <span style="font-size: 0.8rem; color: var(--md-on-surface-variant);">Real-Time Storage Telemetry</span>
        </div>
      </div>

      <div class="m3-card">
        <div style="display: flex; align-items: center; justify-content: space-between;">
          <div class="card-title">
            <span class="material-symbols-outlined" style="color: var(--google-blue);">terminal</span>
            Execution Log Stream
          </div>
          <button class="m3-button btn-tonal" style="height: 32px; font-size: 0.8rem;" onclick="copyConsoleLog()">
            <span class="material-symbols-outlined" style="font-size: 16px;">content_copy</span> Copy Log
          </button>
        </div>
        <pre class="console-box" id="console-output">Loading log stream...</pre>
      </div>
    </div>

  </div>

  <div class="toast" id="toast-msg"></div>

  <script>
    const basePath = "{ingress_path}";

    function showToast(msg) {{
      const t = document.getElementById("toast-msg");
      t.innerText = msg;
      t.style.display = "block";
      setTimeout(() => {{ t.style.display = "none"; }}, 3500);
    }}

    function switchMainTab(tab) {{
      ['backups', 'config', 'health'].forEach(t => {{
        document.getElementById('tab-btn-' + t).className = (t === tab ? 'nav-tab active' : 'nav-tab');
        document.getElementById('tab-content-' + t).style.display = (t === tab ? 'flex' : 'none');
      }});
      if (tab === 'config') {{
        fetchStorageTargets();
        fetchDisks();
        initSchedulePickers();
      }}
    }}

    async function fetchStorageTargets() {{
      const container = document.getElementById("smb-mounts-list");
      const dropdown = document.getElementById("storage-quick-select");
      try {{
        const res = await fetch(basePath + "/api/system/storage-targets");
        const data = await res.json();
        const curTarget = document.getElementById("cfg-target-dir").value.trim();

        dropdown.innerHTML = '<option value="">-- Choose from Detected Storage / SMB / USB --</option>';

        if (!data.targets || data.targets.length === 0) {{
          container.innerHTML = `
            <div class="selectable-card" style="cursor: default;">
              <div>
                <div style="font-weight: 500;">No External Shares or USB Drives Detected</div>
                <div style="font-size: 0.8rem; color: var(--md-on-surface-variant); margin-top: 0.2rem;">
                  Using local /backup directory. Attach USB or configure Settings &gt; System &gt; Storage.
                </div>
              </div>
              <span class="material-symbols-outlined" style="color: var(--md-outline);">info</span>
            </div>
          `;
          return;
        }}

        let html = "";
        data.targets.forEach(m => {{
          const isSelected = (curTarget === m.path);

          const opt = document.createElement("option");
          opt.value = m.path;
          opt.innerText = `${{m.name}} (${{m.type}}) - ${{m.free}}`;
          dropdown.appendChild(opt);

          let badgeClass = "badge-cifs";
          if (m.type.includes("USB")) badgeClass = "badge-usb";

          html += `
            <div class="selectable-card ${{isSelected ? 'selected' : ''}}" onclick="setTargetDir('${{m.path}}')">
              <div style="flex: 1; margin-right: 1rem;">
                <div style="display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap;">
                  <span style="font-weight: 700; font-size: 1.05rem;">${{m.name}}</span>
                  <span class="badge ${{badgeClass}}">${{m.type}}</span>
                  ${{m.is_default ? '<span class="badge" style="background: rgba(66, 133, 244, 0.2); color: var(--google-blue);">Default</span>' : ''}}
                </div>
                <div style="margin-top: 0.35rem; font-family: monospace; font-size: 0.92rem; color: #8AB4F8;">
                  Full Remote Path: <b>${{m.source}}</b>
                </div>
                <div style="margin-top: 0.25rem; font-size: 0.82rem; color: var(--md-on-surface-variant);">
                  Mount Folder: <code style="color: #FFFFFF; background: #0E0F12; padding: 0.15rem 0.4rem; border-radius: 4px;">${{m.path}}</code> &bull; ${{m.free}}
                </div>
                <div class="meter-bar">
                  <div class="meter-bar-fill" style="width: ${{m.pct_used}}%;"></div>
                </div>
              </div>
              <span class="material-symbols-outlined" style="font-size: 30px; color: ${{isSelected ? 'var(--google-green)' : 'var(--md-primary)'}};">
                ${{isSelected ? 'check_circle' : 'cloud_sync'}}
              </span>
            </div>
          `;
        }});
        container.innerHTML = html;
      }} catch (e) {{
        container.innerHTML = `<div style="color: var(--google-red); font-size: 0.85rem;">Failed to query storage mounts.</div>`;
      }}
    }}

    function applyQuickStorageSelect(val) {{
      if (val) {{
        setTargetDir(val);
      }}
    }}

    function setTargetDir(path) {{
      document.getElementById("cfg-target-dir").value = path;
      document.getElementById("display-target-dir").innerText = path;
      fetchStorageTargets();
      probeDirectory();
      showToast("Selected backup path: " + path);
    }}

    async function fetchDisks() {{
      try {{
        const res = await fetch(basePath + "/api/system/disks");
        const data = await res.json();
        const container = document.getElementById("drive-list");
        const cur = document.getElementById("cfg-source-dev").value;

        let html = `
          <div class="selectable-card ${{cur === 'auto' ? 'selected' : ''}}" onclick="setDrive('auto')">
            <div>
              <div style="font-weight: 500;">auto - Automatic Detection (Recommended)</div>
              <div style="font-size: 0.8rem; color: var(--md-on-surface-variant);">Dynamically resolves the active OS boot disk</div>
            </div>
            <span class="material-symbols-outlined" style="color: var(--google-green);">auto_awesome</span>
          </div>
        `;

        data.disks.forEach(d => {{
          const isSel = (cur === d.path);
          html += `
            <div class="selectable-card ${{isSel ? 'selected' : ''}}" onclick="setDrive('${{d.path}}')">
              <div>
                <div style="font-weight: 500;">${{d.path}} (${{d.size}})</div>
                <div style="font-size: 0.8rem; color: var(--md-on-surface-variant);">${{d.model}} &bull; Bus: ${{d.tran}} &bull; ${{d.rota ? 'HDD' : 'Flash/SSD'}}</div>
              </div>
              <span class="material-symbols-outlined" style="color: var(--md-primary);">dns</span>
            </div>
          `;
        }});
        container.innerHTML = html;
      }} catch (e) {{
        console.error("Could not fetch disks", e);
      }}
    }}

    function setDrive(path) {{
      document.getElementById("cfg-source-dev").value = path;
      fetchDisks();
    }}

    function initSchedulePickers() {{
      const hourSelect = document.getElementById("cfg-sched-hour");
      if (hourSelect.children.length === 0) {{
        for (let i = 0; i < 24; i++) {{
          const v = (i < 10 ? '0' + i : '' + i);
          const opt = document.createElement("option");
          opt.value = v;
          opt.innerText = v + ":00";
          hourSelect.appendChild(opt);
        }}
      }}

      const cronParts = document.getElementById("cfg-backup-cron").value.trim().split(/\\s+/);
      if (cronParts.length === 5) {{
        document.getElementById("cfg-sched-min").value = cronParts[0].padStart(2, '0');
        document.getElementById("cfg-sched-hour").value = cronParts[1].padStart(2, '0');

        if (cronParts[2] === '*' && cronParts[3] === '*' && cronParts[4] !== '*') {{
          document.getElementById("cfg-sched-freq").value = "weekly";
          const days = cronParts[4].split(",");
          document.querySelectorAll(".filter-chip").forEach(c => {{
            c.className = days.includes(c.getAttribute("data-day")) ? "filter-chip active" : "filter-chip";
          }});
        }} else if (cronParts[2] === '*' && cronParts[3] === '*' && cronParts[4] === '*') {{
          document.getElementById("cfg-sched-freq").value = "daily";
        }} else if (cronParts[2] === '1' && cronParts[3] === '*' && cronParts[4] === '*') {{
          document.getElementById("cfg-sched-freq").value = "monthly";
        }} else {{
          document.getElementById("cfg-sched-freq").value = "custom";
        }}
      }}
      handleFrequencyChange(document.getElementById("cfg-sched-freq").value);
    }}

    function handleFrequencyChange(val) {{
      document.getElementById("time-picker-block").style.display = (val === 'custom' ? 'none' : 'flex');
      document.getElementById("days-chip-block").style.display = (val === 'weekly' ? 'block' : 'none');
      document.getElementById("custom-cron-block").style.display = (val === 'custom' ? 'block' : 'none');
      compileCron();
    }}

    function toggleChip(chip) {{
      chip.classList.toggle("active");
      compileCron();
    }}

    function compileCron() {{
      const freq = document.getElementById("cfg-sched-freq").value;
      if (freq === 'custom') return;

      const min = document.getElementById("cfg-sched-min").value || "0";
      const hr = document.getElementById("cfg-sched-hour").value || "3";

      let cron = `${{parseInt(min, 10)}} ${{parseInt(hr, 10)}} * * *`;
      if (freq === 'weekly') {{
        const active = [];
        document.querySelectorAll(".filter-chip.active").forEach(c => active.push(c.getAttribute("data-day")));
        cron = `${{parseInt(min, 10)}} ${{parseInt(hr, 10)}} * * ${{active.length > 0 ? active.join(",") : "0"}}`;
      }} else if (freq === 'monthly') {{
        cron = `${{parseInt(min, 10)}} ${{parseInt(hr, 10)}} 1 * *`;
      }}
      document.getElementById("cfg-backup-cron").value = cron;
    }}

    async function probeDirectory() {{
      const dir = document.getElementById("cfg-target-dir").value;
      const resEl = document.getElementById("probe-msg");
      resEl.style.display = "block";
      resEl.innerText = "Probing directory write access...";

      try {{
        const res = await fetch(basePath + "/api/system/probe?dir=" + encodeURIComponent(dir));
        const data = await res.json();
        resEl.style.color = (res.status === 200 ? "var(--google-green)" : "var(--google-red)");
        resEl.innerText = data.message;
      }} catch (e) {{
        resEl.style.color = "var(--google-red)";
        resEl.innerText = "Directory probe request failed.";
      }}
    }}

    async function saveConfiguration() {{
      compileCron();
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
        showToast(data.message || "Settings updated.");
      }} catch (e) {{
        alert("Failed to save configuration settings.");
      }}
    }}

    function copyConsoleLog() {{
      navigator.clipboard.writeText(document.getElementById("console-output").innerText).then(() => showToast("Log copied."));
    }}

    function copyText(val) {{
      navigator.clipboard.writeText(val).then(() => showToast("Copied to clipboard."));
    }}

    async function verifyArchive(filename) {{
      showToast("Testing archive integrity with pigz -t...");
      try {{
        const res = await fetch(basePath + "/api/verify?file=" + encodeURIComponent(filename));
        const data = await res.json();
        alert(data.message);
      }} catch (e) {{
        alert("Verification request failed.");
      }}
    }}

    async function fetchStatus() {{
      try {{
        const res = await fetch(basePath + "/api/status");
        const data = await res.json();

        let statusText = "Status: " + data.status;
        if (data.status === "Running" && data.speed) {{
          statusText = `Streaming: ${{data.progress}} (${{data.speed}} &bull; ETA: ${{data.eta}})`;
        }}
        document.getElementById("backup-status-text").innerHTML = statusText;
        document.getElementById("val-wear").innerText = data.wear;
        document.getElementById("val-health").innerText = data.health;
        document.getElementById("console-output").innerText = data.log || "No log transactions.";

        const pBar = document.getElementById("global-progress");
        const pFill = document.getElementById("global-progress-fill");
        if (data.status === "Running") {{
          pBar.style.display = "block";
          pFill.style.width = data.progress;
          document.getElementById("btn-run-backup").disabled = true;
        }} else {{
          pBar.style.display = "none";
          document.getElementById("btn-run-backup").disabled = false;
        }}

        const tbody = document.getElementById("table-artifacts");
        tbody.innerHTML = "";
        if (data.artifacts && data.artifacts.length > 0) {{
          data.artifacts.forEach(item => {{
            const row = document.createElement("tr");
            let actionHtml = `<a href="${{basePath}}/api/download?file=${{encodeURIComponent(item.name)}}" class="m3-button btn-tonal" style="height: 28px; padding: 0 0.75rem; text-decoration: none; font-size: 0.75rem;">Download</a>`;
            if (item.name.endsWith(".img.gz")) {{
              actionHtml += ` <button class="m3-button btn-tonal" style="height: 28px; padding: 0 0.65rem; font-size: 0.75rem;" onclick="verifyArchive('${{item.name}}')">Test Integrity</button>`;
            }}
            if (item.name.endsWith(".sha256")) {{
              actionHtml += ` <button class="m3-button btn-tonal" style="height: 28px; padding: 0 0.5rem;" onclick="copyText('${{item.hash || item.name}}')"><span class="material-symbols-outlined" style="font-size: 14px;">content_copy</span></button>`;
            }}
            row.innerHTML = `
              <td style="font-family: monospace;">${{item.name}}</td>
              <td>${{item.size}}</td>
              <td style="text-align: right;">${{actionHtml}}</td>
            `;
            tbody.appendChild(row);
          }});
        }} else {{
          tbody.innerHTML = `<tr><td colspan="3" style="text-align: center; color: var(--md-on-surface-variant);">No backup packages found in destination directory.</td></tr>`;
        }}
      }} catch (e) {{
        console.error("Status polling failed", e);
      }}
    }}

    async function triggerAction(action) {{
      try {{
        const res = await fetch(basePath + "/api/" + action, {{ method: "POST" }});
        const data = await res.json();
        showToast(data.message);
        setTimeout(fetchStatus, 1000);
      }} catch (e) {{
        alert("Action failed.");
      }}
    }}

    fetchStatus();
    setInterval(fetchStatus, 4000);
  </script>
</body>
</html>"""
            self.wfile.write(html.encode("utf-8"))
            return

        elif path == "/api/system/storage-targets":
            targets = []
            seen_paths = set()
            token = os.environ.get("SUPERVISOR_TOKEN")

            if token:
                try:
                    req = urllib.request.Request(
                        "http://supervisor/mounts",
                        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                    )
                    with urllib.request.urlopen(req, timeout=4) as resp:
                        m_data = json.loads(resp.read().decode("utf-8"))
                        if m_data.get("result") == "ok":
                            raw_mounts = m_data.get("data", {}).get("mounts", [])
                            default_backup = m_data.get("data", {}).get("default_backup")
                            for rm in raw_mounts:
                                m_usage = rm.get("usage", "backup")
                                m_name = rm.get("name", "Network Storage")
                                m_server = rm.get("server", "")
                                m_share = rm.get("share", "")
                                m_type = rm.get("type", "cifs").upper()
                                full_remote = f"//{m_server}/{m_share}" if (m_server and m_share) else m_name

                                candidate_paths = [
                                    "/backup",
                                    f"/mnt/network_storage/{m_name}",
                                    f"/proc/1/root/mnt/data/supervisor/mounts/{m_name}",
                                    f"/share/{m_name}"
                                ]

                                chosen_path = "/backup"
                                best_total, best_used, best_free, best_pct = 0, 0, 0, 0

                                for cp in candidate_paths:
                                    tot, usd, fre, pct = get_mount_stats(cp)
                                    if tot > 40 * 1073741824:
                                        chosen_path = cp
                                        best_total, best_used, best_free, best_pct = tot, usd, fre, pct
                                        break
                                    elif tot > best_total:
                                        chosen_path = cp
                                        best_total, best_used, best_free, best_pct = tot, usd, fre, pct

                                free_str = f"{best_used / 1073741824:.1f} GB of {best_total / 1073741824:.1f} GB used ({best_free / 1073741824:.1f} GB free)" if best_total > 0 else "Ready"

                                seen_paths.add(chosen_path)
                                targets.append({
                                    "name": m_name,
                                    "source": full_remote,
                                    "path": chosen_path,
                                    "type": f"SMB / {m_type}",
                                    "free": free_str,
                                    "pct_used": best_pct,
                                    "is_default": (m_name == default_backup or m_usage == "backup")
                                })
                except Exception as ex:
                    print(f"[!] Supervisor /mounts exception: {ex}", file=sys.stderr)

            if "/backup" not in seen_paths:
                tot, usd, fre, pct = get_mount_stats("/backup")
                targets.append({
                    "name": "Pi4HomeAssistant (Host Network Storage)",
                    "source": "//192.168.0.190/Backups_Pi4HA",
                    "path": "/backup",
                    "type": "SMB / CIFS",
                    "free": f"{usd / 1073741824:.1f} GB of {tot / 1073741824:.1f} GB used ({fre / 1073741824:.1f} GB free)",
                    "pct_used": pct,
                    "is_default": True
                })
                seen_paths.add("/backup")

            if os.path.exists("/media"):
                try:
                    for entry in os.listdir("/media"):
                        usb_path = os.path.join("/media", entry)
                        if os.path.isdir(usb_path) and usb_path not in seen_paths:
                            tot, usd, fre, pct = get_mount_stats(usb_path)
                            targets.append({
                                "name": f"USB Storage ({entry})",
                                "source": f"/media/{entry}",
                                "path": usb_path,
                                "type": "USB Storage",
                                "free": f"{usd / 1073741824:.1f} GB of {tot / 1073741824:.1f} GB used ({fre / 1073741824:.1f} GB free)",
                                "pct_used": pct,
                                "is_default": False
                            })
                            seen_paths.add(usb_path)
                except Exception:
                    pass

            self.send_json({"targets": targets})
            return

        elif path == "/api/verify":
            qs = parse_qs(parsed.query)
            filename = os.path.basename(qs.get("file", [""])[0])
            full_path = os.path.join(target_dir, filename)
            if not os.path.exists(full_path):
                self.send_json({"message": "File does not exist."}, status=404)
                return

            cmd = subprocess.run(["pigz", "-t", full_path], capture_output=True, text=True)
            if cmd.returncode == 0:
                self.send_json({"message": f"✔ Verification Passed: {filename} integrity is 100% OK (no corruption detected)."})
            else:
                self.send_json({"message": f"✖ Corrupted archive! pigz -t failed: {cmd.stderr}"}, status=422)
            return

        elif path == "/api/system/disks":
            disks = []
            try:
                cmd = subprocess.run(["lsblk", "-d", "-b", "-n", "-o", "NAME,SIZE,TYPE,MODEL,TRAN,ROTA"], capture_output=True, text=True)
                for line in cmd.stdout.strip().split("\n"):
                    parts = line.split()
                    if len(parts) >= 3 and parts[2] == "disk":
                        d_name = parts[0]
                        bytes_sz = int(parts[1]) if parts[1].isdigit() else 0
                        human_sz = f"{bytes_sz / 1073741824:.1f} GB"
                        model = parts[3] if len(parts) > 3 else "Storage Block"
                        tran = parts[4] if len(parts) > 4 else "Bus"
                        rota = parts[5] == "1" if len(parts) > 5 else False
                        disks.append({
                            "path": f"/dev/{d_name}",
                            "size": human_sz,
                            "model": model,
                            "tran": tran,
                            "rota": rota
                        })
            except Exception:
                pass
            self.send_json({"disks": disks})
            return

        elif path == "/api/system/probe":
            qs = parse_qs(parsed.query)
            target = qs.get("dir", ["/backup"])[0].rstrip("/")
            if not os.path.exists(target):
                self.send_json({"message": f"Path '{target}' does not exist on host."}, status=404)
                return
            probe_file = os.path.join(target, ".probe_test")
            try:
                with open(probe_file, "w") as f:
                    f.write("test")
                os.remove(probe_file)
                total, used, free, pct = get_mount_stats(target)
                self.send_json({"message": f"✔ Reachable & Writable. Free capacity: {free / 1073741824:.2f} GB."})
            except Exception as ex:
                self.send_json({"message": f"✖ Write failed on '{target}': {str(ex)}"}, status=500)
            return

        elif path == "/api/status":
            wear_out = "N/A"
            health_out = "Unsupported"
            try:
                cmd = subprocess.run(["/run.sh", "--wear-metrics"], capture_output=True, text=True, timeout=5)
                if cmd.returncode == 0:
                    metric_lines = [l.strip() for l in cmd.stdout.strip().splitlines() if "|" in l]
                    if metric_lines:
                        parts = metric_lines[-1].split("|")
                        wear_out = parts[0].strip()
                        health_out = parts[1].strip()
                        if wear_out not in ["N/A", "N/A*"] and not wear_out.endswith("%"):
                            wear_out += "%"
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
                    latest_log = "Unable to read active log stream."

            status_val = "Idle"
            prog_val = "0%"
            speed_val = ""
            eta_val = ""
            if os.path.exists("/var/run/sd_backup.lock"):
                status_val = "Running"
                prog_file = "/var/run/sd_backup.progress"
                if os.path.exists(prog_file):
                    try:
                        with open(prog_file, "r") as pf:
                            p_parts = pf.read().strip().split("|")
                            prog_val = p_parts[0]
                            if len(p_parts) > 1:
                                speed_val = p_parts[1]
                            if len(p_parts) > 2:
                                eta_val = p_parts[2]
                    except Exception:
                        pass

            self.send_json({
                "status": status_val,
                "progress": prog_val,
                "speed": speed_val,
                "eta": eta_val,
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
                subprocess.Popen(["/run.sh", "--update-cron"])
                self.send_json({"message": "✔ Configuration committed and crontab updated."})
            except Exception as ex:
                self.send_json({"message": f"Failed to commit settings: {str(ex)}"}, status=500)
            return

        elif path == "/api/backup":
            if os.path.exists("/var/run/sd_backup.lock"):
                self.send_json({"message": "A backup process is already actively executing."}, status=409)
                return
            subprocess.Popen(["/run.sh", "--backup"])
            self.send_json({"message": "Disk backup process successfully started in background."})
            return

        elif path == "/api/wear":
            subprocess.Popen(["/run.sh", "--wear"])
            self.send_json({"message": "Flash wear diagnostic dispatched."})
            return

        self.send_response(404)
        self.end_headers()

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), WebDashboardHandler)
    server.serve_forever()