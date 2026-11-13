# Shared by cdj_card_snap.sh and cdj_card_restore.sh (source this, do not run it).

MIN_BYTES=$((64 * 1024 * 1024))

# Size of a disk in bytes, from "Disk Size: 2.0 GB (2013265920 Bytes) (...)".
disk_bytes() {
  diskutil info "$1" | awk -F'[()]' '/Disk Size|Total Size/ {print $2; exit}' | awk '{print $1}'
}

# Normalise "disk4" or "/dev/disk4" to "/dev/disk4" and stop unless it is a whole, real,
# external disk: not the Mac's own, not a partition, not a mounted disk image.
require_card_disk() {
  local disk=/dev/${1#/dev/} info
  info=$(diskutil info "$disk") || { echo "no such disk: $disk" >&2; return 1; }
  print -r -- "$info" | grep -q "Device Location: *External" || { echo "$disk is not an external disk" >&2; return 1; }
  print -r -- "$info" | grep -q "Whole: *Yes" || { echo "$disk is a partition: give the whole disk (e.g. /dev/disk4)" >&2; return 1; }
  print -r -- "$info" | grep -q "Virtual: *No" || { echo "$disk is a disk image, not a card" >&2; return 1; }
  print -r -- "$disk"
}
