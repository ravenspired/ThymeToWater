from machine import Pin, ADC
import time

class Cd4051:
    def __init__(self, pin_a, pin_b, pin_c, adc_pin, vref=3.3, settle_us=5):
        self.pin_a = Pin(pin_a, Pin.OUT)
        self.pin_b = Pin(pin_b, Pin.OUT)
        self.pin_c = Pin(pin_c, Pin.OUT)
        self.adc = ADC(Pin(adc_pin))
        self.vref = vref
        self.settle_us = settle_us  # brief settle time after switching channel

        self.channel = -1  # force first select_channel call to actually write pins
        self.select_channel(0)

    def select_channel(self, ch):
        if ch < 0 or ch > 7:
            raise ValueError("channel must be 0-7")
        if ch != self.channel:
            self.pin_a.value(ch & 0x01)
            self.pin_b.value((ch >> 1) & 0x01)
            self.pin_c.value((ch >> 2) & 0x01)
            self.channel = ch
            time.sleep_us(self.settle_us)

    def read_channel(self, ch):
        """Select a channel and return its raw ADC value (0-65535)."""
        self.select_channel(ch)
        return self.adc.read_u16()

    def read_voltage(self, ch):
        """Select a channel and return its value converted to volts."""
        raw = self.read_channel(ch)
        return raw * self.vref / 65535

    def read_all(self):
        """Return a list of raw readings for channels 0-7, in order."""
        return [self.read_channel(ch) for ch in range(8)]