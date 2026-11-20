#!/usr/bin/env python3
"""A trigger button answers a touch once.

The skin builder gives each trigger two things bound to its parameter: a "Mouse Down -> Toggle Switch" action and a Button
component. MPC runs both: the action when the finger lands, the Button when it lifts. One touch therefore reached the plugin as two
presses, as far apart as the touch was long. The wrapper and the Manager drop a second press within 250 ms, which hides it for a quick
tap only: a press held longer queued and unqueued an install, turned two pages, typed a letter twice (emulator traces on MPC 3.9.1
and 3.10.0, 2026-11-19: with either binding alone every touch is one press, whatever its length).

This drops the Mouse Down action from every trigger that has a Button on the same handle. The Button stays: it draws the pressed
image, sets the value rather than toggling it, acts when the finger lifts and not at all when the finger slides off first. The
"Enter Pressed" action (hardware) stays too.

  skin_taps.py <skin dir>     every TUI.json under it, in place (run by skin_post.py; safe to run again)
  skin_taps.py --selftest"""
import json
import os
import sys


def once(t):
    """Edit the parsed TUI.json `t` in place; returns how many triggers were changed."""
    n = 0

    def walk(o):
        nonlocal n
        if isinstance(o, dict):
            v = o.get("value")
            if isinstance(o.get("key"), str) and o["key"].startswith("shTrig_") and isinstance(v, dict) and isinstance(v.get("actions"), list):
                buttons = {c["componentData"]["data"].get("handleName") for c in v.get("componentsData", [])
                           if c.get("componentData", {}).get("type") == "Button"}
                keep = [a for a in v["actions"]
                        if not (a.get("onAction") == "Mouse Down" and a.get("handler") == "Toggle Switch" and a.get("handleName") in buttons)]
                if len(keep) != len(v["actions"]):
                    v["actions"] = keep
                    n += 1
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(t)
    return n


def selftest():
    act = lambda on, h="Data": {"version": 2, "onAction": on, "handler": "Toggle Switch", "handleName": h}   # noqa: E731
    comp = lambda typ, h="Data": {"componentData": {"type": typ, "data": {"handleName": h}}}                  # noqa: E731
    t = {"defs": [
        {"key": "shTrig_apply_x", "value": {"actions": [act("Mouse Down"), act("Enter Pressed")], "componentsData": [comp("Focus"), comp("Button")]}},
        {"key": "shTrig_nobutton", "value": {"actions": [act("Mouse Down")], "componentsData": [comp("Focus")]}},          # nothing else takes the touch
        {"key": "shTrig_other", "value": {"actions": [act("Mouse Down", "Open")], "componentsData": [comp("Button")]}},    # a different handle
        {"key": "shSwitch_x", "value": {"actions": [act("Mouse Down")], "componentsData": [comp("Button")]}}]}              # not a trigger
    assert once(t) == 1
    d = {x["key"]: [a["onAction"] for a in x["value"]["actions"]] for x in t["defs"]}
    assert d == {"shTrig_apply_x": ["Enter Pressed"], "shTrig_nobutton": ["Mouse Down"], "shTrig_other": ["Mouse Down"], "shSwitch_x": ["Mouse Down"]}, d
    assert once(t) == 0     # again: nothing left to change
    print("skin_taps OK")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
        sys.exit(0)
    total = 0
    for root, _, files in os.walk(sys.argv[1]):
        if "TUI.json" not in files:
            continue
        p = os.path.join(root, "TUI.json")
        t = json.load(open(p, encoding="utf-8"))
        k = once(t)
        if k:
            open(p, "w", encoding="utf-8").write(json.dumps(t, indent=4))   # as skin_post.py writes it
        total += k
    print("skin_taps: %d trigger(s) now answer a touch once" % total)
