#!/usr/bin/env python3
"""The data wheel stays on the card list (#11).

MPC gives the data wheel to the control that has hardware focus, and a touch moves the focus to the control touched. Every drawn
control accepts focus by default, so after any tap the wheel edited that button instead (Live III trace, 2026-11-22: the wheel's
clicks reached d_back, the details' BACK, as 0.01, 0.02 ...) and the invisible wheel control (layout kind `wheel`, bound to wsel)
never had it again. In the tab that holds a wheel control, this turns hardware focus off for every other control placed there, so
the wheel control is the only one that can take it. Touches do not need focus: taps work as before.

  skin_wheel.py <skin dir>     every TUI.json under it, in place (run by skin_post.py; safe to run again)
  skin_wheel.py --selftest"""
import json
import os
import sys


def once(t):
    """Edit the parsed TUI.json `t` in place; returns how many controls stopped taking focus."""
    n = 0
    for d in t["pageData"]["componentDefinitions"]["localComponentDefinitions"]:
        kids = d.get("value", {}).get("componentsData") or []
        types = [str(c.get("componentData", {}).get("type", "")) for c in kids]
        if not any(x.startswith("shWheel_") for x in types):
            continue
        for c, x in zip(kids, types):
            b = c.get("bounds")
            if isinstance(b, dict) and not x.startswith("shWheel_") and b.get("acceptsHWFocus") == "Yes":
                b["acceptsHWFocus"] = "No"
                n += 1
    return n


def selftest():
    kid = lambda ty: {"componentData": {"type": ty}, "bounds": {"acceptsHWFocus": "Yes"}}
    t = {"pageData": {"componentDefinitions": {"localComponentDefinitions": [
        {"key": "tab1", "value": {"componentsData": [kid("shButton_a"), kid("shWheel_8x8"), kid("shList_b")]}},
        {"key": "tab2", "value": {"componentsData": [kid("shButton_a")]}}]}}}
    assert once(t) == 2
    k1 = t["pageData"]["componentDefinitions"]["localComponentDefinitions"][0]["value"]["componentsData"]
    assert [c["bounds"]["acceptsHWFocus"] for c in k1] == ["No", "Yes", "No"]
    assert t["pageData"]["componentDefinitions"]["localComponentDefinitions"][1]["value"]["componentsData"][0]["bounds"]["acceptsHWFocus"] == "Yes"
    assert once(t) == 0
    print("skin_wheel selftest OK")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
    else:
        for root, _, files in os.walk(sys.argv[1]):
            if "TUI.json" in files:
                p = os.path.join(root, "TUI.json")
                t = json.load(open(p, encoding="utf-8"))
                n = once(t)
                open(p, "w", encoding="utf-8").write(json.dumps(t, indent=4))
                print("skin_wheel: %s: %d controls no longer take the wheel's focus" % (p, n))
