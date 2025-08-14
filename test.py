# Edge Impulse - OpenMV Image Classification Example
#
# This work is licensed under the MIT license.
# Copyright (c) 2013-2024 OpenMV LLC. All rights reserved.
# https://github.com/openmv/openmv/blob/master/LICENSE

import ujson
import sensor
import time
import tf
import uos, gc
from machine import LED
#import guider
import network

#以下是手动补齐的软件包
import usocket as socket
import ustruct as struct
import utime

class MQTTException(Exception):
    pass

class MQTTClient:

    def __init__(self, client_id, server, port=1883, user=None, password=None, keepalive=60):
        self.client_id = client_id
        self.server = server
        self.port = port
        self.user = user
        self.pswd = password
        self.keepalive = keepalive
        self.sock = None
        self.cb = None

    def _send_str(self, s):
        self.sock.write(struct.pack("!H", len(s)))
        self.sock.write(s)

    def _recv_len(self):
        n = 0
        sh = 0
        while True:
            b = self.sock.read(1)
            if not b:
                raise MQTTException("Timeout or connection lost")
            b = b[0]
            n |= (b & 0x7f) << sh
            if not b & 0x80:
                break
            sh += 7
        return n

    def set_callback(self, f):
        self.cb = f

    def connect(self, clean_session=True):
        addr = socket.getaddrinfo(self.server, self.port)[0][-1]
        self.sock = socket.socket()
        self.sock.connect(addr)
        pkt = bytearray(b"\x10")  # CONNECT packet type
        var_header = bytearray(b"\x00\x04MQTT\x04")  # Protocol Name + Level
        flags = 0
        if clean_session:
            flags |= 0x02
        var_header.append(flags)
        var_header += struct.pack("!H", self.keepalive)

        payload = struct.pack("!H", len(self.client_id)) + self.client_id

        remaining_length = len(var_header) + len(payload)
        # MQTT剩余长度编码（可能大于127字节，需要多字节编码）
        def encode_len(length):
            encoded = bytearray()
            while True:
                digit = length % 128
                length = length // 128
                if length > 0:
                    digit |= 0x80
                encoded.append(digit)
                if length == 0:
                    break
            return encoded

        pkt += encode_len(remaining_length)
        pkt += var_header
        pkt += payload

        self.sock.write(pkt)

        resp = self.sock.read(4)
        if not resp or resp[0] != 0x20 or resp[1] != 0x02:
            raise MQTTException("Invalid CONNACK")
        if resp[3] != 0:
            raise MQTTException("Connection refused, code: %d" % resp[3])

    def disconnect(self):
        self.sock.write(b"\xe0\x00")  # DISCONNECT fixed header
        self.sock.close()
        self.sock = None

    def publish(self, topic, msg, retain=False, qos=0):
        pkt = bytearray()
        # MQTT publish header
        header = 0x30
        if retain:
            header |= 0x01
        if qos == 1:
            header |= 0x02
        elif qos == 2:
            header |= 0x04
        pkt.append(header)

        # 计算剩余长度
        remaining_length = 2 + len(topic) + len(msg)
        if qos > 0:
            remaining_length += 2  # 包含packet id
        # 先编码剩余长度
        def encode_len(length):
            encoded = bytearray()
            while True:
                digit = length % 128
                length = length // 128
                if length > 0:
                    digit |= 0x80
                encoded.append(digit)
                if length == 0:
                    break
            return encoded

        pkt += encode_len(remaining_length)

        # 主题
        pkt += struct.pack("!H", len(topic)) + topic

        # qos>0时需要packet id
        if qos > 0:
            pkt += struct.pack("!H", 1)  # packet id固定为1，可改

        pkt += msg

        self.sock.write(pkt)

        # 简化版不处理PUBACK等响应

    def subscribe(self, topic, qos=0):
        pkt = bytearray()
        pkt.append(0x82)  # SUBSCRIBE fixed header with qos=1
        # 2字节packet id + 2字节topic length + topic + 1字节qos
        remaining_length = 2 + 2 + len(topic) + 1

        def encode_len(length):
            encoded = bytearray()
            while True:
                digit = length % 128
                length = length // 128
                if length > 0:
                    digit |= 0x80
                encoded.append(digit)
                if length == 0:
                    break
            return encoded

        pkt += encode_len(remaining_length)
        pkt += struct.pack("!H", 1)  # packet id
        pkt += struct.pack("!H", len(topic)) + topic
        pkt.append(qos)

        self.sock.write(pkt)

        resp = self.sock.read(5)
        if not resp or resp[0] != 0x90:
            raise MQTTException("SUBACK error")

    def check_msg(self):
        if not self.sock:
            return
        self.sock.settimeout(0.1)
        try:
            resp = self.sock.read(1)
            if not resp:
                return
            if resp[0] == 0x30:  # PUBLISH
                length = self._recv_len()
                topic_len_bytes = self.sock.read(2)
                if not topic_len_bytes or len(topic_len_bytes) < 2:
                    return
                topic_len = struct.unpack("!H", topic_len_bytes)[0]
                topic = self.sock.read(topic_len)
                msg_len = length - topic_len - 2
                msg = self.sock.read(msg_len)
                if self.cb:
                    self.cb(topic, msg)
                msg_dict = ujson.loads(msg.decode())
                return msg_dict
        except Exception as e:
            return False


def connect_wifi(SSID, PASSWORD):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(SSID, PASSWORD)
    connect_times = 0
    while not wlan.isconnected():
        print('Trying to connect to "{:s}"...'.format(SSID))
        time.sleep_ms(1000)
        connect_times += 1
        if connet_times > 5:
            print(f"Connect to {SSID} failed.")
            break;
    print("WiFi Connected ", wlan.ifconfig())
    return wlan.ifconfig()






sensor.reset()                         # Reset and initialize the sensor.
#sensor.set_pixformat(sensor.GRAYSCALE)
sensor.set_pixformat(sensor.RGB565)    # Set pixel format to RGB565 (or GRAYSCALE)
sensor.set_framesize(sensor.QVGA) # Set frame size to QVGA (320x240)
sensor.set_windowing((240,240))       # Set 240x240 window.
sensor.skip_frames(time=2000)          # Let the camera adjust.

net = None
labels = None

try:
    # load the model, alloc the model file on the heap if we have at least 64K free after loading
    net = tf.load("trained.tflite", load_to_fb=uos.stat('trained.tflite')[6] > (gc.mem_free() - (64*1024)))
except Exception as e:
    print(e)
    raise Exception('Failed to load "trained.tflite", did you copy the .tflite and labels.txt file onto the mass-storage device? (' + str(e) + ')')

try:
    labels = [line.rstrip('\n') for line in open("labels.txt")]
except Exception as e:
    raise Exception('Failed to load "labels.txt", did you copy the .tflite and labels.txt file onto the mass-storage device? (' + str(e) + ')')


clock = time.clock()
while(True):
    clock.tick()
    img = sensor.snapshot()
    #Compare_msg = client.check_msg()

    results = net.classify(img,
                           roi=(0, 0, img.width(), img.height()),
                           scale_mul=0,
                           x_overlap=0,
                           y_overlap=0)



    obj = results[0]
    scores = obj[4]
    predictions_list = list(zip(labels, scores))
    predictions_max = 0
    predictions_num = None

    for i in range(len(predictions_list)):
        label, score = predictions_list[i]
        if score > predictions_max:
            predictions_max = score
            predictions_num = label
        print("%s = %f" % (label, score))



    print(clock.fps(), "fps")
