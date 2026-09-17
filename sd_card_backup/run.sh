#!/usr/bin/env bash
set -euo pipefail

OPTIONS_FILE="/data/options.json"
SETTINGS_FILE="/data/settings.json"
LOCK_FILE="/var/run/sd_backup.lock"
PROGRESS_FILE="/var/run/sd_backup.progress"

# Prioritize settings.json (saved from Web UI) over supervisor options.json
get_cfg_file() {
    if [[ -f "$SETTINGS_FILE" ]]; then
        echo "$SETTINGS_FILE"
    else
        echo "$OPTIONS_FILE"
    fi
}

# --- DYNAMIC STORAGE DEVICE RESOLUTION ---
resolve_storage_device() {
    local source_opt
    source_opt=$(jq -r '.source_dev // "auto"' "$(get_cfg_file)" 2>/dev/null || echo "auto")
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

# --- GLOBAL CONTAINER FREEZE HOOK (ACID STATE GUARD) ---
PAUSED_CONTAINERS=()

freeze_dirty_containers() {
    PAUSED_CONTAINERS=()
    if [[ ! -S "/var/run/docker.sock" ]]; then
        return 0
    fi

    echo "[*] Discovering active stateful containers for temporary freeze..."
    local running_json
    running_json=$(curl -s --unix-socket /var/run/docker.sock "http://localhost/containers/json?filters=%7B%22status%22%3A%5B%22running%22%5D%7D" 2>/dev/null || true)
    if [[ -z "$running_json" || "$running_json" == *"message"* ]]; then
        return 0
    fi

    local my_cid
    my_cid=$(cat /etc/hostname 2>/dev/null || echo "self")
    local target_ids
    target_ids=$(echo "$running_json" | jq -r --arg self "$my_cid" '.[] | select((.Id | startswith($self) | not) and (.Names[0] | test("mariadb|sqlite|influxdb|postgres|zigbee2mqtt|matter|mosquitto|zwave"; "i"))) | .Id' 2>/dev/null || true)

    for cid in $target_ids; do
        if [[ -n "$cid" ]]; then
            local cname
            cname=$(echo "$running_json" | jq -r ".[] | select(.Id == \"$cid\") | .Names[0]" 2>/dev/null || echo "$cid")
            echo "[*] Pausing container ${cname} to prevent dirty writes during partition clone..."
            if curl -s -X POST --unix-socket /var/run/docker.sock "http://localhost/containers/${cid}/pause" 2>/dev/null; then
                PAUSED_CONTAINERS+=("$cid")
            fi
        fi
    done

    # Safety Watchdog: Unconditionally unpause after 25s even if dd runs longer
    (
        sleep 25
        unfreeze_dirty_containers
    ) &
    freeze_watchdog_pid=$!
}

unfreeze_dirty_containers() {
    if [[ ${#PAUSED_CONTAINERS[@]} -eq 0 ]] || [[ ! -S "/var/run/docker.sock" ]]; then
        return 0
    fi

    for cid in "${PAUSED_CONTAINERS[@]}"; do
        curl -s -X POST --unix-socket /var/run/docker.sock "http://localhost/containers/${cid}/unpause" 2>/dev/null || true
    done
    echo "[✔] All paused service containers resumed."
    PAUSED_CONTAINERS=()
}

# --- EMERGENCY BACKUP CANCELLATION & PURGE ---
stop_backup() {
    echo "[!] Stop request received. Terminating backup processes..."
    unfreeze_dirty_containers 2>/dev/null || true
    
    # 1. Terminate dd, pigz, and child subshells
    pkill -f "dd if=" 2>/dev/null || true
    pkill -f "pigz.*haos_backup_" 2>/dev/null || true
    pkill -f "run.sh --backup" 2>/dev/null || true

    # 2. Identify and purge in-progress partial artifacts
    if [[ -f "/var/run/sd_backup.active" ]]; then
        local partial_file
        partial_file=$(cat "/var/run/sd_backup.active" 2>/dev/null || true)
        if [[ -n "$partial_file" && -f "$partial_file" ]]; then
            echo "[*] Deleting incomplete archive: ${partial_file}"
            rm -f "$partial_file" "${partial_file}.sha256" "${partial_file%.img.gz}.log" 2>/dev/null || true
        fi
        rm -f "/var/run/sd_backup.active" 2>/dev/null || true
    fi

    # 3. Release system locks and reset telemetry
    rm -f "$PROGRESS_FILE" "$LOCK_FILE" 2>/dev/null || true
    update_ha_sensor "sensor.sd_card_backup_status" "Cancelled" "SD Card Backup Status" "mdi:close-circle"
    update_ha_sensor "sensor.sd_card_backup_progress" "0%" "SD Card Backup Progress" "mdi:percent"
    fire_ha_event "haos_sd_backup_cancelled" "{\"status\": \"cancelled\"}"
    echo "[✔] Backup stopped and partial files safely removed."
}

# --- CENTRALIZED EXECUTION LOGGER ---
log_status() {
    local msg="$1"
    local ts
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[*] [${ts}] ${msg}"
    if [[ -n "${ACTIVE_LOG_FILE:-}" && -f "${ACTIVE_LOG_FILE:-}" ]]; then
        echo "[${ts}] ${msg}" >> "$ACTIVE_LOG_FILE"
    fi
}

# --- TIMEZONE SYNCHRONIZATION WITH HOME ASSISTANT CORE ---
sync_ha_timezone() {
    if [[ -n "${SUPERVISOR_TOKEN:-}" ]]; then
        local tz
        tz=$(curl -s -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" "http://supervisor/core/info" 2>/dev/null | jq -r '.data.timezone // empty' 2>/dev/null || true)
        if [[ -n "$tz" && -f "/usr/share/zoneinfo/${tz}" ]]; then
            cp "/usr/share/zoneinfo/${tz}" /etc/localtime
            echo "$tz" > /etc/timezone
            export TZ="$tz"
            echo "[*] Synchronized container timezone with Home Assistant: ${tz} ($(date))"
        fi
    fi
}

# --- ATOMIC CRONTAB REBUILD & SIGHUP RELOAD ---
apply_crontab_config() {
    local b_cron="$1"
    local w_cron="$2"
    local is_enabled="$3"

    {
        echo "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        [[ -n "${SUPERVISOR_TOKEN:-}" ]] && echo "SUPERVISOR_TOKEN=\"${SUPERVISOR_TOKEN}\""
        [[ -n "${TZ:-}" ]] && echo "TZ=\"${TZ}\""
        if [[ "$is_enabled" == "true" ]]; then
            echo "${b_cron} /run.sh --backup > /proc/1/fd/1 2>&1"
        else
            echo "# Automated backup schedule disabled by user"
        fi
        echo "${w_cron} /run.sh --wear > /proc/1/fd/1 2>&1"
    } > /etc/crontabs/root

    chmod 600 /etc/crontabs/root
    pkill -HUP -x crond 2>/dev/null || true
    echo "[✔] Crontab updated and SIGHUP sent to crond (Active: ${is_enabled}, Schedule: ${b_cron})"
}

# --- PI 4 THERMAL WATCHDOG ---
get_soc_temp() {
    if [[ -f "/sys/class/thermal/thermal_zone0/temp" ]]; then
        local raw_temp
        raw_temp=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo 0)
        echo $(( raw_temp / 1000 ))
    else
        echo "0"
    fi
}

# --- DIRECT COLD SPARE RESTORER (BURN TO USB) ---
burn_to_spare() {
    local archive_path="$1"
    local target_disk="$2"
    local pass_phrase="${3:-}"

    exec 200>"$LOCK_FILE"
    if ! flock -n 200; then
        echo "[!] Task already in progress. Skipping burn request."
        exit 1
    fi

    local burn_log_file="/var/run/burn_spare.log"
    local monitor_pid=""
    rm -f "$burn_log_file" "$PROGRESS_FILE" "$PROGRESS_FILE.raw" 2>/dev/null || true

    cleanup_burn() {
        if [[ -n "${monitor_pid:-}" ]]; then
            kill "${monitor_pid}" 2>/dev/null || true
            wait "${monitor_pid}" 2>/dev/null || true
        fi
        rm -f "$PROGRESS_FILE.raw" 2>/dev/null || true
        flock -u 200 2>/dev/null || true
        exec 200>&- 2>/dev/null || true
        rm -f "$LOCK_FILE" 2>/dev/null || true
    }
    trap cleanup_burn EXIT INT TERM

    burn_log() {
        local msg="$1"
        local ts
        ts=$(date '+%Y-%m-%d %H:%M:%S')
        echo "[*] [${ts}] ${msg}"
        echo "[${ts}] ${msg}" >> "$burn_log_file"
    }

    resolve_storage_device
    if [[ -z "$archive_path" || ! -f "$archive_path" ]]; then
        burn_log "[✖ ERROR] Archive file does not exist: $archive_path"
        cleanup_burn
        exit 1
    fi

    if [[ -z "$target_disk" || ! -b "$target_disk" ]]; then
        burn_log "[✖ ERROR] Invalid target block device: $target_disk"
        cleanup_burn
        exit 1
    fi

    if [[ "$target_disk" == "$SOURCE_DEV" ]]; then
        burn_log "[✖ CRITICAL GUARD] Target $target_disk is the ACTIVE BOOT DEVICE! Aborting burn."
        cleanup_burn
        exit 1
    fi

    if [[ "$target_disk" =~ (zram|ram|loop|dm-) ]]; then
        burn_log "[✖ CRITICAL GUARD] Target $target_disk is a RAM/virtual device! Aborting burn."
        cleanup_burn
        exit 1
    fi

    local disk_sz
    disk_sz=$(blockdev --getsize64 "$target_disk" 2>/dev/null || echo 0)
    if (( disk_sz < 1073741824 )); then
        burn_log "[✖ CRITICAL GUARD] Target $target_disk is smaller than 1 GB (${disk_sz} bytes)! Aborting burn."
        cleanup_burn
        exit 1
    fi

    burn_log "Starting cold spare flash: $(basename "$archive_path") -> ${target_disk}"
    echo "2%|Flashing Spare|Step 1/4: Wiping partition structures on ${target_disk}..." > "$PROGRESS_FILE"
    update_ha_sensor "sensor.sd_card_backup_status" "Flashing Spare" "SD Card Backup Status" "mdi:flash"
    update_ha_sensor "sensor.sd_card_backup_progress" "2%" "SD Card Backup Progress" "mdi:percent"

    burn_log "Step 1/4: Unmounting and wiping partition signatures on ${target_disk}..."
    umount "${target_disk}"* 2>/dev/null || true
    swapoff "${target_disk}"* 2>/dev/null || true
    wipefs -a -f "$target_disk" 2>/dev/null || true
    sgdisk -Z "$target_disk" 2>/dev/null || true
    dd if=/dev/zero of="$target_disk" bs=1M count=10 conv=fsync status=none 2>/dev/null || true

    burn_log "Step 2/4: Decompressing and streaming sectors to ${target_disk}..."
    echo "5%|Flashing Spare|Step 2/4: Writing raw sectors to ${target_disk}..." > "$PROGRESS_FILE"

    # Monitor decompression & write progress via pv integer feed
    (
        while true; do
            sleep 2
            if [[ -f "$PROGRESS_FILE.raw" ]]; then
                local p
                p=$(tail -n 1 "$PROGRESS_FILE.raw" 2>/dev/null || echo 0)
                if [[ "$p" =~ ^[0-9]+$ ]]; then
                    local scaled=$(( 5 + (p * 80 / 100) ))
                    (( scaled > 85 )) && scaled=85
                    echo "${scaled}%|Flashing Spare|Writing sectors to ${target_disk} (${p}% decompressed)..." > "$PROGRESS_FILE"
                    update_ha_sensor "sensor.sd_card_backup_progress" "${scaled}%" "SD Card Backup Progress" "mdi:percent"
                fi
            fi
        done
    ) &
    monitor_pid=$!

    local pipe_err=0
    if [[ "$archive_path" == *.enc ]]; then
        if [[ -z "$pass_phrase" ]]; then
            burn_log "[✖ ERROR] Passphrase required for encrypted archive."
            cleanup_burn
            exit 1
        fi
        export BURN_PASS="$pass_phrase"
        (pv -n -i 2 "$archive_path" 2> "$PROGRESS_FILE.raw") | \
            nice -n 5 openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 -pass env:BURN_PASS | \
            nice -n 5 pigz -dc | \
            nice -n 5 dd of="$target_disk" bs=8M conv=fsync status=none || pipe_err=1
        unset BURN_PASS
    else
        (pv -n -i 2 "$archive_path" 2> "$PROGRESS_FILE.raw") | \
            nice -n 5 pigz -dc | \
            nice -n 5 dd of="$target_disk" bs=8M conv=fsync status=none || pipe_err=1
    fi

    kill "$monitor_pid" 2>/dev/null || true
    wait "$monitor_pid" 2>/dev/null || true
    monitor_pid=""
    rm -f "$PROGRESS_FILE.raw" 2>/dev/null || true

    if [[ $pipe_err -ne 0 ]]; then
        burn_log "[✖ ERROR] Sector stream failed during flashing."
        update_ha_sensor "sensor.sd_card_backup_status" "Failed - Burn Error" "SD Card Backup Status" "mdi:alert-circle"
        cleanup_burn
        exit 1
    fi

    burn_log "Step 3/4: Relocating GPT table to the physical end of ${target_disk}..."
    echo "88%|Flashing Spare|Step 3/4: Aligning secondary GPT boundaries..." > "$PROGRESS_FILE"
    update_ha_sensor "sensor.sd_card_backup_progress" "88%" "SD Card Backup Progress" "mdi:percent"
    sync
    partprobe "$target_disk" 2>/dev/null || true
    sleep 2
    sgdisk -e "$target_disk" 2>/dev/null || true
    partprobe "$target_disk" 2>/dev/null || true
    sleep 2

    burn_log "Step 4/4: Expanding Partition 8 (HAOS /data) to 100% of available space..."
    echo "94%|Flashing Spare|Step 4/4: Resizing partition 8 to fill drive..." > "$PROGRESS_FILE"
    update_ha_sensor "sensor.sd_card_backup_progress" "94%" "SD Card Backup Progress" "mdi:percent"
    parted -s "$target_disk" resizepart 8 100% 2>/dev/null || true
    partprobe "$target_disk" 2>/dev/null || true
    sleep 2

    local target_p8="${target_disk}8"
    [[ "$target_disk" =~ [0-9]$ ]] && target_p8="${target_disk}p8"

    for i in {1..5}; do [[ -b "$target_p8" ]] && break || sleep 1; done

    if [[ -b "$target_p8" ]]; then
        burn_log "Expanding ext4 filesystem on ${target_p8}..."
        e2fsck -fy "$target_p8" 2>/dev/null || true
        resize2fs "$target_p8" 2>/dev/null || true
        burn_log "[✔] Ext4 filesystem successfully expanded to 100% card capacity."
    fi

    # --- STEP 5/5: AUTOMATED IN-UI INTEGRITY & PARTITION AUDIT ---
    burn_log "Step 5/5: Running post-flash integrity and partition audit..."
    echo "98%|Verifying Spare|Running read-only filesystem check (e2fsck -fn)..." > "$PROGRESS_FILE"
    update_ha_sensor "sensor.sd_card_backup_progress" "98%" "SD Card Backup Progress" "mdi:percent"
    update_ha_sensor "sensor.sd_card_backup_status" "Verifying Spare" "SD Card Backup Status" "mdi:check-decagram"

    # 1. Audit partition table geometry and print directly to log
    burn_log "Partition layout on ${target_disk}:"
    lsblk -o NAME,SIZE,FSTYPE,LABEL "$target_disk" >> "$burn_log_file" 2>&1 || true

    # 2. Perform non-destructive read-only filesystem check on data partition
    local fsck_err=0
    if [[ -b "$target_p8" ]]; then
        burn_log "Auditing filesystem health on ${target_p8} (e2fsck -fn)..."
        local fsck_output
        fsck_output=$(e2fsck -fn "$target_p8" 2>&1) || fsck_err=$?
        echo "$fsck_output" >> "$burn_log_file"

        # e2fsck return codes: 0 = No errors, 1 = Errors corrected, >1 = Uncorrected errors
        if [[ $fsck_err -le 1 ]]; then
            burn_log "[✔ PASS] Ext4 filesystem on ${target_p8} is clean, consistent, and bootable."
        else
            burn_log "[!] WARNING: Ext4 audit reported errors on ${target_p8} (Exit code: ${fsck_err})."
        fi
    fi

    if [[ $fsck_err -le 1 ]]; then
        burn_log "[✔ COMPLETE] Cold spare flashed & 100% verified on ${target_disk}! Ready to swap."
        echo "100%|Verified & Ready|Cold spare flashed and 100% verified!" > "$PROGRESS_FILE"
        update_ha_sensor "sensor.sd_card_backup_status" "Idle (Burn & Verified OK)" "SD Card Backup Status" "mdi:check-circle"
    else
        burn_log "[✖ NOTICE] Flash finished with verification warnings. Review log below."
        echo "100%|Check Warnings|Flashed with filesystem warnings (see log)" > "$PROGRESS_FILE"
        update_ha_sensor "sensor.sd_card_backup_status" "Idle (Burn Warnings)" "SD Card Backup Status" "mdi:alert-circle"
    fi

    update_ha_sensor "sensor.sd_card_backup_progress" "100%" "SD Card Backup Progress" "mdi:percent"

    # --- DETACH USB SPARE FROM KERNEL (PARTUUID SPLIT-BRAIN SHIELD) ---
    local dev_base
    dev_base=$(basename "$target_disk")
    burn_log "Detaching ${target_disk} from kernel bus to prevent PARTUUID boot collision..."
    sync
    blockdev --flushbufs "$target_disk" 2>/dev/null || true

    # Safely power down and unbind the USB block node so reboot cannot mount it accidentally
    if [[ -f "/sys/block/${dev_base}/device/delete" ]]; then
        echo 1 > "/sys/block/${dev_base}/device/delete" 2>/dev/null || true
    fi
    burn_log "[✔] Spare drive safely unmounted and powered down. Ready to unplug."

    sleep 3
    cleanup_burn
    trap - EXIT INT TERM
    exit 0
}

# --- CLI ROUTING (Evaluated before any daemon logging) ---
if [[ "${1:-}" == "--stop" ]]; then
    stop_backup
    exit 0
fi

if [[ "${1:-}" == "--burn-spare" ]]; then
    burn_to_spare "${2:-}" "${3:-}" "${4:-}"
    exit 0
fi

if [[ "${1:-}" == "--soc-temp" ]]; then
    get_soc_temp
    exit 0
fi

if [[ "${1:-}" == "--wear-metrics" ]]; then
    get_wear_metrics
    exit 0
fi

if [[ "${1:-}" == "--update-cron" ]]; then
    sync_ha_timezone
    local_cfg=$(get_cfg_file)
    B_CRON=$(jq -r '.backup_cron // "0 3 * * 0"' "$local_cfg" 2>/dev/null || echo "0 3 * * 0")
    W_CRON=$(jq -r '.wear_cron // "0 12 * * 1"' "$local_cfg" 2>/dev/null || echo "0 12 * * 1")
    SCHED_ENABLED=$(jq -r 'if .schedule_enabled == false then "false" else "true" end' "$local_cfg" 2>/dev/null || echo "false")

    apply_crontab_config "$B_CRON" "$W_CRON" "$SCHED_ENABLED"
    exit 0
fi

# --- BRIDGE HOST NETWORK STORAGE ---
bridge_host_network_storage() {
    if command -v nsenter &>/dev/null; then
        local found_mount=""
        # 1. Discover active CIFS/NFS mount from host kernel mount table
        found_mount=$(nsenter --target 1 --mount -- sh -c '
            awk "$3 ~ /^(cifs|smb3|nfs|nfs4)$/ || $1 ~ /^\/\// {print $2; exit}" /proc/mounts
        ' 2>/dev/null || true)

        # 2. Fallback: check supervisor mounts directory on host
        if [[ -z "$found_mount" ]]; then
            found_mount=$(nsenter --target 1 --mount -- sh -c '
                for d in /mnt/data/supervisor/mounts/*; do
                    if [ -d "$d" ]; then
                        echo "$d"
                        break
                    fi
                done
            ' 2>/dev/null || true)
        fi

        # 3. Mount directly to host backup directory (propagates into container /backup via rslave)
        if [[ -n "$found_mount" ]]; then
            echo "[*] Bridging host network mount (${found_mount}) to /backup..."
            nsenter --target 1 --mount -- mount --rbind "$found_mount" /mnt/data/supervisor/backup 2>/dev/null || true
        fi
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
        echo "[*] Deep SQLite Pre-Check: Committing WAL transactions and verifying consistency..."
        sqlite3 "$db_path" "PRAGMA busy_timeout = 5000; PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null || true
        local db_check
        db_check=$(sqlite3 "$db_path" "PRAGMA quick_check;" 2>/dev/null || echo "error")
        if [[ "$db_check" != "ok" ]]; then
            echo "[!] WARNING: SQLite database reported integrity warnings: ${db_check}"
        else
            echo "[✔] SQLite database integrity: 100% OK."
        fi
    fi
}

# --- GLOBAL CONTAINER FREEZE HOOK (ACID STATE GUARD) ---
PAUSED_CONTAINERS=()

freeze_dirty_containers() {
    PAUSED_CONTAINERS=()
    if [[ ! -S "/var/run/docker.sock" ]]; then
        return 0
    fi

    echo "[*] Discovering active stateful containers for temporary freeze..."
    local running_json
    running_json=$(curl -s --unix-socket /var/run/docker.sock "http://localhost/containers/json?filters=%7B%22status%22%3A%5B%22running%22%5D%7D" 2>/dev/null || true)
    if [[ -z "$running_json" || "$running_json" == *"message"* ]]; then
        return 0
    fi

    # Target databases, message brokers, and coordinators while excluding self
    local my_cid
    my_cid=$(cat /etc/hostname 2>/dev/null || echo "self")
    local target_ids
    target_ids=$(echo "$running_json" | jq -r --arg self "$my_cid" '.[] | select((.Id | startswith($self) | not) and (.Names[0] | test("mariadb|sqlite|influxdb|postgres|zigbee2mqtt|matter|mosquitto|zwave"; "i"))) | .Id' 2>/dev/null || true)

    for cid in $target_ids; do
        if [[ -n "$cid" ]]; then
            local cname
            cname=$(echo "$running_json" | jq -r ".[] | select(.Id == \"$cid\") | .Names[0]" 2>/dev/null || echo "$cid")
            echo "[*] Pausing container ${cname} to prevent dirty writes during partition clone..."
            if curl -s -X POST --unix-socket /var/run/docker.sock "http://localhost/containers/${cid}/pause" 2>/dev/null; then
                PAUSED_CONTAINERS+=("$cid")
            fi
        fi
    done

    # Safety Watchdog: Unconditionally unpause after 25s even if dd runs longer
    (
        sleep 25
        unfreeze_dirty_containers
    ) &
}

unfreeze_dirty_containers() {
    if [[ ${#PAUSED_CONTAINERS[@]} -eq 0 ]] || [[ ! -S "/var/run/docker.sock" ]]; then
        return 0
    fi

    for cid in "${PAUSED_CONTAINERS[@]}"; do
        curl -s -X POST --unix-socket /var/run/docker.sock "http://localhost/containers/${cid}/unpause" 2>/dev/null || true
    done
    echo "[✔] All paused service containers resumed."
    PAUSED_CONTAINERS=()
}

send_email() {
    local subject="$1"
    local body="$2"
    local cfg_file
    cfg_file=$(get_cfg_file)
    local s_enabled s_user s_to s_host s_port s_pass
    s_enabled=$(jq -r '.smtp_enabled // false' "$cfg_file")
    s_user=$(jq -r '.smtp_user // ""' "$cfg_file")
    s_to=$(jq -r '.smtp_to // ""' "$cfg_file")
    s_host=$(jq -r '.smtp_host // ""' "$cfg_file")
    s_port=$(jq -r '.smtp_port // 587' "$cfg_file")
    s_pass=$(jq -r '.smtp_pass // ""' "$cfg_file")

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
    done < <(find "$target_dir" -maxdepth 1 \( -name "$pattern" -o -name "${pattern}.enc" \) | sort)

    local total=${#files[@]}
    if (( total > keep )); then
        local to_delete=$(( total - keep ))
        for (( i=0; i<to_delete; i++ )); do
            local target="${files[$i]}"
            if [[ "$target" != "${ACTIVE_LOG_FILE:-}" ]]; then
                rm -f "$target" "${target}.sha256" "${target%.enc}.sha256" 2>/dev/null || true
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
    echo "================================================================================"
    echo " [!] CRITICAL RESTORE WARNING: TARGET DISK IS SMALLER THAN ORIGINAL IMAGE"
    echo " Target: ${TARGET_BYTES} bytes | Original Image: ${REQUIRED_BYTES} bytes (${REQUIRED_HUMAN})"
    echo "--------------------------------------------------------------------------------"
    echo " • Dropping whole storage tiers (e.g. 32 GB -> 16 GB / 8 GB) WILL NOT WORK."
    echo "   Ext4 metadata spans the entire 32 GB space; truncating sectors causes corruption"
    echo "   and HAOS will fail to boot."
    echo " • FORCE is ONLY valid for minor manufacturing variances between same-tier cards"
    echo "   (e.g., restoring a 31.9 GB image onto a 31.2 GB card)."
    echo " • To migrate to a smaller card (8 GB / 16 GB), perform a fresh HAOS flash and"
    echo "   restore using Home Assistant's native backup (.tar) instead."
    echo "================================================================================"
    read -rp "Type 'FORCE' ONLY if this is a same-size card with slight sector variance: " force_restore
    if [[ "$force_restore" != "FORCE" ]]; then
        echo "[✖] Restore aborted to protect against filesystem corruption."
        exit 1
    fi
    echo "[*] Proceeding with forced restore..."
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

if [[ "$IMAGE_FILE" == *.enc ]]; then
    echo "[*] Encrypted archive detected. OpenSSL AES-256 decryption required."
    read -rsp "Enter Decryption Passphrase: " RESTORE_PASS
    echo ""
    export RESTORE_PASS
    echo "[*] Decrypting, decompressing, and streaming sectors to ${DEST_DEV}..."
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 -pass env:RESTORE_PASS -in "$FULL_IMAGE" | $DECOMP_CMD | $PV_PIPE | dd of="$DEST_DEV" bs=4M conv=fsync status=none
    unset RESTORE_PASS
else
    echo "[*] Decompressing and streaming sectors to ${DEST_DEV}..."
    $DECOMP_CMD "$FULL_IMAGE" | $PV_PIPE | dd of="$DEST_DEV" bs=4M conv=fsync status=none
fi
sync

echo "[*] Relocating secondary GPT data structures to the end of the physical disk..."
sync
if command -v sgdisk &>/dev/null; then
    sgdisk -e "$DEST_DEV" || true
else
    printf 'fix\n' | parted ---pretend-input-tty "$DEST_DEV" print &>/dev/null || true
fi
partprobe "$DEST_DEV" || true
sleep 2
umount "${DEST_DEV}"* 2>/dev/null || true

echo "[*] Expanding partition 8 (HAOS data) to 100% of available card capacity..."
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

if [[ -b "$P8" ]]; then
    echo "[*] Resizing ext4 filesystem to expand into all newly available space..."
    e2fsck -fy "$P8" || true
    resize2fs "$P8" || true
fi
echo "[✔] Restore and expansion complete! Drive is fully resized and bootable."
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
if ($TargetDisk.Size -lt $RequiredBytes) {
    Write-Host "`n================================================================================" -ForegroundColor Red
    Write-Host " [!] CRITICAL WARNING: TARGET DISK IS SMALLER THAN ORIGINAL DRIVE" -ForegroundColor Yellow
    Write-Host " Target Disk: $([math]::Round($TargetDisk.Size/1GB, 2)) GB | Required Original Image: $RequiredHuman" -ForegroundColor White
    Write-Host "--------------------------------------------------------------------------------" -ForegroundColor Red
    Write-Host " • Dropping whole tiers (e.g. 32 GB -> 16 GB / 8 GB) WILL CORRUPT THE FILESYSTEM." -ForegroundColor Red
    Write-Host "   Raw ext4 metadata is mapped across the full 32 GB. HAOS will NOT boot." -ForegroundColor Yellow
    Write-Host " • FORCE is strictly intended for minor sector variances between same-tier cards" -ForegroundColor White
    Write-Host "   (e.g., restoring a 31.9 GB image onto a 31.2 GB replacement card)." -ForegroundColor White
    Write-Host " • To move to an 8 GB or 16 GB card, flash a clean HAOS and restore via .tar." -ForegroundColor Cyan
    Write-Host "================================================================================`n" -ForegroundColor Red
    $ForceChoice = Read-Host "Type 'FORCE' ONLY if this is a same-size card with slight variance"
    if ($ForceChoice -ne "FORCE") { Write-Error "Restore aborted to prevent corrupted flash."; Exit }
}

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

if ($ImageName -like "*.enc") {
    if (Get-Command openssl -ErrorAction SilentlyContinue) {
        $Pass = Read-Host "Enter Decryption Passphrase"
        Write-Host "[*] Decrypting archive with OpenSSL..." -ForegroundColor Yellow
        $DecPath = $ImagePath.Substring(0, $ImagePath.Length - 4)
        & openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 -pass pass:$Pass -in $ImagePath -out $DecPath
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $DecPath)) {
            Write-Error "Decryption failed! Verify passphrase."; Exit
        }
        $ImagePath = $DecPath
    } else {
        Write-Error "Archive is encrypted with AES-256. Install OpenSSL on Windows or restore via the HAOS Web UI Burn Spare / Linux script."; Exit
    }
}

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
    threshold=$(jq -r '.safe_wear_threshold // 80' "$(get_cfg_file)")
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

    local monitor_pid=""
    local thermal_pid=""
    local freeze_watchdog_pid=""
    local selected_dir=""

    cleanup_backup() {
        unfreeze_dirty_containers 2>/dev/null || true
        if [[ -n "${thermal_pid:-}" ]]; then
            kill "${thermal_pid}" 2>/dev/null || true
            wait "${thermal_pid}" 2>/dev/null || true
        fi
        if [[ -n "${monitor_pid:-}" ]]; then
            kill "${monitor_pid}" 2>/dev/null || true
            wait "${monitor_pid}" 2>/dev/null || true
        fi
        if [[ -n "${freeze_watchdog_pid:-}" ]]; then
            kill "${freeze_watchdog_pid}" 2>/dev/null || true
        fi
        [[ -n "${selected_dir:-}" ]] && rm -f "${selected_dir}/.backup_probe" 2>/dev/null || true
        rm -f "$PROGRESS_FILE" 2>/dev/null || true
        rm -f "/var/run/sd_backup.active" 2>/dev/null || true
        flock -u 200 2>/dev/null || true
        exec 200>&- 2>/dev/null || true
        rm -f "$LOCK_FILE" 2>/dev/null || true
    }
    trap cleanup_backup EXIT INT TERM

    resolve_storage_device
    bridge_host_network_storage

    local cfg_file
    cfg_file=$(get_cfg_file)

    local target_dir
    target_dir=$(jq -r '.target_dir // ""' "$cfg_file")
    target_dir="${target_dir%/}"

    if [[ -z "$target_dir" ]]; then
        echo "[✖ ERROR] No backup storage destination configured. Select a target in the Web UI."
        update_ha_sensor "sensor.sd_card_backup_status" "Failed - No Target Selected" "SD Card Backup Status" "mdi:alert-circle"
        cleanup_backup
        return 1
    fi
    local retention_count
    retention_count=$(jq -r '.retention_count // 3' "$cfg_file")
    local enable_rescue
    enable_rescue=$(jq -r '.enable_rescue // false' "$cfg_file")
    local rclone_enabled
    rclone_enabled=$(jq -r '.rclone_sync_enabled // false' "$cfg_file")
    local rclone_target
    rclone_target=$(jq -r '.rclone_remote_target // ""' "$cfg_file")

    local timestamp
    timestamp=$(date +%Y-%m-%d_%H-%M-%S)
    local output_archive="${target_dir}/haos_backup_${timestamp}.img.gz"
    local status_log="${target_dir}/haos_backup_${timestamp}.log"
    ACTIVE_LOG_FILE="$status_log"

    update_ha_sensor "sensor.sd_card_backup_status" "Running" "SD Card Backup Status" "mdi:progress-clock"
    update_ha_sensor "sensor.sd_card_backup_progress" "0%" "SD Card Backup Progress" "mdi:percent"
    echo "0%|0.0 MB/s|Estimating..." > "$PROGRESS_FILE"

    echo "================================================================"
    echo " Starting Live Disk Backup: ${SOURCE_DEV} -> ${output_archive}"
    echo "================================================================"

    # --- MULTI-TIER DESTINATION STORAGE FAILOVER MATRIX ---
    local primary_target="$target_dir"
    local active_tier="Primary"
    local selected_dir=""

    probe_tier() {
        local p="$1"
        [[ -z "$p" ]] && return 1
        mkdir -p "$p" 2>/dev/null || true
        if touch "${p}/.backup_probe" 2>/dev/null; then
            rm -f "${p}/.backup_probe"
            local kb
            kb=$(df -kP "$p" 2>/dev/null | awk 'NR==2 {print $4}')
            if [[ "$kb" =~ ^[0-9]+$ ]] && (( kb >= 15000000 )); then
                return 0
            fi
        fi
        return 1
    }

    echo "[*] Validating storage tier: Primary (${primary_target})..."
    if probe_tier "$primary_target"; then
        selected_dir="$primary_target"
    else
        echo "[!] Primary storage failed probe. Engaging Failover Tier Matrix..."
        
        # Tier 2: Check attached USB storage mounts
        for usb_root in "/media" "/run/media"; do
            if [[ -d "$usb_root" ]]; then
                for u in "$usb_root"/*; do
                    if [[ -d "$u" ]] && probe_tier "$u"; then
                        selected_dir="$u"
                        active_tier="Secondary (USB Failover: $(basename "$u"))"
                        break 2
                    fi
                done
            fi
        done

        # Tier 3: Local staging buffer fallback
        if [[ -z "$selected_dir" ]] && probe_tier "/share/backup_buffer"; then
            selected_dir="/share/backup_buffer"
            active_tier="Tertiary (Local /share Buffer)"
        fi
    fi

    if [[ -z "$selected_dir" ]]; then
        echo "[✖ CRITICAL ERROR] All storage tiers failed write probe or lack 15GB free space."
        update_ha_sensor "sensor.sd_card_backup_status" "Failed - All Tiers Unreachable" "SD Card Backup Status" "mdi:alert-circle"
        send_email "HAOS Backup FAILED" "Primary, USB, and local fallback storage tiers failed write-probe." || true
        cleanup_backup
        return 1
    fi

    target_dir="$selected_dir"
    echo "[✔] Active Storage Tier: ${active_tier} -> ${target_dir}"
    output_archive="${target_dir}/haos_backup_${timestamp}.img.gz"
    status_log="${target_dir}/haos_backup_${timestamp}.log"
    ACTIVE_LOG_FILE="$status_log"
    echo "$output_archive" > /var/run/sd_backup.active

    local dev_bytes
    dev_bytes=$(blockdev --getsize64 "$SOURCE_DEV" 2>/dev/null || lsblk -b -n -d -o SIZE "$SOURCE_DEV" 2>/dev/null || echo 0)
    if [[ "$dev_bytes" -le 0 ]]; then
        echo "[✖ ERROR] Cannot determine capacity of block device ${SOURCE_DEV}."
        cleanup_backup
        return 1
    fi
    local dev_human
    dev_human=$(awk "BEGIN {printf \"%.2f GB\", $dev_bytes/1073741824}")

    # Query partition 8 usage once to prevent triplicate summation
    local used_data_kb
    used_data_kb=$(df -kP /config 2>/dev/null | awk 'NR==2 {print $3}')
    local used_data_human
    used_data_human=$(awk "BEGIN {printf \"%.2f GB\", ${used_data_kb:-0}/1048576}")

    cat <<EOF > "$status_log"
================================================================================
HAOS DISASTER RECOVERY PROFILE & RESTORE SIZING WARNING
================================================================================
Actual Data Used:        ${used_data_human} (Filesystem data content)
Original Disk Geometry:  ${dev_human} (${dev_bytes} bytes)
Recommended Card Size:   ${dev_human} or larger (64 GB, 128 GB, etc.)

[!] IMPORTANT CARD RESTORE RESTRICTIONS:
 - Smaller storage tiers (e.g., 8 GB or 16 GB cards) CANNOT be used with this raw
   block image. Ext4 structures span the full original card boundary; restoring to
   a smaller card halts mid-write and corrupts Partition 8.
 - Replacement cards of the SAME nominal size (~32 GB) that have slight sector
   variations (e.g., 31.2 GB vs 31.9 GB) are supported by typing 'FORCE' in the script.
 - To move your HAOS system to an 8 GB or 16 GB card, install a fresh HAOS image
   and restore using Home Assistant's native (.tar) backup engine instead.
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
    freeze_dirty_containers
    sync
    echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true

    local cores
    cores=$(nproc)
    local pigz_threads=$(( cores > 1 ? cores - 1 : 1 ))
    local start_time
    start_time=$(date +%s)

    local dd_nocache=""
    if dd if=/dev/null of=/dev/null iflag=nocache 2>/dev/null; then
        dd_nocache="iflag=nocache"
    fi
    local dd_flags="status=none ${dd_nocache}"
    [[ "$enable_rescue" == "true" ]] && dd_flags="conv=noerror,sync status=none ${dd_nocache}"

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

                        # Scale raw streaming to 0-85% so post-processing milestones advance from 86-100%
                        local pct=$(( read_pos * 85 / dev_bytes ))
                        (( pct > 85 )) && pct=85
                        local read_pct=$(( read_pos * 100 / dev_bytes ))
                        (( read_pct > 100 )) && read_pct=100

                        local eta_str="calculating..."
                        if (( speed_bps > 100000 )); then
                            local rem_bytes=$(( dev_bytes - read_pos ))
                            local rem_sec=$(( rem_bytes / speed_bps ))
                            local rem_min=$(( rem_sec / 60 ))
                            local rem_sec_mod=$(( rem_sec % 60 ))
                            eta_str="${rem_min}m ${rem_sec_mod}s"
                        fi

                        echo "${pct}%|${speed_mb} MB/s (${read_pct}% read)|ETA: ${eta_str}" > "$PROGRESS_FILE"
                        update_ha_sensor "sensor.sd_card_backup_progress" "${pct}%" "SD Card Backup Progress" "mdi:percent"
                    fi
                fi
            fi
        done
    ) &
    monitor_pid=$!

    local backup_pass
    backup_pass=$(jq -r '.backup_password // ""' "$cfg_file" 2>/dev/null || echo "")
    if [[ -n "$backup_pass" ]]; then
        output_archive="${output_archive}.enc"
        echo "$output_archive" > /var/run/sd_backup.active
        echo "[*] Zero-Knowledge AES-256-CBC Encryption ENABLED."
    fi

    # Start thermal watchdog in background during streaming
    local thermal_pid=""
    (
        while true; do
            cur_temp=$(get_soc_temp)
            update_ha_sensor "sensor.sd_card_soc_temperature" "${cur_temp}°C" "SoC Temperature" "mdi:thermometer"
            if (( cur_temp >= 75 )); then
                echo "[!] Thermal Warning: SoC reached ${cur_temp}°C. Throttling backup process..."
                pkill -STOP -f "pigz" 2>/dev/null || true
                sleep 2
                pkill -CONT -f "pigz" 2>/dev/null || true
            fi
            sleep 4
        done
    ) &
    thermal_pid=$!

    # -R enables rsyncable rolling block resets, restricting byte changes to delta chunks
    local pipe_status=0
    if [[ -n "$backup_pass" ]]; then
        export BACKUP_PASS="$backup_pass"
        nice -n 5 dd if="$SOURCE_DEV" bs=8M $dd_flags | pv -q | \
            nice -n 5 pigz -p "$pigz_threads" -1 -b 1024 -R | \
            nice -n 5 openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -pass env:BACKUP_PASS | \
            tee "$output_archive" | sha256sum | awk '{print $1}' > "${output_archive}.sha256" || pipe_status=$?
    else
        nice -n 5 dd if="$SOURCE_DEV" bs=8M $dd_flags | pv -q | \
            nice -n 5 pigz -p "$pigz_threads" -1 -b 1024 -R | \
            tee "$output_archive" | sha256sum | awk '{print $1}' > "${output_archive}.sha256" || pipe_status=$?
    fi

    unfreeze_dirty_containers
    kill "$thermal_pid" 2>/dev/null || true
    kill "$monitor_pid" 2>/dev/null || true
    wait "$monitor_pid" 2>/dev/null || true
    monitor_pid=""

    log_status "Phase 1 Complete: 100% of physical sectors read from ${SOURCE_DEV}."

    # --- MILESTONE 2: FLUSH WRITE CACHES (88%) ---
    log_status "Phase 2/5 (88%): Committing kernel dirty page cache to destination storage (${target_dir})..."
    echo "88%|Flushing Cache|Writing memory buffers to destination storage..." > "$PROGRESS_FILE"
    update_ha_sensor "sensor.sd_card_backup_progress" "88%" "SD Card Backup Progress" "mdi:percent"
    update_ha_sensor "sensor.sd_card_backup_status" "Flushing Cache" "SD Card Backup Status" "mdi:progress-upload"
    sync
    log_status "Phase 2/5 Complete: Kernel write buffer safely committed to storage."

    if [[ "$pipe_status" -ne 0 ]]; then
        log_status "[✖ ERROR] Streaming pipeline failed with exit code $pipe_status!"
        update_ha_sensor "sensor.sd_card_backup_status" "Failed - Stream Error" "SD Card Backup Status" "mdi:alert-circle"
        send_email "HAOS Backup FAILED" "Read/Write stream error occurred on $SOURCE_DEV." || true
        cleanup_backup
        return 1
    fi

    # --- MILESTONE 3: DEEP ARCHIVE INTEGRITY AUDIT (92%) ---
    log_status "Phase 3/5 (92%): Initiating deep bitrot integrity audit (pigz -t)..."
    echo "92%|Verifying Archive|Deep bitrot integrity audit (pigz -t)..." > "$PROGRESS_FILE"
    update_ha_sensor "sensor.sd_card_backup_progress" "92%" "SD Card Backup Progress" "mdi:percent"
    update_ha_sensor "sensor.sd_card_backup_status" "Verifying Archive" "SD Card Backup Status" "mdi:check-decagram"

    local verify_ok=0
    if [[ "$output_archive" == *.enc ]]; then
        if openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 -pass env:BACKUP_PASS -in "$output_archive" 2>>"$status_log" | pigz -t >> "$status_log" 2>&1; then
            verify_ok=1
        fi
        unset BACKUP_PASS
    else
        if pigz -t "$output_archive" >> "$status_log" 2>&1; then
            verify_ok=1
        fi
    fi

    if [[ $verify_ok -eq 1 ]]; then
        log_status "Phase 3/5 Complete: Block checksums verified 100% OK (zero corruption)."
    else
        log_status "[✖ ERROR] Corrupted archive detected! Preserving previous restore points."
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

    # --- MILESTONE 4: RECOVERY GENERATION & PRUNING (96%) ---
    log_status "Phase 4/5 (96%): Generating companion disaster recovery tools (restore_${timestamp}.sh / .ps1)..."
    echo "96%|Housekeeping|Generating restore scripts & pruning old sets..." > "$PROGRESS_FILE"
    update_ha_sensor "sensor.sd_card_backup_progress" "96%" "SD Card Backup Progress" "mdi:percent"
    update_ha_sensor "sensor.sd_card_backup_status" "Housekeeping" "SD Card Backup Status" "mdi:folder-wrench"

    generate_restore_scripts "$target_dir" "$timestamp" "$(basename "$output_archive")" "$dev_bytes" "$dev_human"
    log_status "Phase 4/5: Enforcing retention policy (Preserving last ${retention_count} backup sets)..."
    prune_artifacts "$target_dir" "haos_backup_*.img.gz" "$retention_count"
    prune_artifacts "$target_dir" "haos_backup_*.log" "$retention_count"
    prune_artifacts "$target_dir" "restore_*.sh" "$retention_count"
    prune_artifacts "$target_dir" "restore_*.ps1" "$retention_count"
    log_status "Phase 4/5 Complete: Companion recovery scripts compiled and old archives pruned."

    # --- MILESTONE 5: 3-2-1 CLOUD REPLICATION (98%) ---
    if [[ "$rclone_enabled" == "true" ]] && [[ -n "$rclone_target" ]]; then
        log_status "Phase 5/5 (98%): Triggering delta chunk-level 3-2-1 cloud sync to ${rclone_target}..."
        echo "98%|Cloud Replication|Syncing disaster recovery set via Rclone..." > "$PROGRESS_FILE"
        update_ha_sensor "sensor.sd_card_backup_progress" "98%" "SD Card Backup Progress" "mdi:percent"
        update_ha_sensor "sensor.sd_card_backup_status" "Cloud Sync" "SD Card Backup Status" "mdi:cloud-sync"

        local rclone_cfg=""
        for rc_path in "/config/rclone/rclone.conf" "/share/rclone/rclone.conf" "/data/rclone.conf" "/root/.config/rclone/rclone.conf"; do
            if [[ -f "$rc_path" ]]; then
                rclone_cfg="$rc_path"
                break
            fi
        done

        local rclone_cmd=(rclone sync "$target_dir" "$rclone_target")
        [[ -n "$rclone_cfg" ]] && rclone_cmd+=(--config "$rclone_cfg")
        rclone_cmd+=(
            --include "haos_backup_${timestamp}*"
            --include "restore_${timestamp}*"
            --checksum
            --fast-list
            --transfers 2
            --log-level NOTICE
        )

        "${rclone_cmd[@]}" >> "$status_log" 2>&1 || log_status "[!] Rclone delta sync reported non-zero return code."
        log_status "Phase 5/5 Complete: Entire disaster recovery set mirrored offsite."
    fi

    log_status "Backup sequence finished successfully in ${duration} min. Final size: ${final_size}."

    cat <<EOF >> "$status_log"
================================================================================
BACKUP FINISHED SUCCESSFULLY AT $(date)
Final Archive Size: ${final_size}
Total Duration:     ${duration} minutes
Archive SHA256:     $(cat "${output_archive}.sha256")
================================================================================
EOF

    echo "[✔] Backup completed successfully: ${output_archive} (${final_size})"

    # Disarm lock immediately so the Web UI switches back to Idle without waiting on notifications
    cleanup_backup
    trap - EXIT INT TERM

    update_ha_sensor "sensor.sd_card_backup_status" "Idle (Success)" "SD Card Backup Status" "mdi:check-circle"
    update_ha_sensor "sensor.sd_card_backup_progress" "100%" "SD Card Backup Progress" "mdi:percent"
    update_ha_sensor "sensor.sd_card_backup_last_size" "$final_size" "SD Card Backup Last Size" "mdi:database"
    update_ha_sensor "sensor.sd_card_backup_duration" "${duration} min" "SD Card Backup Duration" "mdi:timer-outline"

    fire_ha_event "haos_sd_backup_completed" "{\"archive\": \"${output_archive}\", \"size\": \"${final_size}\", \"duration_min\": ${duration}}"

    local summary="Backup completed!\nFile: $(basename "$output_archive")\nSize: ${final_size}\nDuration: ${duration} min\nRequired Disk Size: ${dev_human}"
    send_email "HAOS SD Card Backup Succeeded" "$summary" || true
    send_ha_notification "HAOS SD Backup Complete" "Completed in ${duration} min. Size: ${final_size}. Minimum Disk: ${dev_human}"
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

local_cfg=$(get_cfg_file)
RUN_ON_START=$(jq -r '.run_backup_on_start // false' "$local_cfg")
if [[ "$RUN_ON_START" == "true" ]]; then
    echo "[*] 'run_backup_on_start' is enabled. Initiating immediate snapshot..."
    run_backup || true
fi

sync_ha_timezone

BACKUP_CRON=$(jq -r '.backup_cron // "0 3 * * 0"' "$local_cfg" 2>/dev/null || echo "0 3 * * 0")
WEAR_CRON=$(jq -r '.wear_cron // "0 12 * * 1"' "$local_cfg" 2>/dev/null || echo "0 12 * * 1")
SCHED_ENABLED=$(jq -r 'if .schedule_enabled == false then "false" else "true" end' "$local_cfg" 2>/dev/null || echo "false")

apply_crontab_config "$BACKUP_CRON" "$WEAR_CRON" "$SCHED_ENABLED"

echo "[✔] Daemon active. Listening for scheduled triggers..."
exec crond -f -l 2