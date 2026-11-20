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
    t.hasnt(t.vm.out("ls -a '%s'" % t.synths), ".pm-snap", "no safety copy is left behind")

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
    t.eq(t.vm.out("ls -a '%s' | grep -c -E '^\\.pm-snap|^\\.skin\\.' || true" % t.synths).strip(), "0", "safety copies left in the Synths folder")
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
