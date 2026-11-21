"""The MPC Sample (AC50) machine. It runs MPC Sample 1.x, not MPC OS 3.x, and has no plugin browser, so the Plugin Manager cannot be
opened on it: none of the Manager's scenarios apply. What a user does on it today is tested instead (sample_scenarios.py): a plugin
installed with its package's install.sh onto the SD card, the card's Synths folder made executable by hand, the plugin put into the
project's mixer by the RACK addin's apply helper, and what is left after a reboot.

The machine snapshot ("machine" in state/<instance>/machine/, as for the other machines) has MPC Sample running on its main screen
with an SD card in. What is done here that a real Sample already has:
  * the SD card: the harness's own disk (shim/qemu-system-aarch64), exFAT, labelled MPCSD. QEngine's udev rule tells edisksd to
    ignore every virtio disk; a rule of the harness's lets this one through, and MPC then mounts it itself, as /media/MPCSD with
    edisksd's defaults (noexec, nosuid, nodev, nosymfollow), as it does a card on the device.
  * a Synths folder on the card, and MPC's settings file: MPC Sample writes "MPC Sample.settings" only when it has something to
    keep, and the first thing is the card's Synths folder, which it adds to SynthContentLocations when it starts.
  * a root filesystem that cannot be remounted read-write (the ext4 "read-only" feature): the device's cannot, so the addin
    installer puts its drop-in in /run, as it did on the device. QEngine's image could be remounted, and an addin installed in the
    emulator would then have survived a reboot that it does not survive on the device.
  * QEngine's LD_PRELOAD drop-in (override.conf) renamed so that it sorts before the addin installer's, as on the other machines.
The network: see emu.VM._serial_console (MPC Sample switches wired networking off)."""
import json, os, shutil, time
import emu
from emu import Fail, log

CODES = ("AC50",)
SERVICE = "ac50"
PROCESS = "MPC Sample"
SETTINGS = "/data/Settings/Akai/MPC Sample/MPC Sample.settings"
CARD = "/media/MPCSD"
SYNTHS = CARD + "/Synths"
PROJECT = "/data/TempProjects/Temp-Project.xpj"           # MPC's working copy of the open project
USER_DISK = "/dev/disk/by-id/virtio-pmuser"
UDEV_RULE = ('# The emulator\'s stand-in for the MPC Sample\'s SD card: QEngine tells edisksd to ignore every virtio disk\n'
             'SUBSYSTEM=="block", ENV{ID_SERIAL}=="pmuser", ENV{EDISKSD_IGNORE}="0"\n')
QENGINE_DROPIN = ("d=/etc/systemd/system/%s.service.d; if [ -f $d/override.conf ]; then mv $d/override.conf $d/10-qengine.conf; fi" % SERVICE)


def is_sample_code(code):
    return code in CODES


def is_sample(instance):
    code = os.environ.get("PM_PRODUCT_CODE", "")
    if not code:
        try: code = emu.instance_env(instance).get("PRODUCT_CODE", "")
        except OSError: return False
    return is_sample_code(code)


def mpc_pid(vm):
    return vm.out("pidof '%s' || true" % PROCESS).strip().split(" ")[0]


def wait_mpc(vm, secs=120, other_than=""):
    """MPC Sample running (a new process when other_than is given); returns its pid."""
    end = time.time() + emu.slow(secs)
    while time.time() < end:
        p = mpc_pid(vm)
        if p and p != other_than: return p
        time.sleep(2)
    raise Fail("MPC Sample is not running")


def restart_mpc(vm, settle=8):
    """Restart MPC's service and wait until the new process is up and has opened its project."""
    old = mpc_pid(vm)
    vm.sh("systemctl restart %s" % SERVICE, timeout=int(emu.slow(120)))
    p = wait_mpc(vm, other_than=old)
    time.sleep(emu.slow(settle))
    return p


def screen_text(img):
    """OCR of the 320x240 screen, enlarged (the Manager's OCR is tuned for 1280x800)."""
    import io, subprocess
    from PIL import Image
    im = img.convert("L").resize((img.width * 4, img.height * 4), Image.LANCZOS)
    buf = io.BytesIO(); im.save(buf, "PNG")
    return subprocess.run(["tesseract", "-", "-", "--psm", "11"], input=buf.getvalue(), stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL).stdout.decode("utf-8", "replace")


def main_screen(vm, secs=90, save=None):
    """Wait for MPC Sample's main screen (the sample's Trim, Tune and Filter pages along the top, Start, End and Loop below); returns
    what was read."""
    end = time.time() + emu.slow(secs); txt = ""
    while time.time() < end:
        img = vm.settle(10)
        txt = screen_text(img)
        if save: img.save(save)
        if sum(w in txt for w in ("Trim", "Tune", "Filter", "Start", "End", "Loop")) >= 4: return txt   # OCR misses the lit tab
        time.sleep(3)
    raise Fail("MPC Sample never showed its main screen (last read: %r)" % txt.strip()[:200])


def make(instance, machine_id):
    """Boot the instance from its own disks, set up the card and the stand-ins above, and save the snapshot "machine" with MPC
    Sample running on its main screen."""
    vm = emu.VM(instance, "machine")
    shutil.rmtree(vm.dir, ignore_errors=True)
    t0 = time.time()
    try:
        vm.start()
        boot = time.time() - t0
        wait_mpc(vm, 300)
        mpc_up = time.time() - t0
        main_screen(vm, save=os.path.join(vm.dir, "boot.png"))
        screen_up = time.time() - t0
        firmware = vm.out("cat /tmp/com.akai.mpcsample.version 2>/dev/null || true").strip()
        if vm.exists(SETTINGS): log("%s: MPC Sample has written its settings before it had a card" % instance)
        root = vm.out("findmnt -n -o SOURCE /").strip()
        vm.sh("mount -o remount,rw / && mkdir -p /etc/udev/rules.d && cat > /etc/udev/rules.d/95-pm-sdcard.rules && %s && sync"
              % QENGINE_DROPIN, input=UDEV_RULE.encode())
        vm.sh("mount -o remount,ro / && tune2fs -O read-only %s >/dev/null" % root)
        if vm.sh("mount -o remount,rw /", check=False)[0] == 0: raise Fail("the root filesystem can still be remounted read-write")
        vm.sh("systemctl daemon-reload; mkfs.exfat -L MPCSD %s >/dev/null && udevadm control --reload && udevadm trigger --action=change %s "
              "&& udevadm settle" % (USER_DISK, USER_DISK))
        restart_mpc(vm)                                     # MPC mounts the card when it starts
        mounts = vm.read("/proc/mounts")
        card = [l for l in mounts.splitlines() if l.split(" ")[1:2] == [CARD]]
        if not card or "noexec" not in card[0]: raise Fail("MPC did not mount the card at %s as the device does: %r" % (CARD, card))
        vm.sh("mkdir -p %s" % SYNTHS)
        restart_mpc(vm)                                     # and writes its settings, with the card's Synths folder in them
        if "<Location>%s</Location>" % SYNTHS not in vm.read(SETTINGS):
            raise Fail("MPC did not add %s to its settings: %r" % (SYNTHS, vm.read(SETTINGS)))
        main_screen(vm, save=os.path.join(vm.dir, "machine.png"))
        vm.savevm("machine")
        info = {"id": machine_id, "kind": "sample", "arch": "aarch64", "synths": SYNTHS, "external_plugins": False,
                "boot_seconds": int(boot), "mpc_seconds": int(mpc_up), "screen_seconds": int(screen_up),
                "machine_seconds": int(time.time() - t0), "firmware": firmware, "card": card[0]}
        json.dump(info, open(os.path.join(vm.dir, "machine.json"), "w"))
        log("%s: machine ready in %ds (ssh after %ds, MPC Sample after %ds, its main screen after %ds)"
            % (instance, time.time() - t0, boot, mpc_up, screen_up))
        return info
    finally:
        vm.stop()


def prepare(instance, stage, minfo):
    """No Manager to install: the stage is a copy of the machine snapshot, which the scenarios resume."""
    import machine
    machine.copy_to(instance, stage)
    info = {k: minfo[k] for k in ("kind", "arch", "synths", "external_plugins", "boot_seconds", "mpc_seconds", "screen_seconds", "firmware")}
    info.update(machine=minfo["id"], prepare_seconds=0)
    return info
