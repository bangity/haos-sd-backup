#!/usr/bin/env bash
set -euo pipefail

OPTIONS_FILE="/data/options.json"
LOCK_FILE="/var/run/sd_backup.lock"
PROGRESS_FILE="/var/run/sd_backup.progress"

# --- DYNAMIC STORAGE DEVICE RESOLUTION ---
resolve_storage_device() {
    local source_opt
    source_opt=$(jq -r '.source_dev // "auto"' "$OPTIONS_FILE" 2>/dev/null || echo "auto")
    if [[ "$source_opt" == "auto" || -z "$source_opt" ]]; then
        local detected_dev=""
        for mnt in /config /data /; do
            local src
            src=$(findmnt -n -o SOURCE "$mnt" 2>/dev/null || true)
            if [[ -n "$src" && -b "$src" ]]; then
                local pk
                pk=$(lsblk -n -o PKNAME "$src" 2>/dev/null || true)
                if [[ -n "$pk" ]]; then
                    detected_dev="/dev/${pk#/dev/}"
                    break
                fi
            fi
        done

        if [[ -z "$detected_dev" || ! -b "$detected_dev" ]]; then
            if [[ -b "/dev/mmcblk0" ]]; then
                detected_dev="/dev/mmcblk0"
            elif [[ -b "/dev/sda" ]]; then
                detected_dev="/dev/sda"
            elif [[ -b "/dev/nvme0n1" ]]; then
                detected_dev="/dev/nvme0n1"
            fi
        fi
        SOURCE_DEV="${detected_dev:-/dev/mmcblk0}"
    else
        SOURCE_DEV="/dev/${source_opt#/dev/}"
    fi
}

# --- MULTI-ENGINE STORAGE WEAR & HEALTH TELEMETRY ---
get_wear_metrics() {
    resolve_storage_device
    local dev_name
    dev_name=$(basename "$SOURCE_DEV")
    local life_file="/sys/block/${dev_name}/device/life_time"
    local eol_file="/sys/block/${dev_name}/device/pre_eol_info"
    local wear_val="N/A"
    local status="Unsupported"

    if [[ -f "$life_file" ]]; then
        local raw_hex dec_val
        raw_hex=$(awk '{print $1}' "$life_file" 2>/dev/null || echo "0x00")
        dec_val=$(printf "%d" "$raw_hex" 2>/dev/null || echo "0")
        if (( dec_val >= 1 && dec_val <= 10 )); then
            wear_val=$((dec_val * 10))
            status="Normal (JEDEC eMMC)"
        elif (( dec_val == 11 )); then
            wear_val=100
            status="Exceeded Lifetime (JEDEC eMMC)"
        fi
        if [[ -f "$eol_file" ]]; then
            local eol_hex eol_dec
            eol_hex=$(awk '{print $1}' "$eol_file" 2>/dev/null || echo "0x00")
            eol_dec=$(printf "%d" "$eol_hex" 2>/dev/null || echo "0")
            case "$eol_dec" in
                1) status="Normal (0-80% reserve blocks used)" ;;
                2) status="Warning (80-90% reserve blocks used)" ;;
                3) status="Urgent (Over 90% reserve blocks used)" ;;
            esac
        fi
    fi

    if [[ "$wear_val" == "N/A" ]] && command -v smartctl &>/dev/null && [[ -b "$SOURCE_DEV" ]]; then
        local smart_output
        smart_output=$(smartctl -A "$SOURCE_DEV" 2>/dev/null || smartctl -a "$SOURCE_DEV" 2>/dev/null || true)
        local nvme_pct
        nvme_pct=$(echo "$smart_output" | grep -i "Percentage Used:" | head -n1 | awk '{print $3}' | tr -d '%' || true)
        if [[ "$nvme_pct" =~ ^[0-9]+$ ]]; then
            wear_val="$nvme_pct"
            status="NVMe Flash Health OK"
        fi

        if [[ "$wear_val" == "N/A" ]]; then
            local attr_val
            attr_val=$(echo "$smart_output" | grep -E -i "Wear_Leveling_Count|Remaining_Lifetime_Perc|SSD_Life_Left" | head -n1 | awk '{print $4}' || true)
            if [[ "$attr_val" =~ ^[0-9]+$ ]] && (( attr_val > 0 && attr_val <= 100 )); then
                wear_val=$(( 100 - attr_val ))
                status="SATA SSD Health OK"
            fi
        fi
    fi

    if [[ "$wear_val" == "N/A" ]]; then
        local stat_file="/sys/block/${dev_name}/stat"
        if [[ -f "$stat_file" ]]; then
            local sectors_written
            sectors_written=$(awk '{print $7}' "$stat_file" 2>/dev/null || echo 0)
            if [[ "$sectors_written" =~ ^[0-9]+$ ]] && (( sectors_written > 0 )); then
                local gb_written
                gb_written=$(awk "BEGIN {printf \"%.1f\", $sectors_written * 512 / 1073741824}")
                wear_val="N/A*"
                status="MicroSD: no hw wear registers (${gb_written} GB written this boot)"
            else
                status="MicroSD (Hardware wear registers not supported)"
            fi
        else
            status="Hardware Wear Telemetry Unsupported"
        fi
    fi

    echo "${wear_val}|${status}"
}

# --- CLI ROUTING (Evaluated before any daemon logging) ---
if [[ "${1:-}" == "--wear-metrics" ]]; then
    get_wear_metrics
    exit 0
fi

if [[ "${1:-}" == "--update-cron" ]]; then
    B_CRON=$(jq -r '.backup_cron // "0 3 * * 0"' "$OPTIONS_FILE")
    W_CRON=$(jq -r '.wear_cron // "0 12 * * 1"' "$OPTIONS_FILE")
    echo "${B_CRON} /run.sh --backup > /proc/1/fd/1 2>&1" > /etc/crontabs/root
    echo "${W_CRON} /run.sh --wear > /proc/1/fd/1 2>&1" >> /etc/crontabs/root
    exit 0
fi

# --- BRIDGE HOST NETWORK STORAGE ---
bridge_host_network_storage() {
    mkdir -p /mnt/network_storage
    if [[ -d "/proc/1/root/mnt/data/supervisor/mounts" ]]; then
        for mnt_dir in /proc/1/root/mnt/data/supervisor/mounts/*; do
            if [[ -d "$mnt_dir" ]]; then
                local m_name
                m_name=$(basename "$mnt_dir")
                mkdir -p "/mnt/network_storage/${m_name}"
                mount --bind "$mnt_dir" "/mnt/network_storage/${m_name}" 2>/dev/null || true
                mount --bind "$mnt_dir" /backup 2>/dev/null || true
            fi
        done
    fi
}

fire_ha_event() {
    local event_name="$1"
    local json_data="$2"
    if [[ -n "${SUPERVISOR_TOKEN:-}" ]]; then
        curl -s -X POST \
            -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
            -H "Content-Type: application/json" \
            -d "$json_data" \
            "http://supervisor/core/api/events/${event_name}" &>/dev/null || true
    fi
}

update_ha_sensor() {
    local entity="$1"
    local state="$2"
    local friendly_name="$3"
    local icon="$4"
    if [[ -n "${SUPERVISOR_TOKEN:-}" ]]; then
        curl -s -X POST \
            -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
            -H "Content-Type: application/json" \
            -d "{\"state\": \"${state}\", \"attributes\": {\"friendly_name\": \"${friendly_name}\", \"icon\": \"${icon}\"}}" \
            "http://supervisor/core/api/states/${entity}" &>/dev/null || true
    fi
}

send_ha_notification() {
    local title="$1"
    local message="$2"
    if [[ -n "${SUPERVISOR_TOKEN:-}" ]]; then
        curl -s -X POST \
            -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
            -H "Content-Type: application/json" \
            -d "{\"title\": \"${title}\", \"message\": \"${message}\"}" \
            http://supervisor/core/api/services/notify/notify &>/dev/null || true
    fi
}

create_ha_persistent_alert() {
    local title="$1"
    local message="$2"
    local notif_id="$3"
    if [[ -n "${SUPERVISOR_TOKEN:-}" ]]; then
        curl -s -X POST \
            -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
            -H "Content-Type: application/json" \
            -d "{\"title\": \"${title}\", \"message\": \"${message}\", \"notification_id\": \"${notif_id}\"}" \
            http://supervisor/core/api/services/persistent_notification/create &>/dev/null || true
    fi
}

flush_sqlite_wal() {
    local db_path="/config/home-assistant_v2.db"
    if [[ -f "$db_path" ]]; then
        sqlite3 "$db_path" "PRAGMA busy_timeout = 5000; PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null || true
    fi
}

send_email() {
    local subject="$1"
    local body="$2"
    local s_enabled s_user s_to s_host s_port s_pass
    s_enabled=$(jq -r '.smtp_enabled // false' "$OPTIONS_FILE")
    s_user=$(jq -r '.smtp_user // ""' "$OPTIONS_FILE")
    s_to=$(jq -r '.smtp_to // ""' "$OPTIONS_FILE")
    s_host=$(jq -r '.smtp_host // ""' "$OPTIONS_FILE")
    s_port=$(jq -r '.smtp_port // 587' "$OPTIONS_FILE")
    s_pass=$(jq -r '.smtp_pass // ""' "$OPTIONS_FILE")

    if [[ "$s_enabled" != "true" ]] || [[ -z "$s_user" ]] || [[ -z "$s_to" ]] || [[ -z "$s_host" ]]; then
        return 0
    fi

    local proto="smtp"
    local extra_flags="--ssl-reqd"
    [[ "$s_port" == "465" ]] && proto="smtps" && extra_flags=""

    local payload
    payload=$(mktemp)
    cat <<EOF > "$payload"
From: <${s_user}>
To: <${s_to}>
Date: $(date -R)
Subject: ${subject}

${body}
EOF

    curl -sS $extra_flags \
        --url "${proto}://${s_host}:${s_port}" \
        --user "${s_user}:${s_pass}" \
        --mail-from "${s_user}" \
        --mail-rcpt "${s_to}" \
        --upload-file "$payload" &>/dev/null || true
    rm -f "$payload"
}

prune_artifacts() {
    local target_dir="$1"
    local pattern="$2"
    local keep="$3"
    local files=()
    while IFS= read -r f; do
        [[ -n "$f" ]] && files+=("$f")
    done < <(find "$target_dir" -maxdepth 1 -name "$pattern" | sort)

    local total=${#files[@]}
    if (( total > keep )); then
        local to_delete=$(( total - keep ))
        for (( i=0; i<to_delete; i++ )); do
            local target="${files[$i]}"
            if [[ "$target" != "${ACTIVE_LOG_FILE:-}" ]]; then
                rm -f "$target" "${target}.sha256"
            fi
        done
    fi
}

generate_restore_scripts() {
    local base_dir="$1"
    local timestamp="$2"
    local archive_name="$3"
    local min_bytes="$4"
    local min_human="$5"

    local linux_script="${base_dir}/restore_${timestamp}.sh"
    local win_script="${base_dir}/restore_${timestamp}.ps1"

    cat << 'EOF' > "$linux_script"
#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -ne 0 ]] && { echo "[!] Run as root/sudo."; exit 1; }

DEST_DEV="${1:-}"
IMAGE_FILE="PLACEHOLDER_ARCHIVE"
REQUIRED_BYTES=PLACEHOLDER_MIN_BYTES
REQUIRED_HUMAN="PLACEHOLDER_MIN_HUMAN"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FULL_IMAGE="${SCRIPT_DIR}/${IMAGE_FILE}"

if [[ -z "$DEST_DEV" ]] || [[ ! -b "$DEST_DEV" ]]; then
    echo "Usage: sudo $0 /dev/sdX"
    echo "Minimum Required Disk Capacity: ${REQUIRED_HUMAN} (${REQUIRED_BYTES} bytes)"
    echo ""
    echo "Available Storage Block Devices:"
    lsblk -d -o NAME,SIZE,TYPE,MODEL,TRAN | grep -E "disk|usb|mmc|nvme" || lsblk -d -o NAME,SIZE,MODEL
    exit 1
fi

DEV_TYPE=$(lsblk -n -o TYPE "$DEST_DEV" 2>/dev/null || echo "")
if [[ "$DEV_TYPE" == "part" ]] || [[ "$DEST_DEV" =~ (mmcblk|nvme|loop)[0-9]+p[0-9]+$ ]] || [[ "$DEST_DEV" =~ /dev/sd[a-z]+[0-9]+$ ]]; then
    echo "[✖ ERROR] Targeted an individual partition slice (${DEST_DEV}). Target the root disk."
    exit 1
fi

TARGET_BYTES=$(blockdev --getsize64 "$DEST_DEV" 2>/dev/null || lsblk -b -n -d -o SIZE "$DEST_DEV" 2>/dev/null || echo 0)
if [[ "$TARGET_BYTES" -lt "$REQUIRED_BYTES" ]]; then
    echo "[✖ ERROR] Target device ($TARGET_BYTES bytes) is smaller than original size ($REQUIRED_BYTES bytes)."
    exit 1
fi

if [[ -f "${FULL_IMAGE}.sha256" ]]; then
    echo "[*] Checking SHA256 integrity hash..."
    if [[ "$(cat "${FULL_IMAGE}.sha256" | awk '{print $1}')" != "$(sha256sum "$FULL_IMAGE" | awk '{print $1}')" ]]; then
        echo "[!] Hash mismatch! Image is corrupted."; exit 1
    fi
    echo "[✔] Checksum verified."
fi

read -rp "Format / wipe partition signatures before flashing? (y/n): " wipe_opt
if [[ "$wipe_opt" =~ ^[Yy]$ ]]; then
    wipefs -a -f "$DEST_DEV" 2>/dev/null || true
    dd if=/dev/zero of="$DEST_DEV" bs=1M count=32 conv=fsync status=none 2>/dev/null || true
    sync
fi

read -rp "Type 'YES' to begin flashing to $DEST_DEV: " confirm
[[ "$confirm" != "YES" ]] && { echo "Aborted."; exit 0; }

DECOMP_CMD="gzip -dc"
command -v pigz &>/dev/null && DECOMP_CMD="pigz -dc"
PV_PIPE="cat"
command -v pv &>/dev/null && PV_PIPE="pv"

echo "[*] Decompressing and streaming sectors to ${DEST_DEV}..."
$DECOMP_CMD "$FULL_IMAGE" | $PV_PIPE | dd of="$DEST_DEV" bs=4M conv=fsync status=none
sync

echo "[*] Relocating backup GPT data structures..."
if command -v sgdisk &>/dev/null; then
    sgdisk -e "$DEST_DEV" || true
else
    printf 'fix\n' | parted ---pretend-input-tty "$DEST_DEV" print &>/dev/null || true
fi
partprobe "$DEST_DEV" || true
sleep 2
umount "${DEST_DEV}"* 2>/dev/null || true

echo "[*] Expanding partition 8 to fill disk capacity..."
parted -s "$DEST_DEV" resizepart 8 100% || true
partprobe "$DEST_DEV" || true
sleep 2
umount "${DEST_DEV}"* 2>/dev/null || true

if [[ -b "${DEST_DEV}p8" ]]; then
    P8="${DEST_DEV}p8"
else
    P8="${DEST_DEV}8"
fi

for i in {1..5}; do [[ -b "$P8" ]] && break || sleep 1; done

e2fsck -fy "$P8" || true
resize2fs "$P8" || true
echo "[✔] Restore and expansion complete! Drive is bootable."
EOF
    sed -i "s|PLACEHOLDER_ARCHIVE|${archive_name}|g" "$linux_script"
    sed -i "s|PLACEHOLDER_MIN_BYTES|${min_bytes}|g" "$linux_script"
    sed -i "s|PLACEHOLDER_MIN_HUMAN|${min_human}|g" "$linux_script"
    chmod +x "$linux_script"

    cat << 'EOF' > "$win_script"
param([Parameter(Mandatory=$false)][int]$DiskNumber)
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $exe = (Get-Process -Id $PID).Path
    Start-Process $exe -Verb RunAs -ArgumentList "-ExecutionPolicy Bypass -File `"$PSCommandPath`""; Exit
}
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ImageName = "PLACEHOLDER_ARCHIVE"
$RequiredBytes = [int64]PLACEHOLDER_MIN_BYTES
$RequiredHuman = "PLACEHOLDER_MIN_HUMAN"
$ImagePath = Join-Path $ScriptDir $ImageName
$HashPath  = "$ImagePath.sha256"

Clear-Host
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host " HAOS Windows Disaster Recovery Tool: $ImageName" -ForegroundColor White
Write-Host " Minimum Required Capacity: $RequiredHuman ($RequiredBytes bytes)" -ForegroundColor Yellow
Write-Host "================================================================" -ForegroundColor Cyan

if (Test-Path $HashPath) {
    Write-Host "[*] Checking SHA256 integrity hash..." -ForegroundColor Yellow
    if ((Get-Content $HashPath).Trim().Split(" ")[0].ToUpper() -ne (Get-FileHash -Path $ImagePath -Algorithm SHA256).Hash.ToUpper()) {
        Write-Error "SHA256 Mismatch! Archive is corrupted."; Exit
    }
    Write-Host "[✔] Checksum verified." -ForegroundColor Green
}

$Drives = Get-Disk | Where-Object { $_.BusType -in @('USB', 'SD', 'MMC', 'SATA', 'NVMe') -and -not ($_.IsSystem -or $_.IsBoot) }
if (-not $Drives) { $Drives = Get-Disk | Where-Object { -not ($_.IsSystem -or $_.IsBoot) } }
$Drives | Select-Object Number, FriendlyName, BusType, @{N="Size(GB)";E={[math]::Round($_.Size/1GB, 2)}} | Format-Table -AutoSize

if (-not $DiskNumber) { $DiskNumber = Read-Host "`nEnter Target Disk Number to RESTORE" }
$TargetDisk = Get-Disk -Number $DiskNumber -ErrorAction Stop
if ($TargetDisk.IsSystem -or $TargetDisk.IsBoot) { Write-Error "Target is system drive! Operation aborted."; Exit }
if ($TargetDisk.Size -lt $RequiredBytes) { Write-Error "Disk capacity is smaller than required original size ($RequiredBytes bytes)!"; Exit }

$WipeChoice = Read-Host "Wipe partition table first? (y/n)"
if ($WipeChoice -match "^[Yy]") {
    Get-Partition -DiskNumber $DiskNumber -ErrorAction SilentlyContinue | Where-Object { $_.DriveLetter } | ForEach-Object {
        Remove-PartitionAccessPath -DiskNumber $_.DiskNumber -PartitionNumber $_.PartitionNumber -AccessPath "$($_.DriveLetter):" -ErrorAction SilentlyContinue
    }
    Clear-Disk -Number $DiskNumber -RemoveData -RemoveOEM -Confirm:$false -ErrorAction SilentlyContinue
}

$Confirm = Read-Host "Type 'YES' to overwrite Disk $DiskNumber"
if ($Confirm -ne "YES") { Write-Warning "Aborted."; Exit }

Get-Partition -DiskNumber $DiskNumber -ErrorAction SilentlyContinue | Where-Object { $_.DriveLetter } | ForEach-Object {
    Remove-PartitionAccessPath -DiskNumber $_.DiskNumber -PartitionNumber $_.PartitionNumber -AccessPath "$($_.DriveLetter):" -ErrorAction SilentlyContinue
}

try { Set-Disk -Number $DiskNumber -IsOffline $true -ErrorAction SilentlyContinue } catch {}
try { Set-Disk -Number $DiskNumber -IsReadOnly $false -ErrorAction SilentlyContinue } catch {}
$RawPath = "\\.\PhysicalDrive$DiskNumber"

try {
    $InStream = [System.IO.File]::OpenRead($ImagePath)
    $GzStream = New-Object System.IO.Compression.GZipStream($InStream, [System.IO.Compression.CompressionMode]::Decompress)
    $OutStream = [System.IO.File]::Open($RawPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Write, [System.IO.FileShare]::ReadWrite)
    $Buffer = New-Object byte[] (4 * 1024 * 1024)
    $Total = 0
    while (($Read = $GzStream.Read($Buffer, 0, $Buffer.Length)) -gt 0) {
        $OutStream.Write($Buffer, 0, $Read)
        $Total += $Read
        Write-Progress -Activity "Flashing HAOS Raw Image" -Status "$([math]::Round($Total / 1GB, 2)) GB written"
    }
    $OutStream.Flush(); $OutStream.Close(); $GzStream.Close(); $InStream.Close()
} catch {
    Write-Error "Flash operation failed: $_"
} finally {
    try { Set-Disk -Number $DiskNumber -IsOffline $false -ErrorAction SilentlyContinue } catch {}
}

Write-Host "`n[✔] Flashing complete! Drive restored." -ForegroundColor Green
Read-Host "Press Enter to exit..."
EOF
    sed -i "s|PLACEHOLDER_ARCHIVE|${archive_name}|g" "$win_script"
    sed -i "s|PLACEHOLDER_MIN_BYTES|${min_bytes}|g" "$win_script"
    sed -i "s|PLACEHOLDER_MIN_HUMAN|${min_human}|g" "$win_script"
}

run_wear_diagnostic() {
    IFS="|" read -r wear_val status_str < <(get_wear_metrics)
    update_ha_sensor "sensor.sd_card_wear_estimate" "${wear_val}" "Storage Wear Level" "mdi:harddisk"
    update_ha_sensor "sensor.sd_card_health_status" "$status_str" "Storage Health Status" "mdi:heart-pulse"

    local threshold
    threshold=$(jq -r '.safe_wear_threshold // 80' "$OPTIONS_FILE")
    if [[ "$wear_val" =~ ^[0-9]+$ ]] && (( wear_val > threshold )); then
        local warn_msg="WARNING: Storage wear on ${SOURCE_DEV} has reached ${wear_val}% (Safe limit: ${threshold}%). Status: ${status_str}. Replace disk soon."
        create_ha_persistent_alert "Storage Wear Alert" "$warn_msg" "sd_card_wear_alert"
        send_email "CRITICAL: Storage Wear Alert (${wear_val}%)" "$warn_msg" || true
        send_ha_notification "Storage Wear Alert" "$warn_msg"
    fi
}

run_backup() {
    exec 200>"$LOCK_FILE"
    if ! flock -n 200; then
        echo "[!] Backup task already running. Skipping duplicate execution."
        exec 200>&- 2>/dev/null || true
        return 0
    fi

    resolve_storage_device
    bridge_host_network_storage

    local target_dir
    target_dir=$(jq -r '.target_dir // "/backup"' "$OPTIONS_FILE")
    target_dir="${target_dir%/}"
    local retention_count
    retention_count=$(jq -r '.retention_count // 3' "$OPTIONS_FILE")
    local enable_rescue
    enable_rescue=$(jq -r '.enable_rescue // false' "$OPTIONS_FILE")
    local rclone_enabled
    rclone_enabled=$(jq -r '.rclone_sync_enabled // false' "$OPTIONS_FILE")
    local rclone_target
    rclone_target=$(jq -r '.rclone_remote_target // ""' "$OPTIONS_FILE")

    local timestamp
    timestamp=$(date +%Y-%m-%d_%H-%M-%S)
    local output_archive="${target_dir}/haos_backup_${timestamp}.img.gz"
    local status_log="${target_dir}/haos_backup_${timestamp}.log"
    ACTIVE_LOG_FILE="$status_log"

    local monitor_pid=""
    cleanup_backup() {
        if [[ -n "${monitor_pid:-}" ]]; then
            kill "${monitor_pid}" 2>/dev/null || true
            wait "${monitor_pid}" 2>/dev/null || true
        fi
        rm -f "${target_dir}/.backup_probe" 2>/dev/null || true
        rm -f "$PROGRESS_FILE" 2>/dev/null || true
        flock -u 200 2>/dev/null || true
        exec 200>&- 2>/dev/null || true
    }
    trap cleanup_backup EXIT INT TERM

    update_ha_sensor "sensor.sd_card_backup_status" "Running" "SD Card Backup Status" "mdi:progress-clock"
    update_ha_sensor "sensor.sd_card_backup_progress" "0%" "SD Card Backup Progress" "mdi:percent"
    echo "0%|0.0 MB/s|Estimating..." > "$PROGRESS_FILE"

    echo "================================================================"
    echo " Starting Live Disk Backup: ${SOURCE_DEV} -> ${output_archive}"
    echo "================================================================"

    mkdir -p "$target_dir" 2>/dev/null || true

    if ! touch "${target_dir}/.backup_probe" 2>/dev/null; then
        echo "[✖ ERROR] Target directory ${target_dir} is read-only or unreachable."
        update_ha_sensor "sensor.sd_card_backup_status" "Failed - Target Unreachable" "SD Card Backup Status" "mdi:alert-circle"
        send_email "HAOS Backup FAILED" "Target storage ${target_dir} is unreachable or read-only." || true
        cleanup_backup
        return 1
    fi
    rm -f "${target_dir}/.backup_probe"

    local avail_kb
    avail_kb=$(df -kP "$target_dir" | awk 'NR==2 {print $4}')
    if [[ ! "$avail_kb" =~ ^[0-9]+$ ]] || (( avail_kb < 15000000 )); then
        echo "[✖ ERROR] Insufficient storage space on target (<15GB available)."
        update_ha_sensor "sensor.sd_card_backup_status" "Failed - Low Disk Space" "SD Card Backup Status" "mdi:alert-circle"
        send_email "HAOS Backup FAILED: Low Disk Space" "Target storage has under 15GB free space." || true
        cleanup_backup
        return 1
    fi

    local dev_bytes
    dev_bytes=$(blockdev --getsize64 "$SOURCE_DEV" 2>/dev/null || lsblk -b -n -d -o SIZE "$SOURCE_DEV" 2>/dev/null || echo 0)
    if [[ "$dev_bytes" -le 0 ]]; then
        echo "[✖ ERROR] Cannot determine capacity of block device ${SOURCE_DEV}."
        cleanup_backup
        return 1
    fi
    local dev_human
    dev_human=$(awk "BEGIN {printf \"%.2f GB\", $dev_bytes/1073741824}")

    cat <<EOF > "$status_log"
================================================================================
MINIMUM REQUIRED DISK SIZE FOR RESTORE: ${dev_human} (${dev_bytes} bytes)
================================================================================
Timestamp:          ${timestamp}
Source Device:      ${SOURCE_DEV}
Target Archive:     $(basename "$output_archive")
Storage Path:       ${target_dir}
Retention Count:    ${retention_count}
================================================================================
EOF

    fire_ha_event "haos_sd_backup_started" "{\"source\": \"${SOURCE_DEV}\", \"target\": \"${output_archive}\"}"
    run_wear_diagnostic >> "$status_log" 2>&1

    echo "[*] Trimming unmapped ext4 blocks (fstrim)..."
    fstrim -av >> "$status_log" 2>&1 || fstrim -v /config >> "$status_log" 2>&1 || true

    flush_sqlite_wal
    sync
    echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true

    local cores
    cores=$(nproc)
    local pigz_threads=$(( cores > 1 ? cores - 1 : 1 ))
    local start_time
    start_time=$(date +%s)

    local dd_flags="status=none"
    [[ "$enable_rescue" == "true" ]] && dd_flags="conv=noerror,sync status=none"

    echo "[*] Streaming sectors through multi-core pigz (${pigz_threads} threads)..."
    (
        local prev_pos=0
        local prev_time=$(date +%s)
        while true; do
            sleep 4
            local dd_pid
            dd_pid=$(pgrep -f "[d]d if=$SOURCE_DEV" | head -n 1 || true)
            if [[ -n "$dd_pid" ]] && [[ -d "/proc/$dd_pid/fd" ]]; then
                local fd_target=""
                for link_path in /proc/"$dd_pid"/fd/*; do
                    if [[ -e "$link_path" ]] && [[ "$(readlink "$link_path" 2>/dev/null)" == "$SOURCE_DEV" ]]; then
                        fd_target=$(basename "$link_path")
                        break
                    fi
                done
                if [[ -n "$fd_target" ]] && [[ -f "/proc/$dd_pid/fdinfo/$fd_target" ]]; then
                    local read_pos
                    read_pos=$(grep -m1 '^pos:' "/proc/$dd_pid/fdinfo/$fd_target" 2>/dev/null | awk '{print $2}' || echo 0)
                    if [[ "$read_pos" =~ ^[0-9]+$ ]] && (( read_pos > 0 )); then
                        local now=$(date +%s)
                        local dt=$(( now - prev_time ))
                        (( dt < 1 )) && dt=1
                        local dbytes=$(( read_pos - prev_pos ))
                        local speed_bps=$(( dbytes / dt ))
                        local speed_mb
                        speed_mb=$(awk "BEGIN {printf \"%.1f\", $speed_bps/1048576}")
                        prev_pos=$read_pos
                        prev_time=$now

                        local pct=$(( read_pos * 100 / dev_bytes ))
                        (( pct > 99 )) && pct=99

                        local eta_str="calculating..."
                        if (( speed_bps > 100000 )); then
                            local rem_bytes=$(( dev_bytes - read_pos ))
                            local rem_sec=$(( rem_bytes / speed_bps ))
                            local rem_min=$(( rem_sec / 60 ))
                            local rem_sec_mod=$(( rem_sec % 60 ))
                            eta_str="${rem_min}m ${rem_sec_mod}s"
                        fi

                        echo "${pct}%|${speed_mb} MB/s|${eta_str}" > "$PROGRESS_FILE"
                        update_ha_sensor "sensor.sd_card_backup_progress" "${pct}%" "SD Card Backup Progress" "mdi:percent"
                    fi
                fi
            fi
        done
    ) &
    monitor_pid=$!

    local pipe_status=0
    nice -n 19 dd if="$SOURCE_DEV" bs=4M $dd_flags | pv -q | nice -n 19 pigz -p "$pigz_threads" -1 | tee "$output_archive" | sha256sum | awk '{print $1}' > "${output_archive}.sha256" || pipe_status=$?

    kill "$monitor_pid" 2>/dev/null || true
    wait "$monitor_pid" 2>/dev/null || true
    monitor_pid=""

    if [[ "$pipe_status" -ne 0 ]]; then
        echo "[✖ ERROR] Streaming failed with exit code $pipe_status!"
        update_ha_sensor "sensor.sd_card_backup_status" "Failed - Stream Error" "SD Card Backup Status" "mdi:alert-circle"
        send_email "HAOS Backup FAILED" "Read/Write stream error occurred on $SOURCE_DEV." || true
        cleanup_backup
        return 1
    fi

    echo "[*] Testing gzip archive integrity (pigz -t)..."
    if pigz -t "$output_archive" >> "$status_log" 2>&1; then
        echo "[✔] Archive integrity verified 100% OK."
    else
        echo "[✖ ERROR] Corrupted archive detected! Preserving previous restore points."
        update_ha_sensor "sensor.sd_card_backup_status" "Failed - Corrupt Archive" "SD Card Backup Status" "mdi:alert-circle"
        rm -f "$output_archive" "${output_archive}.sha256"
        cleanup_backup
        return 1
    fi

    local end_time
    end_time=$(date +%s)
    local duration=$(( (end_time - start_time) / 60 ))
    local final_size
    final_size=$(ls -lh "$output_archive" | awk '{print $5}')

    echo "[*] Generating companion recovery tools..."
    generate_restore_scripts "$target_dir" "$timestamp" "$(basename "$output_archive")" "$dev_bytes" "$dev_human"

    echo "[*] Pruning obsolete backups (Keeping last ${retention_count} sets)..."
    prune_artifacts "$target_dir" "haos_backup_*.img.gz" "$retention_count"
    prune_artifacts "$target_dir" "haos_backup_*.log" "$retention_count"
    prune_artifacts "$target_dir" "restore_*.sh" "$retention_count"
    prune_artifacts "$target_dir" "restore_*.ps1" "$retention_count"

    if [[ "$rclone_enabled" == "true" ]] && [[ -n "$rclone_target" ]]; then
        echo "[*] Triggering secondary 3-2-1 cloud sync to ${rclone_target}..."
        rclone copy "$output_archive" "$rclone_target" --checksum --log-level NOTICE >> "$status_log" 2>&1 || echo "[!] Rclone sync returned non-zero exit status."
    fi

    cat <<EOF >> "$status_log"
================================================================================
BACKUP FINISHED SUCCESSFULLY AT $(date)
Final Archive Size: ${final_size}
Total Duration:     ${duration} minutes
Archive SHA256:     $(cat "${output_archive}.sha256")
================================================================================
EOF

    update_ha_sensor "sensor.sd_card_backup_status" "Idle (Success)" "SD Card Backup Status" "mdi:check-circle"
    update_ha_sensor "sensor.sd_card_backup_progress" "100%" "SD Card Backup Progress" "mdi:percent"
    update_ha_sensor "sensor.sd_card_backup_last_size" "$final_size" "SD Card Backup Last Size" "mdi:database"
    update_ha_sensor "sensor.sd_card_backup_duration" "${duration} min" "SD Card Backup Duration" "mdi:timer-outline"

    fire_ha_event "haos_sd_backup_completed" "{\"archive\": \"${output_archive}\", \"size\": \"${final_size}\", \"duration_min\": ${duration}}"

    local summary="Backup completed!\nFile: $(basename "$output_archive")\nSize: ${final_size}\nDuration: ${duration} min\nRequired Disk Size: ${dev_human}"
    send_email "HAOS SD Card Backup Succeeded" "$summary" || true
    send_ha_notification "HAOS SD Backup Complete" "Completed in ${duration} min. Size: ${final_size}. Minimum Disk: ${dev_human}"

    echo "[✔] Backup completed successfully: ${output_archive} (${final_size})"
    cleanup_backup
}

if [[ "${1:-}" == "--backup" ]]; then
    run_backup
    exit 0
fi

if [[ "${1:-}" == "--wear" ]]; then
    resolve_storage_device
    run_wear_diagnostic
    exit 0
fi

# --- SERVICE DAEMON INITIALIZATION ---
echo "[*] Initializing SD Card Backup & Health Manager Daemon..."
bridge_host_network_storage
resolve_storage_device

echo "[*] Launching Material Ingress Web Server on port 8099..."
python3 /web_ui.py &

run_wear_diagnostic

RUN_ON_START=$(jq -r '.run_backup_on_start // false' "$OPTIONS_FILE")
if [[ "$RUN_ON_START" == "true" ]]; then
    echo "[*] 'run_backup_on_start' is enabled. Initiating immediate snapshot..."
    run_backup || true
fi

BACKUP_CRON=$(jq -r '.backup_cron // "0 3 * * 0"' "$OPTIONS_FILE")
WEAR_CRON=$(jq -r '.wear_cron // "0 12 * * 1"' "$OPTIONS_FILE")
echo "${BACKUP_CRON} /run.sh --backup > /proc/1/fd/1 2>&1" > /etc/crontabs/root
echo "${WEAR_CRON} /run.sh --wear > /proc/1/fd/1 2>&1" >> /etc/crontabs/root

echo "[✔] Daemon active. Listening for scheduled triggers..."
exec crond -f -l 2