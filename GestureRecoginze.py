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
import random
import uctypes
#import guider
import network

#以下是手动补齐的软件包
import usocket as socket
import ustruct as struct
import utime



class Servo360:
    def __init__(self, port_base, pin):
        """
        port_base: PORT寄存器基地址 (例: 0x40400000)
        pin: 引脚编号 (0~15)
        """
        # 定义寄存器结构
        port_regs = {
            "PDR":  0x000 | uctypes.UINT16,  # 方向寄存器
            "PODR": 0x002 | uctypes.UINT16,  # 输出寄存器
        }
        # 建立寄存器映射
        self.port = uctypes.struct(port_base, port_regs)
        self.pin = pin

        # 设置为输出模式
        self.port.PDR |= (1 << self.pin)

    def _pulse(self, pulse_width_us):
        """发送一次舵机PWM脉冲"""
        self.port.PODR |= (1 << self.pin)  # 高电平
        time.sleep_us(pulse_width_us)
        self.port.PODR &= ~(1 << self.pin)  # 低电平
        time.sleep_us(20000 - pulse_width_us)  # 补足周期

    def run(self, direction, duration_s=1):
        """
        direction: -1=反转, 0=停止, 1=正转
        duration_s: 持续时间（秒）
        """
        if direction == -1:
            pulse = 1000  # 1ms
        elif direction == 0:
            pulse = 1500  # 1.5ms
        elif direction == 1:
            pulse = 2000  # 2ms
        else:
            raise ValueError("方向只能是 -1, 0, 1")

        cycles = int((duration_s * 1000) / 20)  # 每周期20ms
        for _ in range(cycles):
            self._pulse(pulse)



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
        if connect_times > 5:
            print(f"Connect to {SSID} failed.")
            return False
    print("WiFi Connected ", wlan.ifconfig())
    return wlan.ifconfig()



class GestureRecoginze:
    RPS = {
        0: "rock",
        1: "paper",
        2: "scissors"
    }
    def __init__(self):
        self.net = None
        self.lables = None
        self.WIFIConnectStatus = GestureRecoginze.connect_wifi("IQOO Neo 6", "x31415926y")
        self.MqttxClient = None
        self.MqttxConnectStatus = False
        if self.WIFIConnectStatus:
            self.MqttxClient = MQTTClient("openmv", "broker.hivemq.com", port=1883)
            self.MqttxConnectStatus = True
            try:
                self.MqttxClient.connect()
                self.MqttxClient.subscribe("openmv/test")
            except:
                print("connect to MQTTx failed.")
                self.MqttxConnectStatus = False
            self.MqttxClient.set_callback(lambda topic, msg: print(topic, msg))

        self.servo = Servo360(0x40400000, 8)


        """ 初始化摄像头 """
        sensor.reset()                         # Reset and initialize the sensor.
        sensor.set_pixformat(sensor.RGB565)    # Set pixel format to RGB565 (or GRAYSCALE)
        sensor.set_framesize(sensor.QVGA)      # Set frame size to QVGA (320x240)
        sensor.set_windowing((240,240))        # Set 240x240 window.
        sensor.skip_frames(time=2000)          # Let the camera adjust.

        """  加载模型  """
        try:
            # load the model, alloc the model file on the heap if we have at least 64K free after loading
            self.net = tf.load("trained.tflite", load_to_fb=uos.stat('trained.tflite')[6] > (gc.mem_free() - (64*1024)))
        except Exception as e:
            print(e)
            raise Exception('Failed to load "trained.tflite", did you copy the .tflite and labels.txt file onto the mass-storage device? (' + str(e) + ')')

        try:
            self.labels = [line.rstrip('\n') for line in open("labels.txt")]
        except Exception as e:
            raise Exception('Failed to load "labels.txt", did you copy the .tflite and labels.txt file onto the mass-storage device? (' + str(e) + ')')

    def MainAction(self, comparetimes):
        clock = time.clock()
        compare_times = 0
        start_time = None
        CompareResultShow = None
        compare_result = None

        while(compare_times<comparetimes):
            clock.tick()
            img = sensor.snapshot()

            results = self.net.classify(img,
                                   roi=(0, 0, img.width(), img.height()),
                                   scale_mul=0,
                                   x_overlap=0,
                                   y_overlap=0)

            obj = results[0]
            scores = obj[4]
            predictions_list = list(zip(self.labels, scores))
            predictions_max = 0
            predictions_num = None

            for i in range(len(predictions_list)):
                label, score = predictions_list[i]
                if score > predictions_max:
                    predictions_max = score
                    predictions_num = label
                print("%s = %f" % (label, score))

            img.draw_string(0, 0, "Predictions: %s" % predictions_num, mono_space=False, scale=2)


            if start_time is None:
                start_time = time.ticks_ms()
            if time.ticks_diff(time.ticks_ms(), start_time) > 5000:
                if predictions_max > 0.90:
                    machines_gesture = random.randint(0, 2)
                    if machines_gesture == 0: # rock
                        if predictions_num == "rock":
                            compare_result = "draw"
                        elif predictions_num == "paper":
                            compare_result = "win"
                        else:
                            compare_result = "lose"
                    elif machines_gesture == 1: # paper
                        if predictions_num == "rock":
                            compare_result = "lose"
                        elif predictions_num == "paper":
                            compare_result = "draw"
                        else:
                            compare_result = "win"
                    else: # scissors
                        if predictions_num == "rock":
                            compare_result = "win"
                        elif predictions_num == "paper":
                            compare_result = "lose"
                        else:
                            compare_result = "draw"

                    if self.WIFIConnectStatus:
                        self.MqttxClient.publish("openmv/test", ujson.dumps({"compare_times": compare_times,
                                                                           "machine_label":self.RPS[machines_gesture],
                                                                           "label": predictions_num,
                                                                           "score": predictions_max,
                                                                           "compare_result": compare_result}))
                    if compare_result == "win":
                        self.servo.run(1, 1) #正转一秒
                    else:
                        self.servo.run(-1, 1)
                    print(compare_times)
                    start_time = time.ticks_ms()
                    CompareResultShow = time.ticks_ms()
                    compare_times += 1
            else:

                print("get_ready......")

            if CompareResultShow is not None and time.ticks_diff(time.ticks_ms(), CompareResultShow) < 2500:
                img.draw_string(0, 20, "machines_gesture: %s" % self.RPS[machines_gesture], mono_space=False, scale=2)
                img.draw_string(0, 40, "compare result: %s" % compare_result, mono_space=False, scale=2)
            else:
                CompareResultShow = None
                img.draw_string(0, 20, "get_ready......", mono_space=False, scale=2)

            print(clock.fps(), "fps")




    @staticmethod
    def connect_wifi(SSID, PASSWORD):
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)
        try:
            wlan.connect(SSID, PASSWORD)
        except:
            print(f"Connect to {SSID} failed.")
            return False
        connect_times = 0
        while not wlan.isconnected():
            print('Trying to connect to "{:s}"...'.format(SSID))
            time.sleep_ms(1000)
            connect_times += 1
            if connect_times > 5:
                print(f"Connect to {SSID} failed.")
                return False
        print("WiFi Connected ", wlan.ifconfig())
        return wlan.ifconfig()



gesturerecoginze = GestureRecoginze()

gesturerecoginze.MainAction(5)




