#!/usr/bin/env bash
# Build what the emulator tests install, for both CPUs, into tests/emu/build/:
#   pm/<label>/<arch>/Plugin-Manager-G2-<version>-mpc-<arch>.zip   the Manager, as the release workflow packages it, but reading its
#                                                                    catalog from the fixture server (http://10.0.2.2:8731/) instead of the live site
#   fx/<label>/                                                      what the fixture server serves: the zips, catalog.full.json and patches.json
# usage: tests/emu/build.sh [--ref GIT_REF] [--label NAME]
#   --ref    build the Manager's C from that commit instead of the working tree (a test that must fail before a fix runs against it)
#   --label  the name of this build (default: "work", or the ref)
# The skin (vst/build/skin, all three theme folders) is built once by vst/build_themes.sh and reused: it takes about 15 minutes and does
# not depend on the C. Needs Docker with arm32v7 and arm64 emulation, and an mpc-vst-plugins checkout (MPC_VST).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"; pm="$(cd "$here/../.." && pwd)"
MV="${MPC_VST:-$(cd "$pm/../.." && pwd)/open-mpc}"
ref=""; label=""
while [ $# -gt 0 ]; do case "$1" in --ref) ref=$2; shift 2 ;; --label) label=$2; shift 2 ;; *) echo "usage: build.sh [--ref REF] [--label NAME]" >&2; exit 2 ;; esac; done
[ -n "$label" ] || label="${ref:-work}"; label="${label//\//-}"
out="$here/build"; fx="$out/fx/$label"; mkdir -p "$fx" "$out/pm/$label"
U="$(id -u):$(id -g)"
FIX=http://10.0.2.2:8731
VA=9.0.0; VB=9.0.1   # the Manager under test, and the "newer" one the fixture catalog offers (the same code)

skin="$pm/vst/build/skin/OpenMPC - VST - Plugin Manager"
if [ ! -d "$skin/Plugin Skins.light" ] || [ ! -f "$pm/vst/build/params.h" ]; then
    echo "== skin: vst/build_themes.sh (about 15 minutes, once)"
    MPC_VST="$MV" "$pm/vst/build_themes.sh" armv7 >/dev/null
fi

# the C and its parameter table (vst/build/params.h, from vst/params.json): the working tree's, or a commit's
src="$pm"
if [ -n "$ref" ]; then
    src="$out/pm/$label/src"; rm -rf "$src"; mkdir -p "$src"
    git -C "$pm" archive "$ref" src vst | tar -xf - -C "$src"
fi
python3 "$MV/tools/gen_vst.py" "$src/vst/vst.json" --params-h >/dev/null
eval "$(python3 "$MV/tools/gen_vst.py" "$pm/vst/vst.json" --shell | grep -E '^(CFLAGS|CFLAGS_ARM|CFLAGS_A64|LIBS)=')"
DEF="-DCATALOG_URL='\"$FIX/catalog.json\"' -DCATALOG_URL_FALLBACK='\"\"' -DPATCHES_URL='\"$FIX/patches.json\"' -DPATCHES_URL_FALLBACK='\"\"'"

compile() {   # compile <arch> <version>: the same command as mpc-vst-plugins' tools/build_port.sh
    local arch=$1 ver=$2 img plat xf o
    case $arch in armv7) img=arm32v7/gcc:11-bullseye; plat=linux/arm/v7; xf="$CFLAGS_ARM" ;; aarch64) img=arm64v8/gcc:12-bookworm; plat=linux/arm64; xf="$CFLAGS_A64" ;; esac
    o="$out/pm/$label/$arch/$ver"; mkdir -p "$o"
    docker run --rm --platform $plat -u "$U" -v "$src":/b:ro -v "$MV":/mv:ro -v "$o":/o -w /b $img bash -euc "
        gcc -O2 -w -fPIC -shared -fvisibility=hidden -std=gnu11 $CFLAGS $xf $DEF -DPM_VERSION='\"$ver\"' -Ivst/build -I/mv/wrapper \
            src/manager.c /mv/wrapper/vst2_wrap.c $LIBS -lpthread -ldl -Wl,--no-undefined -o /o/openmpc_pm_g2.so
        strip /o/openmpc_pm_g2.so"
}
package() {   # package <arch> <version>: release.py, as the release workflow does
    local arch=$1 ver=$2 o="$out/pm/$label/$1/$2"
    rm -rf "$o/skin" "$o/dist"; mkdir -p "$o/skin"
    cp -a --reflink=auto "$skin" "$o/skin/"
    # the built skin is reused between builds, so the source tree's own edits to it are applied to this copy (a commit from before an
    # edit existed gets the skin as it was then)
    if [ -f "$src/vst/skin_taps.py" ]; then python3 "$src/vst/skin_taps.py" "$o/skin" >/dev/null; fi
    python3 "$MV/tools/release.py" --so "$o/openmpc_pm_g2.so" --skin "$o/skin/OpenMPC - VST - Plugin Manager" --entry "$pm/vst/build/pluginlist-entry.xml" \
        --version "$ver" --id plugin-manager-g2 --repo open-mpc/plugin-manager --license MIT --asset-name Plugin-Manager-G2 \
        --retire "OpenMPC - VST - Plugin Manager G2" --about "The Manager, built for the emulator tests." -o "$o/dist" >/dev/null
    rm -rf "$o/skin"
    ls "$o/dist"/*.zip >/dev/null
}
for arch in armv7 aarch64; do
    for ver in $VA $VB; do
        z="$out/pm/$label/$arch/$ver/dist/Plugin-Manager-G2-$ver-mpc-$arch.zip"
        if [ -f "$z" ] && [ -z "$(find "$src/src" "$src/vst/params.json" "$src/vst/skin_taps.py" "$skin" "$here/build.sh" -newer "$z" -print -quit)" ]; then echo "== Manager $label $ver $arch: up to date"; continue; fi
        echo "== Manager $label $ver $arch"
        compile $arch $ver; package $arch $ver
    done
done

# the fixture plugin (two versions) and addin, as real packages
echo "== fixture packages"
for arch in armv7 aarch64; do
    case $arch in armv7) img=arm32v7/gcc:11-bullseye; plat=linux/arm/v7 ;; aarch64) img=arm64v8/gcc:12-bookworm; plat=linux/arm64 ;; esac
    w="$out/fixture-work/$arch"; rm -rf "$w"; mkdir -p "$w/addin" "$w/skin/acme - VST - Emu Probe/Plugin Skins"
    docker run --rm --platform $plat -u "$U" -v "$here/src":/s:ro -v "$w":/o $img bash -euc "
        gcc -O2 -fPIC -shared -o /o/emu_probe.so /s/probe.c && gcc -O2 -fPIC -shared -o /o/addin/emu_addin.so /s/addin.c && strip /o/emu_probe.so /o/addin/emu_addin.so"
    echo '{}' > "$w/skin/acme - VST - Emu Probe/Plugin Skins/TUI.json"
    printf '<?xml version="1.0" encoding="utf-8"?>\n<plugincontent version="1.0">\n\t<identifier>acme.vst.emuprobe</identifier>\n\t<version>1.0.0.0</version>\n</plugincontent>\n' > "$w/skin/acme - VST - Emu Probe/version.xml"
    echo '<PLUGIN name="Emu Probe" descriptiveName="Emu Probe" format="VST" category="Synth" manufacturer="acme" version="1.0" file="/sdcard/Synths/acme - VST - Emu Probe/emu_probe.so" uid="456d5072" isInstrument="1" fileTime="0" infoUpdateTime="0" numInputs="0" numOutputs="2" isShell="0"/>' > "$w/entry.xml"
    for ver in 1.0.0 1.0.1; do
        python3 "$MV/tools/release.py" --so "$w/emu_probe.so" --skin "$w/skin/acme - VST - Emu Probe" --entry "$w/entry.xml" --version $ver \
            --id emu-probe --repo acme/mpc-vst-emu-probe --license MIT --about "A stand-in plugin for the emulator tests." -o "$w/dist" >/dev/null
    done
    printf 'ADDIN_ID=emu-addin\nADDIN_NAME="Emu addin"\nADDIN_SO=emu_addin.so\nADDIN_CONF=emu_addin.conf\nADDIN_FILES=""\n' > "$w/addin/addin.manifest"
    echo "# settings" > "$w/addin/emu_addin.conf"
    python3 "$MV/tools/release_addin.py" --dir "$w/addin" --version 1.0.0 --repo acme/mpc-addin-emu --license MIT --about "A stand-in addin for the emulator tests." -o "$w/dist" >/dev/null
done
python3 "$here/fixture.py" "$out" "$label" $VA $VB
git -C "$pm" rev-parse ${ref:-HEAD} > "$fx/commit" 2>/dev/null || true
