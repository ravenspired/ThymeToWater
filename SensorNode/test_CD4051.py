from CD4051 import Cd4051
import time

mux = Cd4051(pin_a=2, pin_b=3, pin_c=4, adc_pin=28)

sample_period_ms = 100
channel_period_ms = 1000
channel = 0

last_sample = time.ticks_ms()
last_channel_change = time.ticks_ms()

while True:
    now = time.ticks_ms()

    if time.ticks_diff(now, last_sample) >= sample_period_ms:
        last_sample = now
        raw = mux.read_channel(channel)
        print("CH{}: raw={:5d}  V={:.3f}".format(channel, raw, raw * 3.3 / 65535))

    if time.ticks_diff(now, last_channel_change) >= channel_period_ms:
        last_channel_change = now
        channel = (channel + 1) % 8