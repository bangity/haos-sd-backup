# Home Assistant OS SD Card Backup & Health Manager

Automated live sector-by-sector microSD card backups with multi-core parallel compression (`pigz`), JEDEC flash memory wear diagnostics, Google Material 3 Ingress Web Dashboard, and disaster recovery script generation.

## Features
- **Zero Card Removal:** Runs live on Home Assistant OS while Core, automations, and Zigbee/Thread networks operate without interruption.
- **Material 3 Ingress Web Control Panel:** Access a dedicated dashboard from your Home Assistant sidebar to trigger on-demand backups, check flash wear, view live progress, and download recovery scripts directly.
- **Hardware Wear Telemetry:** Inspects kernel eMMC/SD wear registers and alerts you at 80% wear before hardware lockup.
- **SQLite WAL Checkpointing:** Forces write-ahead logs to flush before disk dumps begin to eliminate database corruption.
- **Auto-Expanding Restores:** Generates companion `restore.sh` (Linux) and `restore.ps1` (Windows) recovery scripts that relocate GPT secondary headers and auto-expand Partition 8 to any card size.

## Installation
1. Go to **Settings > Add-ons > Add-on Store > Repositories**.
2. Add your repository URL:
   `https://github.com/bangity/haos-sd-backup`
3. Select **SD Card Raw Backup & Health Manager** and click **Install**.
4. On the **Info** tab, toggle **Protection mode** to **OFF** (required for `/dev/mmcblk0` block access).
5. Open the **Configuration** tab, set your SMB target directory, retention limit, and cron schedule, then click **Start**.