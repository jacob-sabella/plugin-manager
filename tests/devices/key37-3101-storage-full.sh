set -e
# MPC Key 37 on MPC OS 3.10.1 with VST2 Support on (laid out as a real one, 2026-11-20): the content drive is /storage (also mounted at
# /media/acvs-content), /media/az01-internal is the small data partition, and no Synths folder is listed. /storage cannot take a plugin
# (read-only here; a full one is the same to the Manager). MPC does not scan /media/az01-internal/VST2 Plugins on this unit, so the
# Manager must not install there: the SD card, which 3.10.1 does scan, is the target.
S=/media/az01-internal/Settings/MPC; mkdir -p $S /storage /media/acvs-content "/media/0180-2800"
printf '<?xml version="1.0" encoding="UTF-8"?>\r\n<PROPERTIES>\r\n  <VALUE name="Enhancements.Enabled" val="1"/>\r\n  <VALUE name="Enhancements.VST2Support" val="1"/>\r\n  <VALUE name="SynthContentLocations">\r\n    <SynthContentLocations>\r\n    </SynthContentLocations>\r\n  </VALUE>\r\n  <VALUE name="pluginList-arm">\r\n    <KNOWNPLUGINS/>\r\n  </VALUE>\r\n</PROPERTIES>\r\n' > $S/MPC.settings
printf 'x /media tmpfs rw 0 0\n/dev/mmcblk0p8 /media/az01-internal ext4 rw,relatime 0 0\n/dev/mmcblk0p7 /media/acvs-content ext4 ro,relatime 0 0\n/dev/mmcblk0p7 /storage ext4 ro,relatime 0 0\n/dev/mmcblk1p1 /media/0180-2800 exfat rw,nosuid,nodev,noexec,relatime 0 0\n' > /tmp/pm-mounts
printf "#!/bin/sh\nexit 0\n" > /usr/bin/systemctl; chmod +x /usr/bin/systemctl
