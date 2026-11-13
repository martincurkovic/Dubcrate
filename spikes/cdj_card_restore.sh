#!/bin/zsh
# Write an image from cdj_card_snap.sh back onto the card. THIS OVERWRITES THE WHOLE CARD.
# Used to put the card back after a test, or to load a test image made by
# cdj_card_testimage.py.
#
#   spikes/cdj_card_restore.sh 10-third-cd-20-tracks /dev/disk4
#   spikes/cdj_card_restore.sh 10-third-cd-20-tracks /dev/disk4 --dry-run   # checks only
#
# The device is always given (never guessed), must be a whole external disk, and must be
# exactly the size of the image (so it is the same card). You type the device to confirm.
# The card is read back and compared with the image afterwards, then ejected.
#
# Needs sudo and pv, in a normal Terminal window (sudo asks for the password).
set -eu
source ${0:A:h}/cdj_card_common.zsh

name=${1:?usage: cdj_card_restore.sh NAME /dev/diskN [--dry-run]   (reads ~/cdj-card/NAME.img)}
img=$HOME/cdj-card/$name.img
[[ -f $img ]] || { echo "no such image: $img" >&2; exit 1; }
disk=$(require_card_disk "${2:?give the card's device: see diskutil list external physical}")

img_bytes=$(stat -f %z "$img")
disk_size=$(disk_bytes "$disk")
[[ $img_bytes == $disk_size ]] || {
  echo "$disk is $disk_size bytes but the image is $img_bytes: not the same card, stopping" >&2
  exit 1
}

echo "Image:  $img ($img_bytes bytes)"
diskutil info "$disk" | grep -E "Media Name|Disk Size|Total Size"
if [[ ${3:-} == --dry-run ]]; then echo "dry run: nothing written"; exit 0; fi

echo
echo "This ERASES everything on $disk and writes the image over it."
read "answer?Type the device to continue (${disk}): "
[[ $answer == $disk ]] || { echo "not confirmed, nothing written" >&2; exit 1; }

diskutil unmountDisk "$disk"
pv -s "$img_bytes" "$img" | sudo dd of="${disk/disk/rdisk}" bs=4m 2>/dev/null
sync

echo "Reading it back to check..."
want=$(shasum -a 256 < "$img")
got=$(sudo dd if="${disk/disk/rdisk}" bs=4m 2>/dev/null | pv -s "$img_bytes" | shasum -a 256)
[[ $want == $got ]] || { echo "READ-BACK DOES NOT MATCH THE IMAGE: do not trust this card" >&2; exit 1; }
echo "card matches $name.img"
diskutil eject "$disk"
