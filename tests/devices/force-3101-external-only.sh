set -e
# A Force on MPC OS 3.10.1 with VST2 Support on, whose internal drive cannot take a plugin (here read-only; a full one is the same to
# the Manager) and whose SSD is fine. 3.10.1 loads plugins from "<drive>/VST2 Plugins" on any drive, so the SSD is where installs go.
# The Manager tried the internal drives only and said "No install location" with a usable drive attached.
S=/media/az01-internal/Settings/Force; mkdir -p $S /media/az01-internal/Synths /media/SSD/Synths
printf '<?xml version="1.0" encoding="UTF-8"?>\r\n<PROPERTIES>\r\n  <VALUE name="Enhancements.Enabled" val="1"/>\r\n  <VALUE name="Enhancements.VST2Support" val="1"/>\r\n  <VALUE name="SynthContentLocations">\r\n    <SynthContentLocations>\r\n      <Location>/media/az01-internal/Synths</Location>\r\n      <Location>/media/SSD/Synths</Location>\r\n    </SynthContentLocations>\r\n  </VALUE>\r\n  <VALUE name="pluginList-arm">\r\n    <KNOWNPLUGINS/>\r\n  </VALUE>\r\n</PROPERTIES>\r\n' > $S/MPC.settings
printf 'x /media tmpfs rw 0 0\nx /media/az01-internal ext4 ro,relatime 0 0\n/dev/sda1 /media/SSD exfat rw,noexec,relatime 0 0\n' > /tmp/pm-mounts
printf "#!/bin/sh\nexit 0\n" > /usr/bin/systemctl; chmod +x /usr/bin/systemctl
