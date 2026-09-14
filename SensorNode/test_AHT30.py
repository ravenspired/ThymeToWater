import time
from AHT30 import Aht30

sensor = Aht30(sda_pin=14, scl_pin=15, i2c_id=1)

while True:
    temp_c, humidity = sensor.read()
    print("Temp: {:.2f} C   Humidity: {:.2f} %".format(temp_c, humidity))
    time.sleep(.01)