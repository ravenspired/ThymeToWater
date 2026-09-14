from machine import Pin, I2C
import time

class Aht30:
    AHT30_ADDR = 0x38

    CMD_INIT = bytearray([0xBE, 0x08, 0x00])
    CMD_TRIGGER = bytearray([0xAC, 0x33, 0x00])
    CMD_SOFT_RESET = bytearray([0xBA])

    def __init__(self, sda_pin, scl_pin, i2c_id=1, freq=100000, addr=AHT30_ADDR):
        self.i2c = I2C(i2c_id, sda=Pin(sda_pin), scl=Pin(scl_pin), freq=freq)
        self.addr = addr
        self._init_sensor()

    def _init_sensor(self):
        # power-on wait per datasheet
        time.sleep_ms(40)
        self.i2c.writeto(self.addr, self.CMD_INIT)
        time.sleep_ms(10)

    def soft_reset(self):
        self.i2c.writeto(self.addr, self.CMD_SOFT_RESET)
        time.sleep_ms(20)
        self._init_sensor()

    def _read_raw(self):
        self.i2c.writeto(self.addr, self.CMD_TRIGGER)
        time.sleep_ms(80)  # measurement time, datasheet says ~75-80ms

        data = self.i2c.readfrom(self.addr, 7)

        status = data[0]
        if status & 0x80:  # busy bit still set
            time.sleep_ms(20)
            data = self.i2c.readfrom(self.addr, 7)

        raw_humidity = (data[1] << 12) | (data[2] << 4) | (data[3] >> 4)
        raw_temp = ((data[3] & 0x0F) << 16) | (data[4] << 8) | data[5]

        return raw_humidity, raw_temp

    def read_humidity(self):
        """Return relative humidity in %."""
        raw_humidity, _ = self._read_raw()
        return (raw_humidity / (1 << 20)) * 100

    def read_temperature(self):
        """Return temperature in Celsius."""
        _, raw_temp = self._read_raw()
        return (raw_temp / (1 << 20)) * 200 - 50

    def read(self):
        """Return (temperature_C, humidity_%) in a single measurement."""
        raw_humidity, raw_temp = self._read_raw()
        humidity = (raw_humidity / (1 << 20)) * 100
        temperature = (raw_temp / (1 << 20)) * 200 - 50
        return temperature, humidity