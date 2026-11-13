#!/bin/zsh
# Image the CDJ-1000MK3's SD card: unmount it, then read the raw device into
# ~/cdj-card/NAME.img with a progress bar. Reads the card only, never writes it.
#
#   spikes/cdj_card_snap.sh 10-third-cd /dev/disk4
#   spikes/cdj_card_snap.sh 10-third-cd            # finds the card itself
#
# Without a device it picks the one external disk of 64 MB or more (some card readers
# show a small "USB Special Disk" of their own, which is ignored); if there are none or
# several it stops and lists them. Give the device to be sure.
#
# Needs sudo (the raw device is root-only) and pv (brew install pv). Run it in a
# normal Terminal window: sudo needs a terminal to ask for the password.
set -eu
source ${0:A:h}/cdj_card_common.zsh

name=${1:?usage: cdj_card_snap.sh NAME [/dev/diskN]   (writes ~/cdj-card/NAME.img)}
out=$HOME/cdj-card/$name.img
[[ -e $out ]] && { echo "$out already exists; pick another name" >&2; exit 1; }

if [[ -n ${2:-} ]]; then
  disk=$(require_card_disk "$2")
else
  # The card: the one external physical disk of a card's size.
  disks=()
  for d in ${(f)"$(diskutil list external physical | awk '/^\/dev\/disk/ {print $1}')"}; do
    (( $(disk_bytes "$d") >= MIN_BYTES )) && disks+=($d)
  done
  (( ${#disks} == 1 )) || {
    echo "expected one external disk of 64 MB or more, found ${#disks}: $disks" >&2
    echo "pass the card's device as the second argument (see: diskutil list external physical)" >&2
    exit 1
  }
  disk=${disks[1]}
fi

size=$(disk_bytes "$disk")
diskutil info "$disk" | grep -E "Media Name|Disk Size|Total Size"
diskutil unmountDisk "$disk"
mkdir -p "$HOME/cdj-card"
sudo dd if="${disk/disk/rdisk}" bs=4m 2>/dev/null | pv -s "$size" > "$out"
shasum -a 256 "$out"
echo "wrote $out"
