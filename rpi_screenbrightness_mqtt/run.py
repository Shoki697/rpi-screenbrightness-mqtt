# Copyright 2024 bitconnect
# Author: Tobias Perschon
# License: GNU GPLv3, see LICENSE

import configparser, sys, time, subprocess, threading
from socket import gethostname
import paho.mqtt.client as mqtt
from evdev import InputDevice, categorize, ecodes

class rpiSBmqtt:

    def __init__(self, config_path):
        self._config = configparser.ConfigParser()
        if len(self._config.read(config_path)) == 0:
            raise RuntimeError(
                'Failed to find configuration file at {0}, is the application properly installed?'.format(config_path))
        self._mqttbroker = self._config.get('mqtt', 'broker')
        self._mqttuser = self._config.get('mqtt', 'user')
        self._mqttpassword = self._config.get('mqtt', 'password')
        self._mqttconnectedflag = False
        self._mqtt_state_topic = self._config.get('mqtt', 'state_topic').replace('${HOSTNAME}', gethostname())
        self._mqtt_command_topic = self._config.get('mqtt', 'command_topic').replace('${HOSTNAME}', gethostname())
        self._mqtt_brightness_state_topic = self._config.get('mqtt', 'brightness_state_topic').replace('${HOSTNAME}', gethostname())
        self._mqtt_brightness_command_topic = self._config.get('mqtt', 'brightness_command_topic').replace('${HOSTNAME}', gethostname())
        self._mqtt_touch_event_topic = self._config.get('mqtt', 'touch_event_topic').replace('${HOSTNAME}', gethostname())
        self._mqtt_clientid = self._config.get('mqtt', 'clientid').replace('${HOSTNAME}', gethostname())
        self._console_output = self._config.getboolean('misc', 'debug')
        self._ddcutil_command = self._config.get('ddcutil', 'command', fallback='ddcutil')
        self._touch_device_path = self._config.get('touchscreen', 'device_path', fallback='/dev/input/event0')

        self._monitor_on = True

    def _print(self, message):
        if self._console_output:
            print(message)

    def set_brightness(self, brightness):
        try:
            subprocess.run([self._ddcutil_command, 'setvcp', '10', str(brightness)], check=True)
            self._print(f"Brightness set to {brightness}")
        except subprocess.CalledProcessError as e:
            self._print(f"Failed to set brightness: {e}")

    def get_brightness(self):
        try:
            result = subprocess.run([self._ddcutil_command, 'getvcp', '10'], capture_output=True, text=True, check=True)
            for line in result.stdout.split('\n'):
                if 'Brightness' in line:
                    return int(line.split()[-1])
        except subprocess.CalledProcessError as e:
            self._print(f"Failed to get brightness: {e}")
        return 0

    def set_monitor_power(self, power_on):
        command = 'on' if power_on else 'off'
        try:
            subprocess.run([self._ddcutil_command, 'setvcp', 'D6', '01' if power_on else '04'], check=True)
            self._monitor_on = power_on
            self._print(f"Monitor power set to {command.upper()}")
        except subprocess.CalledProcessError as e:
            self._print(f"Failed to set monitor power: {e}")

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            self._print("Connected!")
            self._mqttconnectedflag = True
            client.subscribe(self._mqtt_brightness_command_topic)
            client.subscribe(self._mqtt_command_topic)
        else:
            self._mqttconnectedflag = False
            self._print("Could not connect. Return code: " + str(reason_code))

    def on_message(self, client, userdata, msg):
        payload = str(msg.payload.decode("utf-8"))
        topic = msg.topic

        if topic == self._mqtt_command_topic:
            self._print("power: " + str(payload))
            if payload == "ON":
                self.set_monitor_power(True)
            elif payload == "OFF":
                self.set_monitor_power(False)
            self.sendStatus(client)

        if topic == self._mqtt_brightness_command_topic:
            self._print("brightness: " + str(payload))
            self.set_brightness(int(payload))
            self.sendStatus(client)

    def on_disconnect(self, client, userdata, flags, reason_code, properties):
        self._print("disconnected. reason:  " + str(reason_code))
        self._mqttconnectedflag = False

    def sendStatus(self, client):
        brightness = self.get_brightness()
        payload_brightness = str(brightness)
        payload_power = "ON" if self._monitor_on else "OFF"
        self._print("Publishing " + payload_brightness + " to topic: " + self._mqtt_brightness_state_topic + " ...")
        client.publish(self._mqtt_brightness_state_topic, payload_brightness, 0, False)
        self._print("Publishing " + payload_power + " to topic: " + self._mqtt_state_topic + " ...")
        client.publish(self._mqtt_state_topic, payload_power, 0, False)

    def touch_event_listener(self, client):
        dev = InputDevice(self._touch_device_path)
        self._print(f"Listening for touch events on {self._touch_device_path}...")
        for event in dev.read_loop():
            if event.type == ecodes.EV_KEY and event.code == ecodes.BTN_TOUCH:
                touch_type = "DOWN" if event.value == 1 else "UP"
                self._print(f"Touch event detected: {touch_type}")
                client.publish(self._mqtt_touch_event_topic, touch_type, 0, False)

    def run(self):
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, self._mqtt_clientid)
        client.on_connect = self.on_connect
        client.on_message = self.on_message
        client.on_disconnect = self.on_disconnect
        client.username_pw_set(self._mqttuser, self._mqttpassword)
        self._print("Connecting to broker " + self._mqttbroker)
        client.loop_start()
        try:
            client.connect(self._mqttbroker, 1883, 60)
        except:
            self._print("Connection failed!")
            exit(1)

        threading.Thread(target=self.touch_event_listener, args=(client,)).start()

        while not self._mqttconnectedflag:  # wait in loop
            self._print("Waiting for connection...")
            time.sleep(1)
        while self._mqttconnectedflag:
            try:
                self.sendStatus(client)
            except Exception as e:
                self._print("exception")
                self._print(str(e))

            time.sleep(10)

        client.loop_stop()  # Stop loop
        client.disconnect()  # disconnect


if __name__ == '__main__':
    print('Starting rpi (touch)screen brightness control via mqtt.')
    config_path = '/etc/rpi_screenbrightness_mqtt.conf'
    if len(sys.argv) == 2:
        config_path = sys.argv[1]
    rpiControl = rpiSBmqtt(config_path)
    rpiControl.run()
