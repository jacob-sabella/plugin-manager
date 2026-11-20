#!/usr/bin/env python3
"""Work on one emulated MPC by hand (the tests use emu.py directly). Inside the pm-emu-host container:

  cli.py up INSTANCE STAGE [SNAPSHOT]   boot it (or resume a snapshot) and stay up; the other commands attach to it (docker exec)
  cli.py sh 'command'                   run in the guest
  cli.py tap OUT.png [X,Y | sN | hX,Y:SECS]...   taps (sN waits N seconds, hX,Y:S holds a press S seconds), then a screenshot
  cli.py mon 'hmp command'              the QEMU monitor (savevm TAG, info snapshots, ...)
  cli.py py 'python'                    run a snippet with `vm` in scope
tests/emu/vm on the host wraps these."""
import os, sys, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import emu

CUR = "/tmp/pm-emu-current.json"
cmd = sys.argv[1]
if cmd == "up":
    fx = emu.Fixture(sys.argv[3])
    vm = emu.VM(sys.argv[2], sys.argv[3])
    vm.start(sys.argv[4] if len(sys.argv) > 4 else None)
    json.dump({"instance": sys.argv[2], "stage": sys.argv[3]}, open(CUR, "w"))
    emu.log("up: %s/%s" % (sys.argv[2], sys.argv[3]))
    while vm.proc.poll() is None:
        time.sleep(1)
    sys.exit(0)
cur = json.load(open(CUR))
vm = emu.VM(cur["instance"], cur["stage"])
try:
    if cmd == "sh":
        rc, out = vm.sh(sys.argv[2], timeout=int(sys.argv[3]) if len(sys.argv) > 3 else 120, check=False)
        sys.stdout.write(out); sys.exit(rc)
    elif cmd == "tap":
        for s in sys.argv[3:]:
            if s[0] == "s": time.sleep(float(s[1:]))
            elif s[0] == "h":
                xy, secs = s[1:].split(":"); x, y = xy.split(","); vm.tap(int(x), int(y), hold=float(secs))
            else:
                x, y = s.split(","); vm.tap(int(x), int(y))
        vm.shot(os.path.abspath(sys.argv[2]))
    elif cmd == "mon":
        print(vm.mon(sys.argv[2]))
    elif cmd == "py":
        exec(sys.argv[2])
finally:
    emu.shutdown()
