"""The scenarios: what a person does with the Plugin Manager on the device, and what must be true on the device afterwards.
Each starts from the ready snapshot: MPC running, the Manager (version A) on a track with its screen open, the catalog loaded."""
import time
from run import scenario, PM
from emu import Fail, log

PROBE = "acme - VST - Emu Probe"
MANAGER = "OpenMPC - VST - Plugin Manager"


@scenario
def fresh_install(t):
    """A fresh install of the Manager: MPC lists it, draws its skin, it reads the catalog and finds where to install."""
    dev = t.vm.read(PM + "/device.txt")
    t.has(dev, "problem: none", "device.txt")
    t.has(dev, "install target: %s" % t.synths, "device.txt")
    t.has(dev, "file=%s/%s/openmpc_pm_g2.so" % (t.synths, MANAGER), "device.txt names the plugin MPC loaded")
    st = t.state()
    t.eq(st["version"], "9.0.0", "the running version")
    t.eq((st["online"], st["loaded"], st["problem"]), (1, 1, ""), "online, catalog loaded, no problem")
    t.eq(sorted(p["id"] for p in st["plugins"]), ["emu-addin", "emu-probe", "faily", "plugin-manager-g2"], "the catalog's entries")
    t.eq(t.plugin("plugin-manager-g2", st).get("state"), "update", "the Manager sees its own update")
    t.eq(t.plugin("emu-probe", st).get("state"), "install", "a plugin that is not installed")
    t.screen_shows("OPEN MPC - PLUGIN MANAGER", "Emu Probe", "UPDATE ALL", "LOCATIONS", name="skin")
    t.has(t.settings_text(), "%s/%s/openmpc_pm_g2.so" % (t.synths, MANAGER), "MPC.settings lists the Manager")
    t.settings_ok()


@scenario
def plugin_install_update_remove(t):
    """Install a plugin with a tap, update it, remove it: files, MPC.settings and the result file each time."""
    folder = "%s/%s" % (t.synths, PROBE)
    t.fx.catalog(emu_probe=["1.0.0"])
    st = t.refresh()
    t.eq(t.plugin("emu-probe", st).get("latest"), "1.0.0", "the catalog offers 1.0.0")
    # the rows are sorted newest first: Emu Probe is the first row once only it and older entries are on offer; find it by its text
    hits = t.vm.find("Emu Probe")
    if t.check(hits, "the list shows Emu Probe"):
        t.vm.tap(1128, hits[0][1] + 16)       # its INSTALL button, on the row's right
    st = t.state(secs=10, until=lambda s: t.plugin("emu-probe", s).get("state") == "q_install", why="the tap queued the install")
    t.shot("queued")
    res, lg = t.apply()
    t.eq(res.get("emu-probe"), ("ok", "install 1.0.0"), "result of the install")
    t.check(t.vm.exists(folder + "/emu_probe.so"), "the plugin's .so is in its folder")
    t.has(t.settings_text(), folder + "/emu_probe.so", "MPC.settings lists the plugin")
    t.settings_ok()
    t.hasnt(t.vm.out("ls -a '%s'" % t.synths), ".pm-s", "no safety copy is left behind")

    t.fx.catalog()                              # 1.0.1 appears
    st = t.reopen()
    p = t.plugin("emu-probe", st)
    t.eq((p.get("installed"), p.get("state")), ("1.0.0", "update"), "the Manager sees the installed plugin and its update")
    t.queue("emu-probe")
    res, lg = t.apply()
    t.eq(res.get("emu-probe"), ("ok", "install 1.0.1"), "result of the update (the result file says install for an update too)")
    t.has(t.vm.read(folder + "/mpc-plugin.json"), '"version": "1.0.1"', "the folder's manifest")
    t.eq(t.settings_text().count(folder + "/emu_probe.so"), 1, "MPC.settings lists the plugin once")

    st = t.reopen()
    t.eq(t.plugin("emu-probe", st).get("installed"), "1.0.1", "the Manager sees 1.0.1")
    t.queue("emu-probe", "remove")
    res, lg = t.apply()
    t.eq(res.get("emu-probe", ("",))[0], "ok", "result of the removal")
    t.check(not t.vm.exists(folder), "the plugin's folder is gone")
    t.hasnt(t.settings_text(), "emu_probe.so", "MPC.settings after the removal")
    t.has(t.settings_text(), "openmpc_pm_g2.so", "MPC.settings still lists the Manager")
    t.settings_ok()


def mpc_env(t):
    """LD_PRELOAD of the running MPC."""
    return t.vm.out("tr '\\0' '\\n' < /proc/$(pidof MPC | cut -d' ' -f1)/environ | grep '^LD_PRELOAD=' || true").strip()


@scenario
def addin_install_remove(t):
    """Install an addin and remove it: its folder, MPC really preloading it after the restart, and MPC's own preloads kept."""
    before = mpc_env(t)
    t.queue("emu-addin")
    res, lg = t.apply()
    t.eq(res.get("emu-addin"), ("ok", "install 1.0.0"), "result of the install")
    t.check(t.vm.exists("/data/mpc-addins/emu-addin/emu_addin.so"), "the addin's .so is in /data/mpc-addins/emu-addin")
    env = mpc_env(t)
    t.has(env, "emu_addin.so", "MPC's LD_PRELOAD after the install")
    for lib in [x for x in before.replace("LD_PRELOAD=", "").split(":") if x]:
        t.has(env, lib, "MPC's LD_PRELOAD keeps what it had")
    t.has(t.vm.read("/tmp/emu-addin.loaded"), "MPC", "the addin ran inside MPC")
    t.settings_ok()

    st = t.reopen()
    t.eq(t.plugin("emu-addin", st).get("installed"), "1.0.0", "the Manager sees the addin")
    t.queue("emu-addin", "remove")
    t.vm.sh("rm -f /tmp/emu-addin.loaded")
    res, lg = t.apply()
    t.eq(res.get("emu-addin", ("",))[0], "ok", "result of the removal")
    t.check(not t.vm.exists("/data/mpc-addins/emu-addin"), "the addin's folder is gone")
    t.eq(mpc_env(t), before, "MPC's LD_PRELOAD is what it was")
    t.check(not t.vm.exists("/tmp/emu-addin.loaded"), "MPC no longer loads the addin")


@scenario
def self_update(t):
    """The Manager updates itself: the new build is what MPC loads afterwards, with nothing left over."""
    own = "%s/%s" % (t.synths, MANAGER)
    t.queue("plugin-manager-g2")
    res, lg = t.apply(secs=900)
    t.eq(res.get("plugin-manager-g2"), ("ok", "install 9.0.1"), "result of the self-update")
    t.has(t.vm.read(own + "/mpc-plugin.json"), '"version": "9.0.1"', "the folder's manifest")
    t.eq(t.vm.out("ls -a '%s' | grep -c -E '^\\.pm-s|^\\.skin\\.' || true" % t.synths).strip(), "0", "safety copies left in the Synths folder")
    t.eq(t.vm.out("ls -a '%s' | grep -c -E '^\\.skin\\.|\\.new$|\\.old$' || true" % own).strip(), "0", "staging folders left in the Manager's folder")
    t.eq(t.settings_text().count("openmpc_pm_g2.so"), 1, "MPC.settings lists the Manager once")
    t.settings_ok()
    st = t.reopen()
    t.eq(st["version"], "9.0.1", "the version MPC loads after the update")
    t.eq(t.plugin("plugin-manager-g2", st).get("state"), "installed", "the Manager no longer offers its own update")
    t.screen_shows("OPEN MPC - PLUGIN MANAGER", "LOCATIONS", name="after-update")


@scenario
def long_press(t):
    """A slow press on a button counts once. The skin bound each trigger twice (an action on touch-down, a Button on release), so a
    press held longer than the quarter-second double-press window queued and then unqueued, or turned two pages (vst/skin_taps.py)."""
    hits = t.vm.find("Emu Probe")
    if not t.check(hits, "the list shows Emu Probe"): return
    y = hits[0][1] + 16
    want = "q_install"
    for hold in (0.08, 0.6, 0.08, 1.5, 0.35):
        t.vm.tap(1128, y, hold=hold, after=1.5)
        got = t.plugin("emu-probe").get("state")
        t.eq(got, want, "after a %.2f s press on INSTALL" % hold)
        if got != want:           # put it where the next press expects it
            t.queue("emu-probe", "install" if want == "q_install" else "cancel")
        want = "install" if want == "q_install" else "q_install"
    t.shot("after-presses")


def is_dark(t, name):
    """The Manager's screen is drawn with the dark skin (its page background, away from any text)."""
    img = t.vm.shot(None)
    t.shot(name)
    r, g, b = img.getpixel((640, 650))
    return r + g + b < 300


@scenario
def theme_survives_update(t):
    """A user who chose the dark theme still has it after the Manager updates itself, with no second restart asked for.
    (The package brings the light skin; the swap to the chosen one used to wait for another restart prompt after the update.)"""
    own = "%s/%s" % (t.synths, MANAGER)
    t.check(not is_dark(t, "light-at-start"), "the fresh install shows the light theme")
    t.vm.tap(532, 770)                                   # SETTINGS
    hits = t.vm.find("THEME")
    if not t.check(hits, "SETTINGS shows THEME"): return
    t.vm.tap(1160, hits[0][1] + 16)                      # its CHANGE button
    t.eq(t.vm.read(t.settings.split("/Settings/")[0] + "/.openmpcpm-theme").strip(), "dark", "the saved theme choice")
    res, lg = t.apply()
    t.eq(t.vm.read(own + "/Plugin Skins/.theme").strip(), "dark", "the skin in place after applying the theme")
    st = t.reopen()
    t.check(is_dark(t, "dark-chosen"), "the dark theme shows after the restart")

    t.queue("plugin-manager-g2")
    res, lg = t.apply(secs=900)
    t.eq(res.get("plugin-manager-g2", ("",))[0], "ok", "result of the self-update")
    t.eq(t.vm.read(own + "/Plugin Skins/.theme").strip(), "dark", "the skin in place after the self-update")
    st = t.reopen()
    t.eq(st["version"], "9.0.1", "the version MPC loads after the update")
    t.check(is_dark(t, "dark-after-update"), "the dark theme shows after the update")
    time.sleep(8)                                        # the old build prepared a second restart here
    st = t.state()
    t.eq((st.get("ready"), st.get("queued")), (0, 0), "nothing is waiting for another restart (ready, queued)")


# ---- drives, full disks, power cuts

def leftovers(t, where):
    """Safety copies and staging folders in a plugin location."""
    return t.vm.out("ls -a '%s' | grep -E '^\\.pm-s|\\.new$|\\.old$|\\.part$' || true" % where).split()


def add_drive(t, label, fs="exfat", opts="rw", mb=200, mkfs=""):
    """A removable drive as MPC would mount it: /media/<label>, here a loop image. Returns its mount point."""
    img = "/data/emu-%s.img" % label.replace(" ", "_")
    mk = {"exfat": "mkfs.exfat -q %s" % mkfs, "ext4": "mke2fs -q -t ext4 -F %s" % mkfs, "vfat": "mkfs.vfat %s" % mkfs}[fs]
    t.vm.sh("dd if=/dev/zero of=%s bs=1M count=0 seek=%d 2>/dev/null && %s %s && mkdir -p '/media/%s' && mount -o loop,%s %s '/media/%s'"
            % (img, mb, mk, img, label, opts, img, label))
    return "/media/" + label


def location(t, path, st=None):
    for r in (st or t.state()).get("locations", []):
        if r["path"] == path: return r
    return {}


def api_install(t, pid, **kw):
    t.queue(pid)
    return t.apply(**kw)


@scenario
def drive_name_with_space(t):
    """An exFAT card called "EMU CARD" (big clusters, as cards come): register it, install to it, update, remove."""
    mnt = add_drive(t, "EMU CARD", "exfat", "rw", mkfs="-c 128K")
    syn = mnt + "/Synths"; folder = "%s/%s" % (syn, PROBE)
    t.fx.catalog(emu_probe=["1.0.0"])
    st = t.refresh()
    r = location(t, syn, st)
    t.eq((r.get("usable"), r.get("candidate")), (1, 1), "the card is offered for REGISTER (usable, candidate)")
    t.vm.api("register?path=%s" % syn.replace(" ", "%20"), post=True)
    res, lg = t.apply()
    t.eq(res.get("loc-1", ("",))[0], "ok", "result of the registration")
    t.has(t.settings_text(), "<Location>%s</Location>" % syn, "MPC.settings lists the card's Synths folder")
    t.settings_ok()
    st = t.reopen()
    names = [x["path"] for x in st["targets"]]
    if not t.check(syn in names, "the card is an install target after the restart: %s" % names): return
    t.vm.api("target?k=%d" % names.index(syn), post=True)
    time.sleep(2)
    res, lg = api_install(t, "emu-probe")
    t.eq(res.get("emu-probe"), ("ok", "install 1.0.0"), "result of the install to the card")
    t.check(t.vm.exists(folder + "/emu_probe.so"), "the plugin's .so is on the card")
    t.has(t.settings_text(), 'file="%s/emu_probe.so"' % folder, "MPC.settings names the file on the card")
    t.eq(leftovers(t, syn), [], "nothing left over on the card")
    t.fx.catalog()
    st = t.reopen()
    p = t.plugin("emu-probe", st)
    t.eq((p.get("installed"), p.get("state")), ("1.0.0", "update"), "the Manager sees the plugin on the card and its update")
    res, lg = api_install(t, "emu-probe")
    t.eq(res.get("emu-probe"), ("ok", "install 1.0.1"), "result of the update on the card")
    t.has(t.vm.read(folder + "/mpc-plugin.json"), '"version": "1.0.1"', "the folder's manifest on the card")
    t.check(not t.vm.exists(t.synths + "/" + PROBE), "the update did not make a second copy on the internal drive")
    st = t.reopen()
    t.queue("emu-probe", "remove")
    res, lg = t.apply()
    t.eq(res.get("emu-probe", ("",))[0], "ok", "result of the removal")
    t.check(not t.vm.exists(folder), "the plugin's folder on the card is gone")
    t.settings_ok()


@scenario
def exfat_noexec_drive(t):
    """A drive MPC mounted noexec (a Force's SSD): the Manager says why it cannot be used, and installs go to the internal drive."""
    mnt = add_drive(t, "EMUSSD", "exfat", "rw,noexec")
    syn = mnt + "/Synths"
    t.vm.sh("mkdir -p '%s'" % syn)
    t.fx.catalog(emu_probe=["1.0.0"])
    st = t.refresh()
    r = location(t, syn, st)
    t.eq((r.get("usable"), r.get("candidate")), (0, 0), "the drive is not usable and not offered for REGISTER")
    t.has(r.get("state", ""), "noexec", "the reason shown for the drive")
    rc, out = t.vm.sh("wget -q -O - --header 'X-PM: 1' --post-data '' 'http://127.0.0.1:8790/api/register?path=%s' 2>&1" % syn, check=False)
    t.check(rc != 0, "registering the noexec drive is refused")
    t.eq([x["path"] for x in st["targets"]], [t.synths], "the install targets")
    t.eq(st.get("problem"), "", "no problem is reported: the internal drive is usable")
    res, lg = api_install(t, "emu-probe")
    t.eq(res.get("emu-probe"), ("ok", "install 1.0.0"), "result of the install")
    t.check(t.vm.exists("%s/%s/emu_probe.so" % (t.synths, PROBE)), "the plugin went to the internal drive")
    t.eq(t.vm.out("ls -A '%s'" % syn).strip(), "", "nothing was written to the noexec drive")
    # the same drive listed by MPC (the user added it in MPC's own settings): still refused, with the reason, and nothing breaks
    t.vm.sh("systemctl stop acvs; sed -i 's|</SynthContentLocations>|  <Location>%s</Location>\\n    </SynthContentLocations>|' %s; systemctl start acvs" % (syn, t.settings))
    st = t.reopen()
    r = location(t, syn, st)
    t.eq(r.get("usable"), 0, "listed by MPC, the noexec drive is still not usable")
    t.eq([x["path"] for x in st["targets"]], [t.synths], "the install targets with the drive listed")
    t.has(t.vm.read(PM + "/device.txt"), "noexec", "device.txt says why")


@scenario
def readonly_addins(t):
    """/data/mpc-addins cannot be written: an addin is refused before MPC stops, with that as the reason; plugins still install."""
    t.vm.sh("mkdir -p /data/mpc-addins && mount --bind /data/mpc-addins /data/mpc-addins && mount -o remount,ro,bind /data/mpc-addins")
    t.fx.catalog(emu_probe=["1.0.0"])
    st = t.refresh()
    pid = t.vm.out("pidof MPC").strip()
    t.queue("emu-addin")
    st = t.prepare()
    t.shot("refused")
    t.eq(st.get("ready"), 0, "the addin install is not offered for a restart")
    t.has(st.get("status", ""), "mpc-addins", "the message names the addins folder")
    t.hasnt(st.get("status", ""), "no install location", "the message does not blame the install location (#5)")
    t.eq(t.vm.out("pidof MPC").strip(), pid, "MPC kept running")
    t.queue("emu-addin", "cancel")
    res, lg = api_install(t, "emu-probe")
    t.eq(res.get("emu-probe"), ("ok", "install 1.0.0"), "a plugin still installs")


@scenario
def power_cut_mid_apply(t):
    """Power is lost while an update is keeping its safety copy (#46). After the reboot the plugin is as it was, and the update
    then goes through: what the cut left behind does not block it."""
    folder = "%s/%s" % (t.synths, PROBE)
    t.fx.catalog(emu_probe=["1.0.0"])
    t.refresh()
    res, lg = api_install(t, "emu-probe")
    t.eq(res.get("emu-probe"), ("ok", "install 1.0.0"), "result of the install")
    # the user's own files make the folder too big for a copy in memory: the safety copy goes on the drive, which takes a while
    t.vm.sh("dd if=/dev/zero of='%s/user.bin' bs=1M count=230 2>/dev/null; sync" % folder, timeout=600)
    t.fx.catalog()
    st = t.reopen()
    t.eq(t.plugin("emu-probe", st).get("state"), "update", "the update is offered")
    t.queue("emu-probe")
    st = t.prepare()
    if not t.check(st.get("ready"), "the update is prepared: %s" % st.get("status")): return
    t.launch()
    end = time.time() + 240; seen = []
    while time.time() < end and not seen:
        seen = [x for x in leftovers(t, t.synths) if x.startswith(".pm-s")]
        if not seen: time.sleep(0.5)
    if not t.check(seen, "a safety copy was being made on the drive"): return
    time.sleep(1.5)
    t.vm.sh("sync", check=False)          # what was copied so far is on the disk, as on a card that was written for a minute
    t.vm.kill()
    log("%s: power cut with %s in place" % (t.vm.instance, seen))
    t.vm.start()
    end = time.time() + 300
    while t.vm.sh("pidof MPC", check=False)[0] != 0:
        if time.time() > end: raise Fail("MPC did not start after the power cut")
        time.sleep(3)
    time.sleep(15)
    left = [x for x in leftovers(t, t.synths) if x.startswith(".pm-s")]
    t.check(left, "the cut left a partial safety copy behind (%s)" % left)
    t.has(t.vm.read(folder + "/mpc-plugin.json"), '"version": "1.0.0"', "the plugin's folder after the cut")
    t.settings_ok()
    st = t.reopen()
    p = t.plugin("emu-probe", st)
    t.eq((p.get("installed"), p.get("state")), ("1.0.0", "update"), "after the reboot the Manager sees 1.0.0 and offers the update")
    res, lg = api_install(t, "emu-probe", secs=900)
    t.eq(res.get("emu-probe"), ("ok", "install 1.0.1"), "result of the update after the power cut")
    t.has(t.vm.read(folder + "/mpc-plugin.json"), '"version": "1.0.1"', "the folder's manifest")
    t.eq(leftovers(t, t.synths), [], "nothing left over")
    t.settings_ok()


@scenario
def catalog_retry(t):
    """The catalog cannot be reached at first (Wi-Fi still joining when MPC opens the project): the Manager tries again on its own."""
    t.fx.put(".fail", "/catalog.json 2\n")           # the next two requests get no answer
    t.vm.api("refresh", post=True)
    st = t.state(secs=30, until=lambda s: not s.get("busy") and s.get("online") == 0, why="the Manager is offline after a dropped fetch")
    t.eq(st.get("online"), 0, "offline after the dropped fetch")
    t.shot("offline")
    st = t.state(secs=45, until=lambda s: s.get("online") == 1 and not s.get("busy"), why="the Manager came back online on its own")
    t.eq((st.get("online"), len(st.get("plugins", [])) >= 4), (1, True), "online with the catalog, with nobody tapping REFRESH")
    t.has(t.fx.requests(), "DROPPED /catalog.json", "the fixture really dropped the request")
