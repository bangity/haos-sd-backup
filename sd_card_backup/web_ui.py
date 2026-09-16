#!/usr/bin/env python3
import os
import sys
import json
import subprocess
import glob
import shutil
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
    cfg = DEFAULT_CONFIG.copy()
    if os.path.exists(OPTIONS_FILE):
        try:
            with open(OPTIONS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                cfg.update(data)
        except Exception:
            pass
    return cfg

def save_config(new_opts):
    cfg = get_config()
    cfg.update(new_opts)
    os.makedirs(os.path.dirname(OPTIONS_FILE), exist_ok=True)
    with open(OPTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    return cfg

class EnterpriseMaterialHandler(BaseHTTPRequestHandler):
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
  <title>HAOS Hardware Recovery &amp; Storage Suite</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&family=Google+Sans:wght@400;500;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@24,400,0,0" />
  <style>
    :root {{
      --md-primary: #8AB4F8;
      --md-on-primary: #002A5A;
      --md-primary-container: #1A73E8;
      --md-surface: #131316;
      --md-surface-container: #1E1F24;
      --md-surface-container-high: #2B2D33;
      --md-surface-container-highest: #373940;
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

    /* --- GENERATED VECTOR HERO BANNER --- */
    .hero-banner {{
      position: relative;
      background: linear-gradient(135deg, #0D214F 0%, #153E7E 50%, #1A56A6 100%);
      border-radius: 28px;
      padding: 2.25rem 2.5rem;
      overflow: hidden;
      display: flex;
      justify-content: space-between;
      align-items: center;
      border: 1px solid rgba(255, 255, 255, 0.12);
      box-shadow: 0 16px 40px rgba(0, 0, 0, 0.45);
    }}
    .hero-svg-bg {{
      position: absolute;
      top: 0; right: 0; bottom: 0; left: 0;
      width: 100%; height: 100%;
      pointer-events: none;
      opacity: 0.22;
    }}
    .hero-content {{ position: relative; z-index: 2; max-width: 680px; }}
    .hero-title {{
      font-family: 'Google Sans', sans-serif;
      font-size: 1.85rem;
      font-weight: 700;
      letter-spacing: -0.02em;
      color: #FFFFFF;
      display: flex;
      align-items: center;
      gap: 0.85rem;
    }}
    .hero-subtitle {{
      margin-top: 0.4rem;
      font-size: 0.95rem;
      color: #D2E3FC;
      opacity: 0.95;
    }}
    .hero-badges {{ display: flex; gap: 0.65rem; margin-top: 1.25rem; flex-wrap: wrap; }}
    .hero-chip {{
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      padding: 0.35rem 0.85rem;
      border-radius: 14px;
      font-size: 0.8rem;
      font-weight: 500;
      background: rgba(255, 255, 255, 0.12);
      backdrop-filter: blur(12px);
      color: #FFFFFF;
      border: 1px solid rgba(255, 255, 255, 0.2);
    }}

    /* --- STATS & CARDS --- */
    .grid-stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 1rem; }}
    .m3-card {{
      background: var(--md-surface-container);
      border-radius: 20px;
      border: 1px solid var(--md-outline-variant);
      padding: 1.35rem;
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
      position: relative;
      overflow: hidden;
      box-shadow: 0 4px 16px rgba(0,0,0,0.2);
    }}
    .m3-card-accent {{ position: absolute; top: 0; left: 0; right: 0; height: 4px; }}
    .stat-label {{ font-size: 0.78rem; font-weight: 500; color: var(--md-on-surface-variant); text-transform: uppercase; letter-spacing: 0.05em; }}
    .stat-value {{ font-family: 'Google Sans', 'Roboto', sans-serif; font-size: 1.6rem; font-weight: 600; }}

    /* --- BUTTONS & ACTIONS --- */
    .actions-shelf {{ display: flex; gap: 0.75rem; flex-wrap: wrap; }}
    .m3-button {{
      display: inline-flex;
      align-items: center;
      gap: 0.55rem;
      height: 44px;
      padding: 0 1.35rem;
      border-radius: 22px;
      font-family: 'Google Sans', 'Roboto', sans-serif;
      font-size: 0.92rem;
      font-weight: 500;
      border: none;
      cursor: pointer;
      transition: all 0.2s cubic-bezier(0.2, 0, 0, 1);
    }}
    .btn-filled {{ background: var(--google-blue); color: #FFFFFF; }}
    .btn-filled:hover {{ filter: brightness(1.1); box-shadow: 0 4px 14px rgba(66, 133, 244, 0.45); }}
    .btn-tonal {{ background: var(--md-surface-container-high); color: var(--md-on-surface); border: 1px solid var(--md-outline-variant); }}
    .btn-tonal:hover {{ background: var(--md-surface-container-highest); }}
    .btn-config {{ background: linear-gradient(135deg, #1E8E3E 0%, #34A853 100%); color: #FFFFFF; box-shadow: 0 4px 14px rgba(52, 168, 83, 0.35); }}
    .btn-config:hover {{ filter: brightness(1.12); }}

    /* --- PROGRESS --- */
    .progress-bar-container {{
      width: 100%;
      height: 8px;
      background: var(--md-surface-container-high);
      border-radius: 4px;
      overflow: hidden;
      display: none;
    }}
    .progress-bar-fill {{
      height: 100%;
      width: 0%;
      background: linear-gradient(90deg, var(--google-blue), #A8C7FA);
      border-radius: 4px;
      transition: width 0.35s ease;
    }}

    /* --- TABLE & CONSOLE --- */
    .data-table {{ width: 100%; border-collapse: collapse; margin-top: 0.5rem; }}
    .data-table th {{
      text-align: left;
      font-size: 0.75rem;
      text-transform: uppercase;
      color: var(--md-on-surface-variant);
      padding: 0.85rem 0.6rem;
      border-bottom: 1px solid var(--md-outline-variant);
    }}
    .data-table td {{
      padding: 0.85rem 0.6rem;
      font-size: 0.88rem;
      border-bottom: 1px solid var(--md-outline-variant);
    }}
    pre.console-box {{
      background: #0D0E11;
      border: 1px solid var(--md-outline-variant);
      border-radius: 14px;
      padding: 1.15rem;
      font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
      font-size: 0.83rem;
      line-height: 1.5;
      color: #CFD3DC;
      max-height: 360px;
      overflow-y: auto;
      white-space: pre-wrap;
      word-break: break-all;
    }}

    /* --- MATERIAL MODAL SYSTEM --- */
    .dialog-overlay {{
      display: none;
      position: fixed;
      top: 0; left: 0; width: 100vw; height: 100vh;
      background: rgba(0, 0, 0, 0.78);
      backdrop-filter: blur(6px);
      align-items: center;
      justify-content: center;
      z-index: 1000;
    }}
    .m3-dialog {{
      background: var(--md-surface-container);
      border-radius: 28px;
      width: 94%;
      max-width: 760px;
      max-height: 90vh;
      display: flex;
      flex-direction: column;
      box-shadow: 0 24px 64px rgba(0, 0, 0, 0.65);
      border: 1px solid var(--md-outline-variant);
      overflow: hidden;
      animation: dialogZoom 0.2s cubic-bezier(0.1, 0.9, 0.2, 1);
    }}
    @keyframes dialogZoom {{
      from {{ transform: scale(0.95); opacity: 0; }}
      to {{ transform: scale(1); opacity: 1; }}
    }}
    .dialog-header {{
      padding: 1.4rem 1.85rem;
      border-bottom: 1px solid var(--md-outline-variant);
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: var(--md-surface-container-high);
    }}
    .dialog-tabs {{
      display: flex;
      gap: 0.5rem;
      padding: 0.65rem 1.85rem;
      background: var(--md-surface-container-high);
      border-bottom: 1px solid var(--md-outline-variant);
      overflow-x: auto;
    }}
    .tab-btn {{
      background: transparent;
      border: none;
      color: var(--md-on-surface-variant);
      padding: 0.5rem 1rem;
      font-size: 0.88rem;
      font-weight: 500;
      border-radius: 18px;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 0.4rem;
    }}
    .tab-btn.active {{
      background: var(--md-surface-container-highest);
      color: var(--md-primary);
    }}
    .dialog-body {{
      padding: 1.85rem;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 1.35rem;
    }}
    .dialog-footer {{
      padding: 1.15rem 1.85rem;
      border-top: 1px solid var(--md-outline-variant);
      display: flex;
      justify-content: flex-end;
      gap: 0.85rem;
      background: var(--md-surface-container-high);
    }}

    /* --- FORM CONTROLS & DRIVE CARDS --- */
    .form-group {{ display: flex; flex-direction: column; gap: 0.45rem; }}
    .form-label {{ font-size: 0.9rem; font-weight: 500; color: var(--md-on-surface); display: flex; align-items: center; gap: 0.4rem; }}
    .form-desc {{ font-size: 0.8rem; color: var(--md-on-surface-variant); line-height: 1.4; }}
    .form-input, .form-select {{
      background: #141518;
      border: 1px solid var(--md-outline);
      color: #FFFFFF;
      padding: 0.75rem 1rem;
      border-radius: 10px;
      font-size: 0.92rem;
      outline: none;
      transition: border-color 0.2s;
    }}
    .form-input:focus, .form-select:focus {{
      border-color: var(--md-primary);
      box-shadow: 0 0 0 3px rgba(138, 180, 248, 0.25);
    }}

    .drive-cards-grid {{ display: grid; grid-template-columns: 1fr; gap: 0.75rem; margin-top: 0.35rem; }}
    .drive-card {{
      background: #15161A;
      border: 2px solid var(--md-outline-variant);
      border-radius: 14px;
      padding: 1rem 1.25rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
      cursor: pointer;
      transition: all 0.2s ease;
    }}
    .drive-card:hover {{ border-color: var(--md-primary); background: #1B1D23; }}
    .drive-card.selected {{
      border-color: var(--google-blue);
      background: rgba(66, 133, 244, 0.1);
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
      background: var(--md-primary-container);
      border-color: var(--md-primary);
      color: #FFFFFF;
    }}

    .guide-box {{
      background: rgba(66, 133, 244, 0.08);
      border-left: 4px solid var(--google-blue);
      padding: 1.15rem;
      border-radius: 0 10px 10px 0;
      font-size: 0.86rem;
      line-height: 1.5;
      color: #D2E3FC;
    }}
    .code-pill {{
      background: #0E0F12;
      padding: 0.2rem 0.5rem;
      border-radius: 6px;
      font-family: monospace;
      color: var(--md-primary);
      border: 1px solid var(--md-outline-variant);
    }}
    .toast-msg {{
      position: fixed;
      bottom: 24px;
      left: 50%;
      transform: translateX(-50%);
      background: #2D2F36;
      color: #FFFFFF;
      padding: 0.8rem 1.6rem;
      border-radius: 24px;
      font-size: 0.9rem;
      box-shadow: 0 8px 24px rgba(0,0,0,0.5);
      border: 1px solid var(--md-outline);
      z-index: 2000;
      display: none;
    }}
  </style>
</head>
<body>
  <div class="app-frame">

    <!-- CODE-GENERATED SVG HERO HEADER -->
    <div class="hero-banner">
      <svg class="hero-svg-bg" viewBox="0 0 850 320" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M-50,140 Q180,60 380,160 T850,90 L850,320 L-50,320 Z" fill="#FFFFFF" fill-opacity="0.9" />
        <circle cx="720" cy="90" r="160" stroke="#FFFFFF" stroke-width="2.5" stroke-dasharray="10 10" />
        <circle cx="720" cy="90" r="100" stroke="#FFFFFF" stroke-width="1.5" />
        <path d="M620,90 L820,90 M720,-10 L720,190" stroke="#FFFFFF" stroke-width="1.5" />
        <rect x="180" y="40" width="90" height="50" rx="10" stroke="#FFFFFF" stroke-width="1.5" stroke-dasharray="6 6" />
        <path d="M225,90 L225,120 M225,120 L380,160" stroke="#FFFFFF" stroke-width="1.5" />
      </svg>

      <div class="hero-content">
        <div class="hero-title">
          <span class="material-symbols-outlined" style="font-size: 34px; color: #8AB4F8;">hard_drive_2</span>
          HAOS Master Backup &amp; Storage Health
        </div>
        <div class="hero-subtitle">
          Commercial-grade live block sector replication, JEDEC hardware wear analytics, and companion disaster recovery automation.
        </div>
        <div class="hero-badges">
          <span class="hero-chip"><span class="material-symbols-outlined" style="font-size: 16px;">lock_open</span> Protection Mode OFF</span>
          <span class="hero-chip"><span class="material-symbols-outlined" style="font-size: 16px;">speed</span> Parallel pigz</span>
          <span class="hero-chip"><span class="material-symbols-outlined" style="font-size: 16px;">cloud_sync</span> Rclone 3-2-1 Ready</span>
          <span class="hero-chip"><span class="material-symbols-outlined" style="font-size: 16px;">terminal</span> GPT Relocation</span>
        </div>
      </div>

      <div style="z-index: 3;">
        <button class="m3-button btn-config" onclick="openConfigModal()">
          <span class="material-symbols-outlined">tune</span> Configure Suite
        </button>
      </div>
    </div>

    <!-- PROGRESS BAR -->
    <div class="progress-bar-container" id="global-progress">
      <div class="progress-bar-fill" id="global-progress-fill"></div>
    </div>

    <!-- METRICS OVERVIEW -->
    <div class="grid-stats">
      <div class="m3-card">
        <div class="m3-card-accent" style="background: var(--google-blue);"></div>
        <div class="stat-label">Backup Engine State</div>
        <div class="stat-value" id="val-status">--</div>
        <p id="sub-status" style="font-size: 0.8rem; color: var(--md-on-surface-variant);">Daemon Monitoring</p>
      </div>
      <div class="m3-card">
        <div class="m3-card-accent" style="background: var(--google-yellow);"></div>
        <div class="stat-label">Flash Memory Wear</div>
        <div class="stat-value" id="val-wear" style="color: var(--google-yellow);">--%</div>
        <p id="sub-wear" style="font-size: 0.8rem; color: var(--md-on-surface-variant);">Warning Limit: {cfg['safe_wear_threshold']}%</p>
      </div>
      <div class="m3-card">
        <div class="m3-card-accent" style="background: var(--google-green);"></div>
        <div class="stat-label">Hardware Life State</div>
        <div class="stat-value" id="val-health">--</div>
        <p id="sub-health" style="font-size: 0.8rem; color: var(--md-on-surface-variant);">Reserve Health Verified</p>
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

    <!-- ARTIFACTS TABLE -->
    <div class="m3-card">
      <span class="stat-label">Disaster Recovery Packages ({target_dir})</span>
      <table class="data-table">
        <thead>
          <tr>
            <th>Filename</th>
            <th>Archive Size</th>
            <th style="text-align: right;">Action</th>
          </tr>
        </thead>
        <tbody id="table-artifacts">
          <tr><td colspan="3" style="text-align: center; color: var(--md-on-surface-variant);">Scanning storage directory...</td></tr>
        </tbody>
      </table>
    </div>

    <!-- CONSOLE LOG -->
    <div class="m3-card">
      <div style="display: flex; align-items: center; justify-content: space-between;">
        <span class="stat-label">Live Execution Console</span>
        <button class="m3-button btn-tonal" style="height: 30px; padding: 0 0.85rem; font-size: 0.78rem;" onclick="copyConsoleLog()">
          <span class="material-symbols-outlined" style="font-size: 15px;">content_copy</span> Copy Log
        </button>
      </div>
      <pre class="console-box" id="console-output">Loading daemon buffer...</pre>
    </div>

  </div>

  <!-- MODAL: INTERACTIVE SETTINGS POPUP WITH TABS -->
  <div class="dialog-overlay" id="config-modal">
    <div class="m3-dialog">
      <div class="dialog-header">
        <div style="display: flex; align-items: center; gap: 0.5rem;">
          <span class="material-symbols-outlined" style="color: var(--md-primary);">tune</span>
          <h2 style="font-family: 'Google Sans', sans-serif; font-size: 1.25rem;">Suite Configuration</h2>
        </div>
        <span class="material-symbols-outlined" style="cursor: pointer;" onclick="closeConfigModal()">close</span>
      </div>

      <div class="dialog-tabs">
        <button class="tab-btn active" id="tab-btn-storage" onclick="switchConfigTab('storage')">
          <span class="material-symbols-outlined" style="font-size: 18px;">storage</span> Source &amp; Destination
        </button>
        <button class="tab-btn" id="tab-btn-schedule" onclick="switchConfigTab('schedule')">
          <span class="material-symbols-outlined" style="font-size: 18px;">calendar_month</span> Schedule &amp; Retention
        </button>
        <button class="tab-btn" id="tab-btn-cloud" onclick="switchConfigTab('cloud')">
          <span class="material-symbols-outlined" style="font-size: 18px;">cloud_sync</span> Rclone 3-2-1 Sync
        </button>
        <button class="tab-btn" id="tab-btn-alerts" onclick="switchConfigTab('alerts')">
          <span class="material-symbols-outlined" style="font-size: 18px;">notifications</span> Alerts &amp; Safety
        </button>
      </div>

      <div class="dialog-body">

        <!-- TAB 1: STORAGE & DRIVES -->
        <div id="tab-content-storage" style="display: flex; flex-direction: column; gap: 1.25rem;">
          <div class="form-group">
            <label class="form-label">
              <span class="material-symbols-outlined" style="font-size: 18px;">dns</span> Source Storage Drive
            </label>
            <p class="form-desc">Select the physical storage drive to clone. We inspect all block devices connected to your system:</p>
            <div class="drive-cards-grid" id="drive-cards-container">
              <div class="drive-card selected" onclick="selectDrive('auto')">
                <div>
                  <div style="font-weight: 500;">auto - Automatic Detection (Recommended)</div>
                  <div style="font-size: 0.78rem; color: var(--md-on-surface-variant);">Dynamically resolves the active OS boot disk</div>
                </div>
                <span class="material-symbols-outlined" style="color: var(--google-green);">auto_awesome</span>
              </div>
            </div>
            <input type="hidden" id="cfg-source-dev" value="{cfg['source_dev']}">
          </div>

          <div class="form-group">
            <label class="form-label">
              <span class="material-symbols-outlined" style="font-size: 18px;">folder</span> Target Storage Directory
            </label>
            <p class="form-desc">Destination SMB mount or local directory. Must have at least 15 GB free space.</p>
            <div style="display: flex; gap: 0.6rem;">
              <input type="text" class="form-input" style="flex: 1;" id="cfg-target-dir" value="{cfg['target_dir']}">
              <button class="m3-button btn-tonal" onclick="probeDirectory()">Test Access</button>
            </div>
            <div id="dir-probe-result" style="font-size: 0.8rem; display: none;"></div>
          </div>
        </div>

        <!-- TAB 2: SCHEDULE & RETENTION -->
        <div id="tab-content-schedule" style="display: none; flex-direction: column; gap: 1.25rem;">
          <div class="form-group">
            <label class="form-label"><span class="material-symbols-outlined" style="font-size: 18px;">schedule</span> Backup Frequency</label>
            <select class="form-select" id="cfg-sched-freq" onchange="handleFrequencyChange(this.value)">
              <option value="weekly">Weekly on Selected Days</option>
              <option value="daily">Daily at Fixed Time</option>
              <option value="monthly">Monthly (1st Day of Month)</option>
              <option value="custom">Advanced (Custom Cron String)</option>
            </select>
          </div>

          <div id="visual-sched-controls">
            <div class="form-group">
              <label class="form-label">Run Time (24h Clock)</label>
              <div style="display: flex; gap: 0.5rem; max-width: 260px;">
                <select class="form-select" id="cfg-sched-hour" style="flex: 1;"></select>
                <span style="align-self: center; font-weight: bold;">:</span>
                <select class="form-select" id="cfg-sched-min" style="flex: 1;">
                  <option value="00">00</option>
                  <option value="15">15</option>
                  <option value="30">30</option>
                  <option value="45">45</option>
                </select>
              </div>
            </div>

            <div class="form-group" id="group-sched-days" style="margin-top: 0.5rem;">
              <label class="form-label">Days of Week</label>
              <div class="filter-chip-group">
                <div class="filter-chip" data-day="0" onclick="toggleDayChip(this)">Sun</div>
                <div class="filter-chip" data-day="1" onclick="toggleDayChip(this)">Mon</div>
                <div class="filter-chip" data-day="2" onclick="toggleDayChip(this)">Tue</div>
                <div class="filter-chip" data-day="3" onclick="toggleDayChip(this)">Wed</div>
                <div class="filter-chip" data-day="4" onclick="toggleDayChip(this)">Thu</div>
                <div class="filter-chip" data-day="5" onclick="toggleDayChip(this)">Fri</div>
                <div class="filter-chip" data-day="6" onclick="toggleDayChip(this)">Sat</div>
              </div>
            </div>
          </div>

          <div class="form-group" id="group-custom-cron" style="display: none;">
            <label class="form-label">Raw Cron Expression</label>
            <input type="text" class="form-input" id="cfg-backup-cron" value="{cfg['backup_cron']}">
          </div>

          <div class="form-group" style="margin-top: 0.5rem;">
            <label class="form-label">
              <span class="material-symbols-outlined" style="font-size: 18px;">history</span> Retention Count
            </label>
            <div style="display: flex; align-items: center; gap: 1rem;">
              <input type="range" id="cfg-retention" min="1" max="15" value="{cfg['retention_count']}" style="flex: 1;" oninput="document.getElementById('retention-display').innerText = this.value">
              <span id="retention-display" style="font-size: 1.25rem; font-weight: 600; min-width: 30px;">{cfg['retention_count']}</span>
            </div>
            <p class="form-desc">Older <span class="code-pill">.img.gz</span>, SHA256 hashes, logs, and companion restore tools are safely removed beyond this limit.</p>
          </div>
        </div>

        <!-- TAB 3: CLOUD REPLICATION (RCLONE) -->
        <div id="tab-content-cloud" style="display: none; flex-direction: column; gap: 1.25rem;">
          <div class="guide-box">
            <b>Rclone 3-2-1 Cloud Replication:</b> Maintain an immutable off-site copy of your Home Assistant OS disk images. 
            Mount your <span class="code-pill">rclone.conf</span> in <span class="code-pill">/config/rclone/</span> to push snapshots directly to Backblaze B2, Google Drive, OneDrive, or AWS S3.
          </div>

          <div class="form-group">
            <div style="display: flex; align-items: center; gap: 0.65rem;">
              <input type="checkbox" id="cfg-rclone-enabled" {"checked" if cfg['rclone_sync_enabled'] else ""} style="width: 18px; height: 18px;">
              <label for="cfg-rclone-enabled" style="font-size: 0.95rem; font-weight: 500;">Enable automated offsite cloud sync</label>
            </div>
          </div>

          <div class="form-group">
            <label class="form-label">Rclone Remote Target</label>
            <input type="text" class="form-input" id="cfg-rclone-target" placeholder="e.g. b2:my-haos-bucket/backups or gdrive:Backups" value="{cfg['rclone_remote_target']}">
            <p class="form-desc">Format: <span class="code-pill">RemoteName:Path/To/Folder</span></p>
          </div>
        </div>

        <!-- TAB 4: ALERTS & SAFETY -->
        <div id="tab-content-alerts" style="display: none; flex-direction: column; gap: 1.25rem;">
          <div class="form-group">
            <label class="form-label">Flash Wear Alert Threshold (%)</label>
            <div style="display: flex; align-items: center; gap: 1rem;">
              <input type="range" id="cfg-wear-thresh" min="50" max="95" value="{cfg['safe_wear_threshold']}" style="flex: 1;" oninput="document.getElementById('wear-display').innerText = this.value + '%'">
              <span id="wear-display" style="font-size: 1.2rem; font-weight: 600; min-width: 50px;">{cfg['safe_wear_threshold']}%</span>
            </div>
            <p class="form-desc">Triggers notifications and persistent dashboard warnings when storage reserve blocks deplete.</p>
          </div>

          <div class="form-group">
            <div style="display: flex; align-items: center; gap: 0.65rem;">
              <input type="checkbox" id="cfg-rescue-mode" {"checked" if cfg['enable_rescue'] else ""} style="width: 18px; height: 18px;">
              <label for="cfg-rescue-mode" style="font-size: 0.95rem; font-weight: 500;">Enable Bad-Sector Rescue Guard (conv=noerror,sync)</label>
            </div>
            <p class="form-desc">Prevents aborting on read errors if the physical flash cells have developed bad blocks.</p>
          </div>
        </div>

      </div>

      <div class="dialog-footer">
        <button class="m3-button btn-tonal" onclick="closeConfigModal()">Cancel</button>
        <button class="m3-button btn-filled" onclick="commitSuiteConfiguration()">Save &amp; Apply</button>
      </div>
    </div>
  </div>

  <!-- MODAL: DISASTER RECOVERY GUIDE -->
  <div class="dialog-overlay" id="recovery-guide-modal">
    <div class="m3-dialog">
      <div class="dialog-header">
        <div style="display: flex; align-items: center; gap: 0.5rem;">
          <span class="material-symbols-outlined" style="color: var(--md-primary);">menu_book</span>
          <h2 style="font-family: 'Google Sans', sans-serif; font-size: 1.25rem;">Disaster Recovery Guide</h2>
        </div>
        <span class="material-symbols-outlined" style="cursor: pointer;" onclick="closeRecoveryGuideModal()">close</span>
      </div>
      <div class="dialog-body">
        <div class="guide-box">
          <b>Zero Manual Resizing Required:</b> Every backup generates automated companion recovery tools (<span class="code-pill">restore.sh</span> and <span class="code-pill">restore.ps1</span>). They check checksums, dismount existing volumes, write raw disk blocks, relocate backup GPT structures to the end of the physical media, and expand Partition 8 to 100% card capacity.
        </div>

        <h3 style="font-size: 1.05rem; color: var(--google-blue); margin-top: 0.25rem;">Restoring on Linux / OpenMediaVault</h3>
        <p class="form-desc">Insert the replacement microSD or SSD into your Linux or OMV machine and run:</p>
        <pre class="console-box" style="padding: 0.85rem;">sudo ./restore_YYYY-MM-DD_HH-MM-SS.sh /dev/sdX</pre>
        <p class="form-desc">The script verifies image integrity, flashes sectors with parallel decompression, executes <span class="code-pill">partprobe</span>, and auto-resizes <span class="code-pill">hassos-data</span> with <span class="code-pill">resize2fs</span>.</p>

        <h3 style="font-size: 1.05rem; color: var(--google-yellow); margin-top: 0.5rem;">Restoring on Windows Terminal (PowerShell)</h3>
        <p class="form-desc">Launch Windows Terminal as Administrator, navigate to your backup folder, and run:</p>
        <pre class="console-box" style="padding: 0.85rem;">Set-ExecutionPolicy Bypass -Scope Process
.\restore_YYYY-MM-DD_HH-MM-SS.ps1</pre>
        <p class="form-desc">Select your target USB disk number from the detected list. The script strips Windows drive letters to avoid Win32 access locks and streams the raw disk.</p>
      </div>
      <div class="dialog-footer">
        <button class="m3-button btn-filled" onclick="closeRecoveryGuideModal()">Close Guide</button>
      </div>
    </div>
  </div>

  <!-- MODAL: CONFIRMATION PROMPT -->
  <div class="dialog-overlay" id="confirm-modal">
    <div class="m3-dialog" style="max-width: 460px;">
      <div class="dialog-header">
        <h2 style="font-family: 'Google Sans', sans-serif; font-size: 1.2rem;">Start Live Disk Backup?</h2>
        <span class="material-symbols-outlined" style="cursor: pointer;" onclick="closeConfirmationModal()">close</span>
      </div>
      <div class="dialog-body">
        <p style="font-size: 0.9rem; color: var(--md-on-surface-variant); line-height: 1.45;">
          This initiates an immediate sector clone to your storage folder. SQLite transactions will be safely checkpointed and trimmed before disk streaming begins.
        </p>
      </div>
      <div class="dialog-footer">
        <button class="m3-button btn-tonal" onclick="closeConfirmationModal()">Cancel</button>
        <button class="m3-button btn-filled" onclick="executeConfirmedBackup()">Confirm &amp; Run</button>
      </div>
    </div>
  </div>

  <div class="toast-msg" id="toast"></div>

  <script>
    const basePath = "{ingress_path}";

    function showToast(msg) {{
      const t = document.getElementById("toast");
      t.innerText = msg;
      t.style.display = "block";
      setTimeout(() => {{ t.style.display = "none"; }}, 3500);
    }}

    function openConfigModal() {{
      fetchDisks();
      initScheduleControls();
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

    function switchConfigTab(tabName) {{
      ['storage', 'schedule', 'cloud', 'alerts'].forEach(t => {{
        document.getElementById('tab-btn-' + t).className = (t === tabName ? 'tab-btn active' : 'tab-btn');
        document.getElementById('tab-content-' + t).style.display = (t === tabName ? 'flex' : 'none');
      }});
    }}

    async function fetchDisks() {{
      try {{
        const res = await fetch(basePath + "/api/system/disks");
        const data = await res.json();
        const container = document.getElementById("drive-cards-container");
        const curDev = document.getElementById("cfg-source-dev").value;

        let cardsHtml = `
          <div class="drive-card ${{curDev === 'auto' ? 'selected' : ''}}" onclick="selectDrive('auto')">
            <div>
              <div style="font-weight: 500;">auto - Automatic Detection (Recommended)</div>
              <div style="font-size: 0.78rem; color: var(--md-on-surface-variant);">Dynamically resolves the root boot disk</div>
            </div>
            <span class="material-symbols-outlined" style="color: var(--google-green);">auto_awesome</span>
          </div>
        `;

        data.disks.forEach(d => {{
          const isSel = (curDev === d.path);
          cardsHtml += `
            <div class="drive-card ${{isSel ? 'selected' : ''}}" onclick="selectDrive('${{d.path}}')">
              <div>
                <div style="font-weight: 500;">${{d.path}} (${{d.size}})</div>
                <div style="font-size: 0.78rem; color: var(--md-on-surface-variant);">${{d.model}} &bull; Bus: ${{d.tran}} &bull; ${{d.rota ? 'HDD' : 'Flash/SSD'}}</div>
              </div>
              <span class="material-symbols-outlined" style="color: var(--md-primary);">memory</span>
            </div>
          `;
        }});
        container.innerHTML = cardsHtml;
      }} catch (e) {{
        console.warn("Could not query disk list", e);
      }}
    }}

    function selectDrive(path) {{
      document.getElementById("cfg-source-dev").value = path;
      fetchDisks();
    }}

    function initScheduleControls() {{
      const hourSelect = document.getElementById("cfg-sched-hour");
      if (hourSelect.children.length === 0) {{
        for (let i = 0; i < 24; i++) {{
          const val = (i < 10 ? '0' + i : '' + i);
          const opt = document.createElement("option");
          opt.value = val;
          opt.innerText = val + ":00";
          hourSelect.appendChild(opt);
        }}
      }}

      const curCron = document.getElementById("cfg-backup-cron").value.trim().split(/\\s+/);
      if (curCron.length === 5) {{
        document.getElementById("cfg-sched-min").value = curCron[0].padStart(2, '0');
        document.getElementById("cfg-sched-hour").value = curCron[1].padStart(2, '0');

        if (curCron[2] === '*' && curCron[3] === '*' && curCron[4] !== '*') {{
          document.getElementById("cfg-sched-freq").value = "weekly";
          const days = curCron[4].split(",");
          document.querySelectorAll(".filter-chip").forEach(chip => {{
            chip.className = days.includes(chip.getAttribute("data-day")) ? "filter-chip active" : "filter-chip";
          }});
        }} else if (curCron[2] === '*' && curCron[3] === '*' && curCron[4] === '*') {{
          document.getElementById("cfg-sched-freq").value = "daily";
        }} else if (curCron[2] === '1' && curCron[3] === '*' && curCron[4] === '*') {{
          document.getElementById("cfg-sched-freq").value = "monthly";
        }} else {{
          document.getElementById("cfg-sched-freq").value = "custom";
        }}
      }}
      handleFrequencyChange(document.getElementById("cfg-sched-freq").value);
    }}

    function handleFrequencyChange(val) {{
      const visualControls = document.getElementById("visual-sched-controls");
      const customControls = document.getElementById("group-custom-cron");
      const daysGroup = document.getElementById("group-sched-days");

      if (val === 'custom') {{
        visualControls.style.display = 'none';
        customControls.style.display = 'flex';
      }} else {{
        visualControls.style.display = 'block';
        customControls.style.display = 'none';
        daysGroup.style.display = (val === 'weekly' ? 'flex' : 'none');
      }}
      compileCron();
    }}

    function toggleDayChip(chip) {{
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
        const activeDays = [];
        document.querySelectorAll(".filter-chip.active").forEach(c => {{
          activeDays.push(c.getAttribute("data-day"));
        }});
        cron = `${{parseInt(min, 10)}} ${{parseInt(hr, 10)}} * * ${{activeDays.length > 0 ? activeDays.join(",") : "0"}}`;
      }} else if (freq === 'monthly') {{
        cron = `${{parseInt(min, 10)}} ${{parseInt(hr, 10)}} 1 * *`;
      }}
      document.getElementById("cfg-backup-cron").value = cron;
    }}

    async function probeDirectory() {{
      const dir = document.getElementById("cfg-target-dir").value;
      const resEl = document.getElementById("dir-probe-result");
      resEl.style.display = "block";
      resEl.innerText = "Probing storage mount...";

      try {{
        const res = await fetch(basePath + "/api/system/probe?dir=" + encodeURIComponent(dir));
        const data = await res.json();
        resEl.style.color = (res.status === 200 ? "var(--google-green)" : "var(--google-red)");
        resEl.innerText = data.message;
      }} catch (e) {{
        resEl.style.color = "var(--google-red)";
        resEl.innerText = "Error contacting directory probe service.";
      }}
    }}

    async function commitSuiteConfiguration() {{
      compileCron();
      const payload = {{
        source_dev: document.getElementById("cfg-source-dev").value,
        target_dir: document.getElementById("cfg-target-dir").value,
        retention_count: parseInt(document.getElementById("cfg-retention").value, 10),
        safe_wear_threshold: parseInt(document.getElementById("cfg-wear-thresh").value, 10),
        enable_rescue: document.getElementById("cfg-rescue-mode").checked,
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
        closeConfigModal();
        showToast(data.message || "Settings updated successfully.");
        setTimeout(() => location.reload(), 1500);
      }} catch (e) {{
        alert("Failed to commit settings.");
      }}
    }}

    function copyToClipboard(text) {{
      navigator.clipboard.writeText(text).then(() => showToast("Hash copied to clipboard."));
    }}

    function copyConsoleLog() {{
      const text = document.getElementById("console-output").innerText;
      navigator.clipboard.writeText(text).then(() => showToast("Log copied to clipboard."));
    }}

    async function fetchStatus() {{
      try {{
        const res = await fetch(basePath + "/api/status");
        const data = await res.json();

        document.getElementById("val-status").innerText = data.status;
        document.getElementById("sub-status").innerText = data.status === "Running" ? "Streaming Disk Sectors..." : "Daemon Monitoring";
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
            let actionHtml = `<a href="${{basePath}}/api/download?file=${{encodeURIComponent(item.name)}}" class="m3-button btn-tonal" style="height: 30px; padding: 0 0.85rem; text-decoration: none; font-size: 0.78rem;">Download</a>`;
            if (item.name.endsWith(".sha256")) {{
              actionHtml += ` <button class="m3-button btn-tonal" style="height: 30px; padding: 0 0.55rem;" onclick="copyToClipboard('${{item.hash || item.name}}')"><span class="material-symbols-outlined" style="font-size: 15px;">content_copy</span></button>`;
            }}
            row.innerHTML = `
              <td style="font-family: monospace;">${{item.name}}</td>
              <td>${{item.size}}</td>
              <td style="text-align: right;">${{actionHtml}}</td>
            `;
            tbody.appendChild(row);
          }});
        }} else {{
          tbody.innerHTML = `<tr><td colspan="3" style="text-align: center; color: var(--md-on-surface-variant);">No backup packages found in target directory.</td></tr>`;
        }}
      }} catch (e) {{
        console.error("Status polling failed", e);
      }}
    }}

    async function triggerAction(endpoint) {{
      try {{
        const res = await fetch(basePath + "/api/" + endpoint, {{ method: "POST" }});
        const resp = await res.json();
        showToast(resp.message);
        setTimeout(fetchStatus, 1500);
      }} catch (e) {{
        alert("Action dispatch failed.");
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
                total, used, free = shutil.disk_usage(target)
                self.send_json({
                    "message": f"✔ Reachable & Writable. Free capacity: {free / 1073741824:.2f} GB."
                })
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
                    latest_log = "Unable to read active log stream."

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
            self.send_json({"message": "Disk backup process successfully triggered in background."})
            return

        elif path == "/api/wear":
            subprocess.Popen(["/run.sh", "--wear"])
            self.send_json({"message": "Flash wear diagnostic dispatched to Home Assistant."})
            return

        self.send_response(404)
        self.end_headers()

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), EnterpriseMaterialHandler)
    server.serve_forever()