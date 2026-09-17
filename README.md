# HAOS SD Card Backup & Storage Health Suite

[![Version](https://img.shields.io/badge/version-1.8.0-blue.svg)](https://github.com/bangity/haos-sd-backup)
[![Home Assistant Add-on](https://img.shields.io/badge/home--assistant-add--on-blue.svg)](https://www.home-assistant.io/)
[![Multi-Arch](https://img.shields.io/badge/arch-aarch64%20%7C%20amd64%20%7C%20armv7-green.svg)](https://github.com/bangity/haos-sd-backup)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A production-grade, sector-level disaster recovery engine and hardware health monitoring suite for **Home Assistant OS (HAOS)**[cite: 4, 5]. Back up live boot disks (`/dev/mmcblk0`, NVMe, or SATA SSDs) directly to network SMB/CIFS shares, external USB drives, or offsite cloud buckets without taking your smart home offline[cite: 4, 5].

---

## Architecture & Features

### 🚀 Core Backup Engine
* **Sector-Level Block Replication (`dd` + `pigz -R`):** Creates complete, bootable raw block clones compressed via multi-core parallel gzip with rsyncable chunk alignment.
* **Kernel Page-Cache OOM Shield (`iflag=nocache`):** Bypasses Linux kernel RAM caching during block streaming to eliminate Out-Of-Memory termination and Zigbee/Matter coordinator packet latency.
* **Global ACID Container Freeze Hook:** Connects to the Docker socket (`/var/run/docker.sock`) to temporarily pause write-heavy databases and coordinators (MariaDB, PostgreSQL, SQLite, InfluxDB, Zigbee2MQTT, Mosquitto, Matter) while partition structures and ext4 superblocks are imaged[cite: 3, 4].
* **Deep SQLite Integrity Pre-Check:** Flushes pending Write-Ahead Logs via `PRAGMA wal_checkpoint(TRUNCATE);` with a 5000ms busy timeout and runs `PRAGMA quick_check;` before streaming.
* **Multi-Tier Destination Storage Failover:** Automatically tests target storage with non-destructive write probes. If your primary NAS is unreachable or full, the engine reroutes the stream to an attached USB drive or local `/share/backup_buffer` staging storage.
* **Pi 4 Thermal Watchdog:** Actively reads SoC temperatures during compression[cite: 4]. If the CPU reaches 75°C, the process throttles `pigz` to prevent hardware thermal throttling and network latency[cite: 4].
* **Zero-Knowledge AES-256-CBC Encryption:** Encrypts archives at rest via OpenSSL with PBKDF2 key derivation (100,000 iterations) using isolated environment variables to prevent process-table credential leaks[cite: 4].
* **Deep Bitrot Verification (`pigz -t`):** Tests unencrypted and AES-encrypted archives over the network post-write to verify 100% CRC integrity[cite: 4].
* **Milestone Progress Pipeline:** Tracks real-time milestones: 0–85% Sector Streaming, 88% Cache Flush, 92% Bitrot Audit, 96% Recovery Housekeeping, and 98% Cloud Sync[cite: 4, 5].

---

### 🔥 Direct "Cold Spare" Restorer (No PC Needed)
* **In-Place Drive Flashing:** Flash any backup archive directly onto a secondary USB flash drive or SD card reader connected to the host[cite: 4, 5].
* **Automated Drive Filtering:** Automatically excludes the active boot disk, virtual RAM disks (`/dev/zram*`, `/dev/loop*`), and devices under 1 GB[cite: 4, 5].
* **Live Restoration Progress & Logs:** Streams decompression and block writes with integer progress tracking and live milestone updates[cite: 4, 5].
* **Auto-Formatting & GPT Header Relocation:** Erases existing partition signatures and moves secondary GPT headers to the physical end of larger disks (`sgdisk -e`)[cite: 4].
* **100% Partition & Ext4 Expansion:** Automatically expands Partition 8 (`hassos-data`) to fill 100% of the replacement drive and executes `resize2fs`[cite: 4].
* **Automated Post-Flash Integrity Audit:** Executes a non-destructive read-only filesystem check (`e2fsck -fn`) and prints full partition geometry directly into the dashboard log[cite: 4].
* **PARTUUID Split-Brain Shield:** Flushes drive buffers and unbinds the USB device node from the kernel bus (`/sys/block/<dev>/device/delete`) after writing to prevent partition collision if left plugged in during a reboot[cite: 4].

---

### 🖥️ Material Design 3 Web UI
* **Dynamic Network & USB Discovery:** Automatically detects host-mounted SMB shares (`usage: share`) and USB drives with live capacity meters and write-probe tests[cite: 4, 5].
* **Zero-Flicker Polling:** Background polling detects USB hotplug and unplug events without resetting dropdown state or user selections.
* **Hierarchical Backup Tree:** Groups images, SHA256 checksums, execution logs, and companion restore scripts by backup set with batch deletion and single-click downloads.
* **Integrated Log Viewer Modal:** Inspect execution logs and disaster recovery parameters in-browser with one-click clipboard copying.
* **Supervisor-Proof Persistence:** All settings are stored in `/data/settings.json`, ensuring configurations survive add-on updates and container restarts.

---

### 🩺 Hardware Telemetry & Cloud Replication
* **Multi-Engine Wear Diagnostics:** Reads JEDEC eMMC lifetime registers, S.M.A.R.T. SSD/NVMe wear leveling, and microSD written-sector counts[cite: 4].
* **3-2-1 Cloud Replication:** Synchronizes full disaster recovery packages offsite to Backblaze B2, Google Drive, AWS S3, or OneDrive via Rclone[cite: 4].
* **Timezone-Synchronized Scheduling:** Automatically syncs container time with Home Assistant Core timezone settings and reloads BusyBox `crond` dynamically via `SIGHUP`[cite: 4].
* **Home Assistant Telemetry:** Publishes entities (`sensor.sd_card_backup_status`, `sensor.sd_card_backup_progress`, `sensor.sd_card_soc_temperature`, `sensor.sd_card_wear_estimate`) and fires native HA events[cite: 4].

---

## Capacity & Replacement Compatibility

| Target Drive Size | Restore Compatibility | Operational Behavior |
| :--- | :--- | :--- |
| **Larger Card (64 GB, 128 GB+)** | **Fully Supported** | Companion tools relocate GPT structures (`sgdisk -e`) and expand Partition 8 (`/data`) to 100% card capacity[cite: 4]. |
| **Same Nominal Card (~32 GB)** | **Supported via `FORCE`** | Overcomes minor block count variations between flash memory manufacturers[cite: 4]. |
| **Smaller Card (8 GB, 16 GB)** | **Unsupported** | Raw ext4 structures span the full original card boundary. To migrate downward, use Home Assistant's native `.tar` backup engine[cite: 4]. |

---

## Installation

1. Add this repository to your Home Assistant Add-on Store:
   ```text
   [https://github.com/bangity/haos-sd-backup](https://github.com/bangity/haos-sd-backup)