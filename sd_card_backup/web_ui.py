#!/usr/bin/env python3
import os
import sys
import json
import fcntl
import subprocess
import glob
import shutil
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

LOCK_FILE = "/var/run/sd_backup.lock"

def is_backup_active():
    """Checks whether the backup lock is actively held by a running process."""
    if not os.path.exists(LOCK_FILE):
        return False
    try:
        with open(LOCK_FILE, "r") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
        try:
            os.remove(LOCK_FILE)
        except Exception:
            pass
        return False
    except (BlockingIOError, PermissionError):
        return True
    except OSError as ex:
        import errno
        if ex.errno == errno.ENOENT:
            return False
        if ex.errno in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
            return True
        return False

PORT = 8099
OPTIONS_FILE = "/data/options.json"

DEFAULT_CONFIG = {
    "source_dev": "auto",
    "target_dir": "",
    "retention_count": 3,
    "enable_rescue": False,
    "run_backup_on_start": False,
    "safe_wear_threshold": 80,
    "schedule_enabled": False,
    "backup_password": "",
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

def scan_backup_packages(directory):
    """Scans and groups backup artifacts by timestamp into hierarchical trees."""
    packages = {}
    if not directory or not os.path.exists(directory):
        return []

    try:
        for fname in os.listdir(directory):
            fpath = os.path.join(directory, fname)
            if not os.path.isfile(fpath):
                continue

            ts = None
            if fname.startswith("haos_backup_"):
                parts = fname.replace("haos_backup_", "").split(".")
                ts = parts[0]
            elif fname.startswith("restore_"):
                parts = fname.replace("restore_", "").split(".")
                ts = parts[0]

            if ts:
                if ts not in packages:
                    readable_date = ts.replace("_", " ")
                    packages[ts] = {
                        "id": ts,
                        "date": readable_date,
                        "total_bytes": 0,
                        "files": []
                    }
                sz = os.path.getsize(fpath)
                packages[ts]["total_bytes"] += sz
                sz_str = f"{sz / 1048576:.1f} MB" if sz < 1073741824 else f"{sz / 1073741824:.2f} GB"

                hash_val = ""
                if fname.endswith(".sha256"):
                    try:
                        with open(fpath, "r") as hf:
                            hash_val = hf.read().strip().split()[0]
                    except Exception:
                        pass

                packages[ts]["files"].append({
                    "name": fname,
                    "size": sz_str,
                    "bytes": sz,
                    "hash": hash_val
                })
    except Exception:
        pass

    result = []
    for ts in sorted(packages.keys(), reverse=True):
        pkg = packages[ts]
        tot = pkg["total_bytes"]
        pkg["total_size"] = f"{tot / 1048576:.1f} MB" if tot < 1073741824 else f"{tot / 1073741824:.2f} GB"
        pkg["files"].sort(key=lambda x: x["name"])
        result.append(pkg)
    return result

SETTINGS_FILE = "/data/settings.json"

def get_config():
    cfg = DEFAULT_CONFIG.copy()
    if os.path.exists(OPTIONS_FILE):
        try:
            with open(OPTIONS_FILE, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    # Persistent file that Supervisor NEVER overwrites on container restart
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg

def save_config(new_opts):
    cfg = get_config()
    cfg.update(new_opts)
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass
    try:
        with open(OPTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass

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
    """Calculates disk capacity directly from the target mount path."""
    try:
        if target_path and os.path.exists(target_path):
            real_path = os.path.realpath(target_path) if os.path.islink(target_path) else target_path
            total, used, free = shutil.disk_usage(real_path)
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
        target_dir = cfg.get("target_dir", "").rstrip("/")

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
      <span style="font-size: 0.85rem; background: rgba(255,255,255,0.15); padding: 0.4rem 0.8rem; border-radius: 12px;">v1.8.0</span>
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
          <button class="m3-button btn-filled" id="btn-run-backup" onclick="handleBackupClick()">
            <span class="material-symbols-outlined">play_arrow</span> Start Backup Now
          </button>
          <span id="backup-status-text" style="font-size: 0.9rem; font-weight: 500;">Status: Idle</span>
        </div>

        <div class="progress-bar-container" id="global-progress">
          <div class="progress-bar-fill" id="global-progress-fill"></div>
        </div>
      </div>

      <!-- LIVE ACTIVE SCHEDULE STATUS CARD (TAB 2) -->
      <div class="m3-card schedule-status-card" style="border-left: 4px solid var(--google-green);">
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <div>
            <div style="font-size: 0.8rem; text-transform: uppercase; color: var(--md-on-surface-variant); font-weight: 600;">Active Schedule Status</div>
            <div style="font-size: 1.15rem; font-weight: 700; color: #FFFFFF; margin-top: 0.2rem;" class="display-cron-summary">Loading schedule...</div>
            <div style="font-size: 0.82rem; color: var(--md-on-surface-variant); margin-top: 0.2rem;">
              Cron Syntax: <code class="display-cron-raw" style="color: var(--md-primary); background: #15161A; padding: 0.1rem 0.35rem; border-radius: 4px;">--</code> &bull; <span class="display-cron-pill" style="color: var(--google-green); font-weight: 500;">● Active in Crontab</span>
            </div>
          </div>
          <span class="material-symbols-outlined display-cron-icon" style="font-size: 36px; color: var(--google-green);">check_circle</span>
        </div>
      </div>

      <!-- BACKUP PACKAGES TREE & BATCH MANAGEMENT -->
      <div class="m3-card">
        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.75rem;">
          <div>
            <div class="card-title">
              <span class="material-symbols-outlined" style="color: var(--google-green);">folder_zip</span>
              Disaster Recovery Packages &amp; Backups
            </div>
            <div style="font-size: 0.82rem; color: var(--md-on-surface-variant); margin-top: 0.2rem;">
              Location: <code id="tree-active-dir" style="color: var(--md-primary); background: #0E0F12; padding: 0.15rem 0.4rem; border-radius: 4px;">{target_dir}</code>
            </div>
          </div>

          <div style="display: flex; gap: 0.5rem; align-items: center;">
            <button class="m3-button btn-tonal" style="height: 32px; font-size: 0.8rem; background: rgba(234, 67, 53, 0.15); color: #EA4335; border-color: rgba(234, 67, 53, 0.3);" id="btn-delete-selected" onclick="deleteSelectedBackups()" disabled>
              <span class="material-symbols-outlined" style="font-size: 16px;">delete_sweep</span> Delete Selected
            </button>
            <button class="m3-button btn-tonal" style="height: 32px; font-size: 0.8rem;" onclick="refreshBackupTree()">
              <span class="material-symbols-outlined" style="font-size: 16px;">refresh</span> Refresh
            </button>
          </div>
        </div>

        <div style="display: flex; align-items: center; gap: 0.6rem; padding: 0.5rem 0; border-bottom: 1px solid var(--md-outline-variant);">
          <input type="checkbox" id="chk-select-all" style="width: 17px; height: 17px; cursor: pointer;" onchange="toggleSelectAll(this.checked)">
          <label for="chk-select-all" style="font-size: 0.85rem; font-weight: 500; cursor: pointer;">Select All Packages</label>
          <span id="selected-count-label" style="font-size: 0.8rem; color: var(--md-on-surface-variant); margin-left: auto;">0 selected</span>
        </div>

        <div id="backup-tree-container" style="display: flex; flex-direction: column; gap: 0.75rem; margin-top: 0.5rem;">
          <div style="text-align: center; color: var(--md-on-surface-variant); padding: 1.5rem 0;">Scanning directory for backups...</div>
        </div>
      </div>
    </div>

    <!-- TAB 2: STORAGE & SCHEDULE CONFIG -->
    <div id="tab-content-config" style="display: none; flex-direction: column; gap: 1.5rem;">
      
      <!-- MUST DO INSTRUCTIONS FOR NETWORK STORAGE -->
      <div class="m3-card" style="border-left: 4px solid var(--google-yellow); background: rgba(251, 188, 5, 0.08);">
        <div style="display: flex; align-items: flex-start; gap: 0.75rem;">
          <span class="material-symbols-outlined" style="color: var(--google-yellow); font-size: 28px; flex-shrink: 0;">warning</span>
          <div>
            <div style="font-family: 'Google Sans', sans-serif; font-weight: 700; font-size: 1.05rem; color: #FFFFFF;">
              Required: Configure Network Storage Usage as "Share"
            </div>
            <p style="font-size: 0.88rem; color: var(--md-on-surface); margin-top: 0.35rem; line-height: 1.45;">
              In Home Assistant OS (<b>Settings &gt; System &gt; Storage &gt; Add network storage</b>), set <b>Usage</b> to <b>Share</b> (do <i>not</i> select "Backup")[cite: 4].
            </p>
            <div style="font-size: 0.82rem; color: var(--md-on-surface-variant); margin-top: 0.4rem; line-height: 1.4;">
              &bull; Supervisor mounts <b>Share</b> storage at <code>/share/&lt;ShareName&gt;</code>, exposing the full remote capacity (~145+ GB free)[cite: 4].<br>
              &bull; Setting usage to "Backup" isolates the share to Supervisor's internal backup tool and locks add-ons to the 28.5 GB SD card[cite: 4].<br>
              &bull; Once added as "Share", click the discovered card below to target it directly.
            </div>
          </div>
        </div>
      </div>

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

      <!-- TARGET STORAGE DESTINATION -->
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

      <!-- ZERO-KNOWLEDGE AES-256 ENCRYPTION -->
      <div class="m3-card">
        <div class="card-title">
          <span class="material-symbols-outlined" style="color: var(--google-yellow);">lock</span>
          Zero-Knowledge AES-256 Archive Encryption
        </div>
        <p style="font-size: 0.88rem; color: var(--md-on-surface-variant);">
          Encrypt all local and cloud backup archives using OpenSSL AES-256-CBC with PBKDF2 (100,000 iterations). Leave blank to disable encryption.
        </p>
        <div style="display: flex; gap: 0.75rem; align-items: center; margin-top: 0.25rem;">
          <input type="password" class="form-input" id="cfg-backup-password" placeholder="Enter encryption passphrase (leave blank to disable)" value="{cfg.get('backup_password', '')}" style="flex: 1;">
          <button class="m3-button btn-tonal" onclick="togglePassVisibility()">
            <span class="material-symbols-outlined" id="pass-vis-icon" style="font-size: 18px;">visibility</span>
          </button>
        </div>
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
          <input type="checkbox" id="cfg-rclone-enabled" {"checked" if cfg.get('rclone_sync_enabled', False) else ""} style="width: 18px; height: 18px;">
          <label for="cfg-rclone-enabled" style="font-size: 0.95rem; font-weight: 500;">Enable automated offsite cloud sync</label>
        </div>

        <div style="display: flex; flex-direction: column; gap: 0.4rem; margin-top: 0.5rem;">
          <label style="font-size: 0.85rem; font-weight: 500;">Rclone Remote Target Path</label>
          <input type="text" class="form-input" id="cfg-rclone-target" placeholder="e.g. b2:my-haos-bucket/backups or gdrive:HAOS_Backups" value="{cfg.get('rclone_remote_target', '')}">
          <span style="font-size: 0.8rem; color: var(--md-on-surface-variant);">Format: <code style="color: var(--md-primary);">RemoteName:Path/To/Folder</code></span>
        </div>
      </div>

      <!-- LIVE ACTIVE SCHEDULE STATUS CARD (TAB 1) -->
      <div class="m3-card schedule-status-card" style="border-left: 4px solid var(--google-green);">
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <div>
            <div style="font-size: 0.8rem; text-transform: uppercase; color: var(--md-on-surface-variant); font-weight: 600;">Active Schedule Status</div>
            <div style="font-size: 1.15rem; font-weight: 700; color: #FFFFFF; margin-top: 0.2rem;" class="display-cron-summary">Loading schedule...</div>
            <div style="font-size: 0.82rem; color: var(--md-on-surface-variant); margin-top: 0.2rem;">
              Cron Syntax: <code class="display-cron-raw" style="color: var(--md-primary); background: #15161A; padding: 0.1rem 0.35rem; border-radius: 4px;">--</code> &bull; <span class="display-cron-pill" style="color: var(--google-green); font-weight: 500;">● Active in Crontab</span>
            </div>
          </div>
          <span class="material-symbols-outlined display-cron-icon" style="font-size: 36px; color: var(--google-green);">check_circle</span>
        </div>
      </div>

      <!-- AUTOMATED SCHEDULE & AUTO-PRUNING POLICY -->
      <div class="m3-card">
        <div class="card-title">
          <span class="material-symbols-outlined" style="color: var(--google-blue);">calendar_month</span>
          Automated Schedule &amp; Auto-Pruning Policy
        </div>
        <p style="font-size: 0.85rem; color: var(--md-on-surface-variant);">
          Configure scheduled background backups and retention limits. The oldest backup package is deleted automatically when the retention count is reached[cite: 4].
        </p>

        <!-- MASTER SCHEDULE ENABLE / DISABLE TOGGLE -->
        <div style="display: flex; align-items: center; justify-content: space-between; background: #15161A; padding: 0.85rem 1.15rem; border-radius: 12px; border: 1px solid var(--md-outline-variant);">
          <div>
            <div style="font-weight: 600; font-size: 0.95rem;">Enable Automated Backup Scheduling</div>
            <div style="font-size: 0.8rem; color: var(--md-on-surface-variant);">Disable this toggle to run backups manually without automated triggers</div>
          </div>
          <input type="checkbox" id="cfg-sched-enabled" style="width: 22px; height: 22px; cursor: pointer;" {"checked" if cfg.get('schedule_enabled', True) else ""} onchange="toggleScheduleActive(this.checked)">
        </div>

        <div id="schedule-controls-wrapper" style="display: flex; flex-direction: column; gap: 1rem; margin-top: 0.5rem; opacity: 1; transition: opacity 0.2s;">
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
            <input type="text" class="form-input" style="width: 100%; margin-top: 0.35rem;" id="cfg-backup-cron" value="{cfg.get('backup_cron', '0 3 * * 0')}">
          </div>

          <div style="margin-top: 0.5rem;">
            <label style="font-size: 0.85rem; font-weight: 500;">Retention Count (Keep Last N Backups)</label>
            <div style="display: flex; align-items: center; gap: 1rem; margin-top: 0.35rem;">
              <input type="range" id="cfg-retention" min="1" max="15" value="{cfg.get('retention_count', 3)}" style="flex: 1;" oninput="document.getElementById('ret-val').innerText = this.value">
              <span id="ret-val" style="font-size: 1.25rem; font-weight: bold; min-width: 30px;">{cfg.get('retention_count', 3)}</span>
            </div>
          </div>

          <div style="display: flex; justify-content: flex-end; margin-top: 1rem;">
            <button class="m3-button btn-filled" onclick="saveConfiguration()">
              <span class="material-symbols-outlined">save</span> Save Configuration
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- TAB 3: HEALTH & LOGS -->
    <div id="tab-content-health" style="display: none; flex-direction: column; gap: 1.5rem;">
      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem;">
        <div class="m3-card">
          <span style="font-size: 0.8rem; text-transform: uppercase; color: var(--md-on-surface-variant);">Flash Memory Wear</span>
          <div style="font-size: 1.7rem; font-weight: bold; color: var(--google-yellow);" id="val-wear">--</div>
          <span style="font-size: 0.8rem; color: var(--md-on-surface-variant);">Warning Limit: {cfg.get('safe_wear_threshold', 80)}%</span>
        </div>
        <div class="m3-card">
          <span style="font-size: 0.8rem; text-transform: uppercase; color: var(--md-on-surface-variant);">SoC Temperature</span>
          <div style="font-size: 1.7rem; font-weight: bold; color: var(--google-green);" id="val-temp">--</div>
          <span style="font-size: 0.8rem; color: var(--md-on-surface-variant);">Pi 4 Watchdog Limit: 75°C</span>
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

  <!-- LOG VIEWER MODAL DIALOG -->
  <div id="log-modal" style="display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.75); backdrop-filter: blur(4px); z-index: 3000; align-items: center; justify-content: center; padding: 1.5rem;">
    <div class="m3-card" style="width: 100%; max-width: 850px; max-height: 85vh; display: flex; flex-direction: column; background: #18191E; border: 1px solid var(--md-outline); border-radius: 20px; box-shadow: 0 16px 40px rgba(0,0,0,0.6);">
      <div style="display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--md-outline-variant); padding-bottom: 0.75rem;">
        <div style="display: flex; align-items: center; gap: 0.5rem; overflow: hidden;">
          <span class="material-symbols-outlined" style="color: var(--google-blue);">description</span>
          <span id="log-modal-title" style="font-family: 'Google Sans', sans-serif; font-weight: 700; font-size: 1.05rem; color: #FFFFFF; text-overflow: ellipsis; white-space: nowrap; overflow: hidden;">Log Viewer</span>
        </div>
        <div style="display: flex; gap: 0.5rem;">
          <button class="m3-button btn-tonal" style="height: 32px; font-size: 0.8rem;" onclick="copyModalLog()">
            <span class="material-symbols-outlined" style="font-size: 16px;">content_copy</span> Copy
          </button>
          <button class="m3-button btn-tonal" style="height: 32px; font-size: 0.8rem; padding: 0 0.6rem;" onclick="closeLogModal()">
            <span class="material-symbols-outlined" style="font-size: 18px;">close</span>
          </button>
        </div>
      </div>
      <pre id="log-modal-content" style="flex: 1; overflow-y: auto; background: #0E0F12; border: 1px solid var(--md-outline-variant); border-radius: 10px; padding: 1rem; margin-top: 0.75rem; font-family: monospace; font-size: 0.82rem; line-height: 1.45; color: #D1D5DB; white-space: pre-wrap;">Loading log...</pre>
    </div>
  </div>

  <!-- COLD SPARE BURN MODAL -->
  <div id="burn-modal" style="display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.75); backdrop-filter: blur(4px); z-index: 3100; align-items: center; justify-content: center; padding: 1.5rem;">
    <div class="m3-card" style="width: 100%; max-width: 600px; background: #18191E; border: 1px solid var(--md-outline); border-radius: 20px;">
      <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--md-outline-variant); padding-bottom: 0.75rem;">
        <div style="display: flex; align-items: center; gap: 0.5rem;">
          <span class="material-symbols-outlined" style="color: var(--google-yellow);">local_fire_department</span>
          <span style="font-family: 'Google Sans', sans-serif; font-weight: 700; font-size: 1.1rem; color: #FFFFFF;">Burn to Cold Spare Drive</span>
        </div>
        <button class="m3-button btn-tonal" style="height: 32px; padding: 0 0.6rem;" onclick="closeBurnModal()">
          <span class="material-symbols-outlined" style="font-size: 18px;">close</span>
        </button>
      </div>

      <div style="display: flex; flex-direction: column; gap: 0.85rem; margin-top: 1rem;">
        <div style="font-size: 0.85rem; color: var(--md-on-surface);">
          Source Archive: <code id="burn-target-file" style="color: var(--md-primary); background: #0E0F12; padding: 0.2rem 0.4rem; border-radius: 4px;"></code>
        </div>
        <div style="font-size: 0.85rem; color: var(--google-yellow);">
          ⚠️ WARNING: All partitions on the selected destination target will be permanently wiped and overwritten!
        </div>

        <label style="font-size: 0.85rem; font-weight: 500;">Select Target Spare Drive (USB / Card Reader):</label>
        <select class="form-select" id="burn-disk-select" style="width: 100%;">
          <option value="">-- Scanning connected drives --</option>
        </select>

        <div id="burn-pass-wrapper" style="display: none; flex-direction: column; gap: 0.35rem;">
          <label style="font-size: 0.85rem; font-weight: 500;">Decryption Passphrase:</label>
          <input type="password" class="form-input" id="burn-password-input" placeholder="Passphrase required for encrypted archive">
        </div>

        <div style="display: flex; justify-content: flex-end; gap: 0.5rem; margin-top: 0.5rem;">
          <button class="m3-button btn-tonal" onclick="closeBurnModal()">Cancel</button>
          <button class="m3-button btn-filled" id="btn-confirm-burn" style="background: var(--google-red);" onclick="executeBurn()">
            <span class="material-symbols-outlined">flash_on</span> Flash Spare Drive
          </button>
        </div>
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

    let lastStorageTargetsHash = "";

    async function fetchStorageTargets() {{
      const container = document.getElementById("smb-mounts-list");
      const dropdown = document.getElementById("storage-quick-select");
      try {{
        const res = await fetch(basePath + "/api/system/storage-targets");
        const data = await res.json();
        const curTarget = document.getElementById("cfg-target-dir").value.trim();

        // Prevent DOM flicker every 4s unless targets actually changed
        const currentHash = JSON.stringify(data.targets) + "|" + curTarget;
        if (currentHash === lastStorageTargetsHash) return;
        lastStorageTargetsHash = currentHash;

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
      const treeLabel = document.getElementById("tree-active-dir");
      if (treeLabel) treeLabel.innerText = path;

      fetchStorageTargets();
      probeDirectory();
      loadBackupTree(path);
      showToast("Selected destination: " + path);
    }}

    function updateScheduleDisplayUI(summary, rawExpr, isEnabled) {{
      document.querySelectorAll(".display-cron-summary").forEach(el => {{
        el.innerText = isEnabled ? summary : "Disabled (Manual Execution Only)";
        el.style.color = isEnabled ? "#FFFFFF" : "var(--md-on-surface-variant)";
      }});
      document.querySelectorAll(".display-cron-raw").forEach(el => {{
        el.innerText = isEnabled ? rawExpr : "Disabled";
      }});
      document.querySelectorAll(".display-cron-pill").forEach(el => {{
        el.innerHTML = isEnabled ? "● Active in Crontab" : "○ Disabled / Paused";
        el.style.color = isEnabled ? "var(--google-green)" : "var(--google-yellow)";
      }});
      document.querySelectorAll(".display-cron-icon").forEach(el => {{
        el.innerText = isEnabled ? "check_circle" : "pause_circle";
        el.style.color = isEnabled ? "var(--google-green)" : "var(--google-yellow)";
      }});
      document.querySelectorAll(".schedule-status-card").forEach(el => {{
        el.style.borderLeftColor = isEnabled ? "var(--google-green)" : "var(--google-yellow)";
      }});
    }}

    async function toggleScheduleActive(isActive, autoSave = true) {{
      const wrapper = document.getElementById("schedule-controls-wrapper");
      if (wrapper) {{
        wrapper.style.pointerEvents = isActive ? "auto" : "none";
        wrapper.style.opacity = isActive ? "1" : "0.45";
      }}
      const rawCron = document.getElementById("cfg-backup-cron") ? document.getElementById("cfg-backup-cron").value : "--";
      updateScheduleDisplayUI(isActive ? "Schedule Active" : "Disabled (Manual Execution Only)", rawCron, isActive);
      if (autoSave) {{
        showToast(isActive ? "Saving: Automated schedule enabled..." : "Saving: Automated schedule disabled...");
        await saveConfiguration();
      }}
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

    function togglePassVisibility() {{
      const el = document.getElementById("cfg-backup-password");
      const icon = document.getElementById("pass-vis-icon");
      if (el.type === "password") {{
        el.type = "text";
        icon.innerText = "visibility_off";
      }} else {{
        el.type = "password";
        icon.innerText = "visibility";
      }}
    }}

    async function saveConfiguration() {{
      compileCron();
      const payload = {{
        source_dev: document.getElementById("cfg-source-dev").value,
        target_dir: document.getElementById("cfg-target-dir").value,
        retention_count: parseInt(document.getElementById("cfg-retention").value, 10),
        schedule_enabled: document.getElementById("cfg-sched-enabled").checked,
        backup_cron: document.getElementById("cfg-backup-cron").value,
        backup_password: document.getElementById("cfg-backup-password").value,
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

    async function handleBackupClick() {{
      const btn = document.getElementById("btn-run-backup");
      if (btn.dataset.state === "running") {{
        if (confirm("Are you sure you want to stop the active backup? The incomplete file will be deleted immediately.")) {{
          btn.disabled = true;
          showToast("Cancelling backup and cleaning up...");
          await fetch(basePath + "/api/stop", {{ method: "POST" }});
          setTimeout(() => {{
            fetchStatus();
            refreshBackupTree();
          }}, 1200);
        }}
      }} else {{
        triggerAction('backup');
        setTimeout(() => {{
          fetchStatus();
          refreshBackupTree();
        }}, 1200);
      }}
    }}

    let currentTreePackages = [];
    let previousBackupState = "Idle";

    async function refreshBackupTree() {{
      const curDir = (document.getElementById("cfg-target-dir").value || "{target_dir}").trim();
      await loadBackupTree(curDir);
      showToast(curDir ? "Refreshed backup packages" : "No destination selected");
    }}

    async function loadBackupTree(dirPath) {{
      const container = document.getElementById("backup-tree-container");
      const label = document.getElementById("tree-active-dir");
      if (label) label.innerText = dirPath || "";

      if (!dirPath) {{
        container.innerHTML = `
          <div style="text-align: center; color: var(--md-on-surface-variant); padding: 1.5rem; background: #15161A; border-radius: 12px;">
            No destination selected yet. Please choose a storage target in the <b>Storage &amp; Schedule Config</b> tab.
          </div>
        `;
        updateSelectedCount();
        return;
      }}

      try {{
        const res = await fetch(basePath + "/api/backups?dir=" + encodeURIComponent(dirPath));
        const data = await res.json();
        currentTreePackages = data.packages || [];

        if (currentTreePackages.length === 0) {{
          container.innerHTML = `
            <div style="text-align: center; color: var(--md-on-surface-variant); padding: 1.5rem; background: #15161A; border-radius: 12px;">
              No backup packages found in <code style="color: #FFF;">${{dirPath}}</code>
            </div>
          `;
          updateSelectedCount();
          return;
        }}

        let html = "";
        currentTreePackages.forEach(pkg => {{
          const allFileNames = pkg.files.map(f => f.name).join(",");
          html += `
            <div class="m3-card" style="padding: 1rem 1.25rem; background: #16181D; border: 1px solid var(--md-outline-variant);" id="pkg-${{pkg.id}}">
              <div style="display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; flex-wrap: wrap;">
                <div style="display: flex; align-items: center; gap: 0.75rem;">
                  <input type="checkbox" class="pkg-checkbox" data-files="${{allFileNames}}" style="width: 17px; height: 17px; cursor: pointer;" onchange="updateSelectedCount()">
                  <span class="material-symbols-outlined" style="color: var(--google-blue); font-size: 24px;">archive</span>
                  <div>
                    <div style="font-weight: 700; font-size: 0.95rem;">Backup Set: ${{pkg.date}}</div>
                    <div style="font-size: 0.78rem; color: var(--md-on-surface-variant);">
                      Total: <b>${{pkg.total_size}}</b> &bull; ${{pkg.files.length}} artifacts (image, hash, log, scripts)
                    </div>
                  </div>
                </div>

                <div style="display: flex; align-items: center; gap: 0.4rem;">
                  <button class="m3-button btn-tonal" style="height: 30px; font-size: 0.78rem; padding: 0 0.65rem;" onclick="toggleTreeCollapse('${{pkg.id}}')">
                    <span class="material-symbols-outlined" style="font-size: 16px;">unfold_more</span> Files
                  </button>
                  <button class="m3-button btn-tonal" style="height: 30px; padding: 0 0.6rem; color: #EA4335;" title="Delete this entire backup set" onclick="deleteSinglePackage('${{pkg.id}}', '${{allFileNames}}')">
                    <span class="material-symbols-outlined" style="font-size: 16px;">delete</span>
                  </button>
                </div>
              </div>

              <!-- COLLAPSIBLE TREE NODES -->
              <div id="tree-files-${{pkg.id}}" style="display: none; margin-top: 0.75rem; padding-top: 0.75rem; border-top: 1px dashed var(--md-outline-variant); flex-direction: column; gap: 0.4rem;">
                ${{pkg.files.map(f => `
                  <div style="display: flex; align-items: center; justify-content: space-between; padding: 0.3rem 0.5rem; background: #0E0F12; border-radius: 8px; font-size: 0.82rem;">
                    <div style="display: flex; align-items: center; gap: 0.5rem; overflow: hidden; text-overflow: ellipsis;">
                      <span class="material-symbols-outlined" style="font-size: 16px; color: var(--md-outline);">subdirectory_arrow_right</span>
                      ${{f.name.endsWith(".log") 
                        ? `<span style="font-family: monospace; cursor: pointer; color: var(--md-primary); text-decoration: underline;" onclick="viewLogModal('${{f.name}}')">${{f.name}}</span>` 
                        : `<span style="font-family: monospace;">${{f.name}}</span>`}}
                      <span style="color: var(--md-on-surface-variant); font-size: 0.75rem;">(${{f.size}})</span>
                    </div>
                    <div style="display: flex; gap: 0.35rem; align-items: center;">
                      ${{f.name.endsWith(".log") ? `<button class="m3-button btn-tonal" style="height: 24px; padding: 0 0.5rem; font-size: 0.72rem;" onclick="viewLogModal('${{f.name}}')">View</button>` : ''}}
                      ${{(f.name.endsWith(".img.gz") || f.name.endsWith(".img.gz.enc")) ? `<button class="m3-button btn-tonal" style="height: 24px; padding: 0 0.5rem; font-size: 0.72rem; color: var(--google-yellow);" onclick="openBurnModal('${{f.name}}')">Burn Spare</button>` : ''}}
                      <a href="${{basePath}}/api/download?file=${{encodeURIComponent(f.name)}}" class="m3-button btn-tonal" style="height: 24px; padding: 0 0.5rem; font-size: 0.72rem; text-decoration: none;">Download</a>
                      ${{(f.name.endsWith(".img.gz") || f.name.endsWith(".img.gz.enc")) ? `<button class="m3-button btn-tonal" style="height: 24px; padding: 0 0.5rem; font-size: 0.72rem;" onclick="verifyArchive('${{f.name}}')">Test</button>` : ''}}
                      ${{f.hash ? `<button class="m3-button btn-tonal" style="height: 24px; padding: 0 0.4rem;" onclick="copyText('${{f.hash}}')"><span class="material-symbols-outlined" style="font-size: 13px;">content_copy</span></button>` : ''}}
                    </div>
                  </div>
                `).join('')}}
              </div>
            </div>
          `;
        }});
        container.innerHTML = html;
        updateSelectedCount();
      }} catch (e) {{
        container.innerHTML = `<div style="color: var(--google-red); font-size: 0.85rem; text-align: center;">Failed to scan directory for backups.</div>`;
      }}
    }}

    function toggleTreeCollapse(pkgId) {{
      const el = document.getElementById("tree-files-" + pkgId);
      if (el) {{
        el.style.display = (el.style.display === "none" ? "flex" : "none");
      }}
    }}

    function toggleSelectAll(checked) {{
      document.querySelectorAll(".pkg-checkbox").forEach(cb => cb.checked = checked);
      updateSelectedCount();
    }}

    function updateSelectedCount() {{
      const checkedBoxes = document.querySelectorAll(".pkg-checkbox:checked");
      const count = checkedBoxes.length;
      document.getElementById("selected-count-label").innerText = count + " selected";
      document.getElementById("btn-delete-selected").disabled = (count === 0);
    }}

    async function deleteSinglePackage(pkgId, fileNamesStr) {{
      if (!confirm("Are you sure you want to permanently delete all files in this backup set?")) return;
      const curDir = document.getElementById("cfg-target-dir").value || "{target_dir}";
      const files = fileNamesStr.split(",");
      await executeDelete(curDir, files);
    }}

    async function deleteSelectedBackups() {{
      const checkedBoxes = document.querySelectorAll(".pkg-checkbox:checked");
      if (checkedBoxes.length === 0) return;
      if (!confirm(`Are you sure you want to permanently delete ${{checkedBoxes.length}} backup set(s)?`)) return;

      const curDir = document.getElementById("cfg-target-dir").value || "{target_dir}";
      const filesToDelete = [];
      checkedBoxes.forEach(cb => {{
        const fList = cb.getAttribute("data-files").split(",");
        fList.forEach(f => filesToDelete.push(f));
      }});
      await executeDelete(curDir, filesToDelete);
    }}

    async function executeDelete(dirPath, fileList) {{
      try {{
        showToast("Deleting artifacts...");
        const res = await fetch(basePath + "/api/backups/delete", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ directory: dirPath, files: fileList }})
        }});
        const data = await res.json();
        showToast(data.message || "Deletion complete.");
        loadBackupTree(dirPath);
        fetchStorageTargets();
      }} catch (e) {{
        alert("Failed to delete files: " + e);
      }}
    }}

    let selectedBurnArchive = "";

    async function openBurnModal(filename) {{
      selectedBurnArchive = filename;
      document.getElementById("burn-target-file").innerText = filename;
      document.getElementById("burn-modal").style.display = "flex";

      const passWrapper = document.getElementById("burn-pass-wrapper");
      passWrapper.style.display = filename.endsWith(".enc") ? "flex" : "none";

      const select = document.getElementById("burn-disk-select");
      select.innerHTML = '<option value="">Loading connected drives...</option>';

      try {{
        const res = await fetch(basePath + "/api/system/disks");
        const data = await res.json();
        const detectedBoot = data.boot_dev || "";

        select.innerHTML = '<option value="">-- Choose Spare Target Disk --</option>';
        let eligibleCount = 0;
        
        data.disks.forEach(d => {{
          // Exclude boot drives, RAM disks (zram/ram/loop), and drives smaller than 1 GB
          const isRamDisk = /(zram|ram|loop|dm-)/.test(d.path);
          if (!d.is_boot && d.path !== detectedBoot && !isRamDisk && d.bytes >= 1073741824) {{
            const opt = document.createElement("option");
            opt.value = d.path;
            opt.innerText = `${{d.path}} (${{d.size}} - ${{d.model}})`;
            select.appendChild(opt);
            eligibleCount++;
          }}
        }});

        if (eligibleCount === 0) {{
          select.innerHTML = '<option value="">No eligible spare drive found (connect USB drive &ge; 1 GB)</option>';
        }}
      }} catch (e) {{
        select.innerHTML = '<option value="">Failed to discover disks</option>';
      }}
    }}

    function closeBurnModal() {{
      document.getElementById("burn-modal").style.display = "none";
    }}

    async function executeBurn() {{
      const targetDev = document.getElementById("burn-disk-select").value;
      const passphrase = document.getElementById("burn-password-input").value;

      if (!targetDev) {{
        alert("Please select a target disk device.");
        return;
      }}

      if (!confirm("Are you 100% sure you want to completely wipe " + targetDev + " and restore " + selectedBurnArchive + "?")) return;

      try {{
        showToast("Flashing spare drive started in background...");
        closeBurnModal();
        const res = await fetch(basePath + "/api/burn-spare", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{
            archive: selectedBurnArchive,
            target_dev: targetDev,
            passphrase: passphrase
          }})
        }});
        const data = await res.json();
        showToast(data.message);
        setTimeout(fetchStatus, 400);
      }} catch (e) {{
        alert("Burn request failed: " + e);
      }}
    }}

    async function fetchStatus() {{
      try {{
        const res = await fetch(basePath + "/api/status");
        const data = await res.json();

        // Auto-refresh the file tree whenever a backup finishes or is cancelled/stopped
        if (previousBackupState === "Running" && data.status !== "Running") {{
          refreshBackupTree();
        }}
        previousBackupState = data.status;

        const btn = document.getElementById("btn-run-backup");
        const pBar = document.getElementById("global-progress");
        const pFill = document.getElementById("global-progress-fill");

        if (data.status === "Running") {{
          btn.dataset.state = "running";
          pBar.style.display = "block";
          pFill.style.width = data.progress;

          const isFlashing = (data.speed && data.speed.toLowerCase().includes("flash")) || (data.eta && data.eta.toLowerCase().includes("sector"));

          if (isFlashing) {{
            btn.disabled = true;
            btn.style.background = "var(--google-yellow)";
            btn.style.color = "#000000";
            btn.innerHTML = '<span class="material-symbols-outlined">local_fire_department</span> Flashing Spare...';
            pFill.style.background = "linear-gradient(90deg, var(--google-yellow), var(--google-red))";
            document.getElementById("backup-status-text").innerHTML =
              `<span style="color: var(--google-yellow); font-weight: 700;">[${{data.progress}}] ${{data.speed}}</span> &bull; ${{data.eta}}`;
          }} else {{
            btn.disabled = false;
            btn.style.color = "#FFFFFF";
            btn.style.background = "var(--google-red)";
            btn.innerHTML = '<span class="material-symbols-outlined">stop_circle</span> Stop Backup';

            const numProg = parseInt(data.progress, 10) || 0;
            if (numProg >= 86) {{
              pFill.style.background = "linear-gradient(90deg, var(--google-yellow), #FDD663)";
              document.getElementById("backup-status-text").innerHTML =
                `<span style="color: var(--google-yellow); font-weight: 700;">[${{data.progress}}] ${{data.speed || "Post-Processing"}}</span> &bull; ${{data.eta || "Finalizing archive on storage..."}}`;
            }} else {{
              pFill.style.background = "linear-gradient(90deg, var(--google-blue), #8AB4F8)";
              document.getElementById("backup-status-text").innerHTML =
                `Streaming: ${{data.progress}} (${{data.speed || '0 MB/s'}} &bull; ${{data.eta || 'calculating'}})`;
            }}
          }}
        }} else {{
          btn.dataset.state = "idle";
          btn.disabled = false;
          btn.style.color = "#FFFFFF";
          btn.style.background = "var(--google-blue)";
          btn.innerHTML = '<span class="material-symbols-outlined">play_arrow</span> Start Backup Now';
          pBar.style.display = "none";
          pFill.style.width = "0%";
          document.getElementById("backup-status-text").innerText = "Status: " + data.status;
        }}

        // Reflect schedule status dynamically, prioritizing local toggle state
        if (data.cron_summary) {{
          const schedCheckbox = document.getElementById("cfg-sched-enabled");
          const isEnabled = schedCheckbox ? schedCheckbox.checked : (data.schedule_enabled !== false && data.cron_expression !== "Disabled");
          updateScheduleDisplayUI(data.cron_summary, data.cron_expression, isEnabled);
        }}

        document.getElementById("val-wear").innerText = data.wear;
        document.getElementById("val-health").innerText = data.health;
        const tempEl = document.getElementById("val-temp");
        if (tempEl && data.soc_temp) {{
          tempEl.innerText = data.soc_temp;
          const numericTemp = parseInt(data.soc_temp, 10);
          if (!isNaN(numericTemp)) {{
            tempEl.style.color = numericTemp >= 75 ? "var(--google-red)" : (numericTemp >= 65 ? "var(--google-yellow)" : "var(--google-green)");
          }}
        }}
        const consoleEl = document.getElementById("console-output");
        if (consoleEl) {{
          consoleEl.innerText = data.log || "No log transactions.";
          consoleEl.scrollTop = consoleEl.scrollHeight;
        }}

        // Continuous hotplug & unplug detection: refresh storage targets while on config tab
        const configTab = document.getElementById("tab-content-config");
        if (configTab && configTab.style.display !== "none") {{
          fetchStorageTargets();
        }}
      }} catch (e) {{
        console.error("Status polling failed", e);
      }}
    }}

    async function viewLogModal(filename) {{
      const modal = document.getElementById("log-modal");
      const title = document.getElementById("log-modal-title");
      const content = document.getElementById("log-modal-content");
      title.innerText = filename;
      content.innerText = "Loading log content...";
      modal.style.display = "flex";

      try {{
        const res = await fetch(basePath + "/api/download?file=" + encodeURIComponent(filename));
        if (res.ok) {{
          const text = await res.text();
          content.innerText = text || "Log file is empty.";
        }} else {{
          content.innerText = "Failed to load log file contents.";
        }}
      }} catch (e) {{
        content.innerText = "Error loading log: " + e;
      }}
    }}

    function closeLogModal() {{
      document.getElementById("log-modal").style.display = "none";
    }}

    function copyModalLog() {{
      const text = document.getElementById("log-modal-content").innerText;
      navigator.clipboard.writeText(text).then(() => showToast("Log copied to clipboard."));
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

    // Initial boot load
    const initialSchedCb = document.getElementById("cfg-sched-enabled");
    if (initialSchedCb && !initialSchedCb.checked) {{
      toggleScheduleActive(false, false);
    }}
    fetchStatus();
    loadBackupTree(document.getElementById("cfg-target-dir").value || "{target_dir}");
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
                            for rm in raw_mounts:
                                m_usage = rm.get("usage", "")
                                # Only populate mounts configured with usage 'share'
                                if m_usage != "share":
                                    continue

                                m_name = rm.get("name", "Network Storage")
                                m_server = rm.get("server", "")
                                m_share = rm.get("share", "")
                                m_type = rm.get("type", "cifs").upper()
                                full_remote = f"//{m_server}/{m_share}" if (m_server and m_share) else m_name
                                share_path = f"/share/{m_name}"

                                tot, usd, fre, pct = get_mount_stats(share_path)
                                free_str = f"{usd / 1073741824:.1f} GB of {tot / 1073741824:.1f} GB used ({fre / 1073741824:.1f} GB free)" if tot > 0 else "Ready"

                                seen_paths.add(share_path)
                                targets.append({
                                    "name": m_name,
                                    "source": full_remote,
                                    "path": share_path,
                                    "type": f"SMB / {m_type}",
                                    "free": free_str,
                                    "pct_used": pct,
                                    "is_default": len(targets) == 0
                                })
                except Exception as ex:
                    print(f"[!] Supervisor /mounts exception: {ex}", file=sys.stderr)

            # Check for any existing subdirectories inside /share
            if os.path.exists("/share"):
                try:
                    for entry in os.listdir("/share"):
                        p = os.path.join("/share", entry)
                        if os.path.isdir(p) and p not in seen_paths:
                            tot, usd, fre, pct = get_mount_stats(p)
                            if tot > 0:
                                targets.append({
                                    "name": entry,
                                    "source": f"/share/{entry}",
                                    "path": p,
                                    "type": "Share Storage",
                                    "free": f"{usd / 1073741824:.1f} GB of {tot / 1073741824:.1f} GB used ({fre / 1073741824:.1f} GB free)",
                                    "pct_used": pct,
                                    "is_default": len(targets) == 0
                                })
                                seen_paths.add(p)
                except Exception:
                    pass

            # Dynamic USB storage scan across standard OS mount paths (hotplug support)
            for media_root in ["/media", "/run/media"]:
                if os.path.exists(media_root):
                    try:
                        for entry in os.listdir(media_root):
                            usb_path = os.path.join(media_root, entry)
                            if os.path.isdir(usb_path) and usb_path not in seen_paths:
                                tot, usd, fre, pct = get_mount_stats(usb_path)
                                if tot > 0:
                                    targets.append({
                                        "name": f"USB Storage ({entry})",
                                        "source": usb_path,
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

            if filename.endswith(".enc"):
                passphrase = cfg.get("backup_password", "")
                if not passphrase:
                    self.send_json({"message": "✖ Passphrase required in Settings to test encrypted archive."}, status=422)
                    return
                verify_env = os.environ.copy()
                verify_env["VERIFY_PASS"] = passphrase
                verify_cmd = f'openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 -pass env:VERIFY_PASS -in "{full_path}" | pigz -t'
                cmd = subprocess.run(verify_cmd, shell=True, capture_output=True, text=True, env=verify_env)
            else:
                cmd = subprocess.run(["pigz", "-t", full_path], capture_output=True, text=True)

            if cmd.returncode == 0:
                self.send_json({"message": f"✔ Verification Passed: {filename} integrity is 100% OK (zero corruption)."})
            else:
                self.send_json({"message": f"✖ Verification failed on {filename}: Invalid passphrase or corrupted blocks."}, status=422)
            return

        elif path == "/api/system/disks":
            disks = []
            boot_dev = ""
            
            # Resolve actual active boot device from mounted root/data partitions
            for mnt in ["/config", "/data", "/"]:
                try:
                    src_cmd = subprocess.run(["findmnt", "-n", "-o", "SOURCE", mnt], capture_output=True, text=True)
                    src_out = src_cmd.stdout.strip()
                    if src_out:
                        pk_cmd = subprocess.run(["lsblk", "-n", "-o", "PKNAME", src_out], capture_output=True, text=True)
                        pk_out = pk_cmd.stdout.strip()
                        if pk_out:
                            boot_dev = f"/dev/{pk_out.replace('/dev/', '')}"
                            break
                except Exception:
                    pass
            if not boot_dev:
                for cand in ["/dev/mmcblk0", "/dev/sda", "/dev/nvme0n1"]:
                    if os.path.exists(cand):
                        boot_dev = cand
                        break

            try:
                cmd = subprocess.run(["lsblk", "-d", "-b", "-n", "-o", "NAME,SIZE,TYPE,MODEL,TRAN,ROTA"], capture_output=True, text=True)
                for line in cmd.stdout.strip().split("\n"):
                    parts = line.split()
                    if len(parts) >= 3 and parts[2] == "disk":
                        d_name = parts[0]
                        
                        # Discard virtual and RAM block devices (zram, ram, loop, dm)
                        if any(d_name.startswith(p) for p in ["zram", "ram", "loop", "dm-"]):
                            continue
                            
                        bytes_sz = int(parts[1]) if parts[1].isdigit() else 0
                        
                        # Discard devices smaller than 1 GB (1,073,741,824 bytes)
                        if bytes_sz < 1073741824:
                            continue
                            
                        human_sz = f"{bytes_sz / 1073741824:.1f} GB"
                        model = parts[3] if len(parts) > 3 else "Storage Block"
                        tran = parts[4] if len(parts) > 4 else "Bus"
                        rota = parts[5] == "1" if len(parts) > 5 else False
                        dev_path = f"/dev/{d_name}"
                        is_boot = (dev_path == boot_dev)
                        
                        disks.append({
                            "path": dev_path,
                            "bytes": bytes_sz,
                            "size": human_sz,
                            "model": model,
                            "tran": tran,
                            "rota": rota,
                            "is_boot": is_boot
                        })
            except Exception:
                pass
            self.send_json({"disks": disks, "boot_dev": boot_dev})
            return

        elif path == "/api/system/probe":
            qs = parse_qs(parsed.query)
            target = qs.get("dir", ["/backup"])[0].rstrip("/")
            try:
                os.makedirs(target, exist_ok=True)
                probe_file = os.path.join(target, ".probe_test")
                with open(probe_file, "w") as f:
                    f.write("test")
                os.remove(probe_file)
                total, used, free, pct = get_mount_stats(target)
                self.send_json({"message": f"✔ Reachable & Writable. Free capacity: {free / 1073741824:.2f} GB."})
            except Exception as ex:
                self.send_json({"message": f"✖ Write failed on '{target}': {str(ex)}"}, status=500)
            return

        elif path == "/api/backups":
            qs = parse_qs(parsed.query)
            scan_dir = qs.get("dir", [target_dir])[0].rstrip("/")
            packages = scan_backup_packages(scan_dir)
            self.send_json({"directory": scan_dir, "packages": packages})
            return

        elif path == "/api/status":
            wear_out = "N/A"
            health_out = "Unsupported"
            soc_temp = "N/A"
            try:
                t_cmd = subprocess.run(["/run.sh", "--soc-temp"], capture_output=True, text=True, timeout=2)
                t_val = t_cmd.stdout.strip()
                if t_val and t_val != "0":
                    soc_temp = f"{t_val}°C"
            except Exception:
                pass
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
            burn_log_file = "/var/run/burn_spare.log"
            backup_logs = sorted(glob.glob(f"{target_dir}/haos_backup_*.log"), key=os.path.getmtime, reverse=True)
            latest_backup_log = backup_logs[0] if backup_logs else None

            # Prioritize active or recent burn log over backup archives
            if os.path.exists(burn_log_file) and (not latest_backup_log or os.path.getmtime(burn_log_file) >= os.path.getmtime(latest_backup_log)):
                try:
                    with open(burn_log_file, "r", encoding="utf-8", errors="ignore") as bf:
                        latest_log = bf.read()
                except Exception:
                    latest_log = "Unable to read burn log stream."
            elif latest_backup_log and os.path.exists(latest_backup_log):
                try:
                    with open(latest_backup_log, "r", encoding="utf-8", errors="ignore") as f:
                        latest_log = f.read()
                except Exception:
                    latest_log = "Unable to read active log stream."

            status_val = "Idle"
            prog_val = "0%"
            speed_val = ""
            eta_val = ""
            if is_backup_active():
                status_val = "Running"
                prog_file = "/var/run/sd_backup.progress"
                if os.path.exists(prog_file):
                    try:
                        with open(prog_file, "r") as pf:
                            raw_prog = pf.read().strip()
                            if raw_prog:
                                p_parts = raw_prog.split("|")
                                prog_val = p_parts[0] if len(p_parts) > 0 and p_parts[0] else "0%"
                                if len(p_parts) > 1:
                                    speed_val = p_parts[1]
                                if len(p_parts) > 2:
                                    eta_val = p_parts[2]
                    except Exception:
                        pass

            # Humanize active cron expression for the UI
            b_cron = cfg.get("backup_cron", "0 3 * * 0")
            cron_parts = b_cron.strip().split()
            cron_summary = "Custom Schedule"
            day_map = {"0": "Sunday", "1": "Monday", "2": "Tuesday", "3": "Wednesday", "4": "Thursday", "5": "Friday", "6": "Saturday", "7": "Sunday"}
            if len(cron_parts) == 5:
                mm, hh = cron_parts[0].zfill(2), cron_parts[1].zfill(2)
                if cron_parts[2] == "*" and cron_parts[3] == "*":
                    if cron_parts[4] == "*":
                        cron_summary = f"Daily at {hh}:{mm}"
                    elif cron_parts[4] in day_map:
                        cron_summary = f"Weekly on {day_map[cron_parts[4]]} at {hh}:{mm}"
                    else:
                        days = [day_map.get(d, d) for d in cron_parts[4].split(",") if d in day_map]
                        cron_summary = f"Weekly on {', '.join(days)} at {hh}:{mm}"
                elif cron_parts[2] == "1" and cron_parts[3] == "*" and cron_parts[4] == "*":
                    cron_summary = f"Monthly on 1st at {hh}:{mm}"

            sched_is_on = cfg.get("schedule_enabled", True)
            if not sched_is_on:
                cron_summary = "Disabled (Manual Only)"

            self.send_json({
                "status": status_val,
                "progress": prog_val,
                "speed": speed_val,
                "eta": eta_val,
                "soc_temp": soc_temp,
                "wear": wear_out,
                "health": health_out,
                "artifacts": artifacts[:15],
                "log": latest_log[-8000:],
                "schedule_enabled": sched_is_on,
                "cron_expression": b_cron if sched_is_on else "Disabled",
                "cron_summary": cron_summary
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

        elif path == "/api/backups/delete":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")
                req = json.loads(body)
                cfg = get_config()
                default_target = cfg.get("target_dir", "/backup").rstrip("/")
                base_dir = req.get("directory", default_target).rstrip("/")
                files_to_delete = req.get("files", [])
                deleted_count = 0

                for fname in files_to_delete:
                    safe_name = os.path.basename(fname)
                    # Allow deletion of backup images, hashes, logs, and companion restore scripts
                    if safe_name.startswith("haos_backup_") or safe_name.startswith("restore_"):
                        full_p = os.path.join(base_dir, safe_name)
                        if os.path.exists(full_p):
                            os.remove(full_p)
                            deleted_count += 1

                self.send_json({"message": f"Successfully deleted {deleted_count} artifact(s)."})
            except Exception as ex:
                self.send_json({"message": f"Deletion failed: {str(ex)}"}, status=500)
            return

        elif path == "/api/backup":
            cfg = get_config()
            if not cfg.get("target_dir"):
                self.send_json({"message": "Please select a storage destination in Tab 2 before starting a backup."}, status=400)
                return
            if is_backup_active():
                self.send_json({"message": "A backup process is already actively executing."}, status=409)
                return
            subprocess.Popen(["/run.sh", "--backup"])
            self.send_json({"message": "Disk backup process successfully started in background."})
            return

        elif path == "/api/stop":
            subprocess.Popen(["/run.sh", "--stop"])
            self.send_json({"message": "Stop signal sent. Terminating and purging incomplete files."})
            return

        elif path == "/api/burn-spare":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")
                req = json.loads(body)
                archive = req.get("archive", "")
                target_dev = req.get("target_dev", "")
                passphrase = req.get("passphrase", "")

                # Extra server-side safety check: reject empty, invalid, RAM, or undersized block devices
                if not target_dev or not os.path.exists(target_dev):
                    self.send_json({"message": "Invalid or disconnected target block device."}, status=400)
                    return

                base_dev_name = os.path.basename(target_dev)
                if any(base_dev_name.startswith(p) for p in ["zram", "ram", "loop", "dm-"]):
                    self.send_json({"message": f"Target {target_dev} is a virtual/RAM device and cannot be used."}, status=400)
                    return

                try:
                    dev_sz = int(subprocess.run(["blockdev", "--getsize64", target_dev], capture_output=True, text=True).stdout.strip() or 0)
                    if dev_sz < 1073741824:
                        self.send_json({"message": f"Target drive {target_dev} has less than 1 GB capacity and cannot be flashed."}, status=400)
                        return
                except Exception:
                    pass

                cfg = get_config()
                default_target = cfg.get("target_dir", "/backup").rstrip("/")
                full_archive_path = os.path.join(default_target, os.path.basename(archive))

                if not os.path.exists(full_archive_path):
                    self.send_json({"message": "Archive file does not exist on disk."}, status=404)
                    return

                subprocess.Popen(["/run.sh", "--burn-spare", full_archive_path, target_dev, passphrase])
                self.send_json({"message": f"Flashing cold spare on {target_dev} started! Check logs for real-time progress."})
            except Exception as ex:
                self.send_json({"message": f"Burn failed: {str(ex)}"}, status=500)
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