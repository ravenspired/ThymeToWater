"""
mapper.py - interactively map the 16 mux channels to connector numbers 1-16.

Run this on the Pico (stop main.py first with Ctrl+C / Stop in Thonny).
For each connector number, short that connector's signal to GND with the
learning cable. The script finds which raw mux channel dropped below 0.1 V
and records it. Results go to mixer.json (logical channel -> raw index 0-15),
which main.py loads.

Ctrl+C at any time: whatever has been captured so far is kept, the rest
are left unmapped (reported as null by main.py).

Raw index: 0-7 = mux A (GP2/3/4, ADC GP28), 8-15 = mux B (GP19/20/21, ADC GP27).
Unshorted channels must idle ABOVE 0.1 V (sensor output or pull-up),
otherwise they will look shorted.
"""
import json
import time

from CD4051 import Cd4051

MIXER_FILE = "mixer.json"
THRESHOLD_V = 0.1
NUM_SAMPLES = 10
DROP = 2
NUM_CHANNELS = 16
CONFIRM_SCANS = 2   # consecutive scans that must agree before accepting

muxes = (
    Cd4051(pin_a=2, pin_b=3, pin_c=4, adc_pin=28),
    Cd4051(pin_a=19, pin_b=20, pin_c=21, adc_pin=27),
)


def trimmed_mean(samples, drop=DROP):
    n = len(samples)
    if n > drop + 2:
        mean = sum(samples) / n
        ranked = sorted(samples, key=lambda x: abs(x - mean))
        samples = ranked[:-drop]
    return sum(samples) / len(samples)


def read_volts(idx):
    mux = muxes[idx >> 3]
    ch = idx & 7
    mux.select_channel(ch)
    mux.adc.read_u16()  # discard first read after switching
    raw = trimmed_mean([mux.read_channel(ch) for _ in range(NUM_SAMPLES)])
    return raw * mux.vref / 65535


def low_channels():
    """Raw indexes of every channel currently below the threshold."""
    return [i for i in range(NUM_CHANNELS) if read_volts(i) < THRESHOLD_V]


def name(idx):
    return "mux {} ch{}".format("AB"[idx >> 3], idx & 7)


def wait_release():
    """Block until no channel is shorted."""
    warned = None
    while True:
        low = low_channels()
        if not low:
            return
        if low != warned:
            print("  Remove the cable - still low on: " + ", ".join(name(i) for i in low))
            warned = low
        time.sleep_ms(200)


def wait_for_short(assigned):
    """Block until exactly one unassigned channel is shorted; return its raw index."""
    last, hits, warned = None, 0, None
    while True:
        low = low_channels()
        if len(low) == 1 and low[0] not in assigned:
            hits = hits + 1 if low[0] == last else 1
            last = low[0]
            if hits >= CONFIRM_SCANS:
                return low[0]
        else:
            last, hits = None, 0
            if low and low != warned:
                if len(low) > 1:
                    print("  Multiple channels low ({}), connect only one".format(
                        ", ".join(name(i) for i in low)))
                else:
                    print("  {} is already mapped to channel {}".format(
                        name(low[0]), assigned[low[0]]))
                warned = low
        time.sleep_ms(50)


def save(mixer):
    with open(MIXER_FILE, "w") as f:
        json.dump({str(k): v for k, v in mixer.items()}, f)


def main():
    mixer = {}      # logical (1-16) -> raw index (0-15)
    assigned = {}   # raw index -> logical
    print("Channel mapper. Ctrl+C to stop early and keep what's captured.\n")

    try:
        for logical in range(1, NUM_CHANNELS + 1):
            wait_release()
            print("Connect learning cable to channel {}".format(logical))
            idx = wait_for_short(assigned)
            mixer[logical] = idx
            assigned[idx] = logical
            save(mixer)
            print("  OK: channel {} = {} (raw index {}), {:.3f} V\n".format(
                logical, name(idx), idx, read_volts(idx)))
    except KeyboardInterrupt:
        print("\nStopped early.")

    if not mixer:
        print("Nothing captured; {} left unchanged.".format(MIXER_FILE))
        return

    save(mixer)
    print("\nSaved {} ({} of {} mapped):".format(MIXER_FILE, len(mixer), NUM_CHANNELS))
    for n in range(1, NUM_CHANNELS + 1):
        if n in mixer:
            print("  channel {:2d} -> raw {:2d} ({})".format(n, mixer[n], name(mixer[n])))
        else:
            print("  channel {:2d} -> unmapped".format(n))


main()