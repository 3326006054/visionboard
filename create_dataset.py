import sensor, image, time, os

sensor.reset()
sensor.set_pixformat(sensor.RGB565)
sensor.set_framesize(sensor.QVGA)  # 320x240
sensor.set_windowing((240,240))
sensor.skip_frames(time=2000)


img_counter = 0

while True:
    img = sensor.snapshot()
    filename = "/dataset/scissors/scissors_img_%03d.jpg" % img_counter
    img.save(filename)
    print("Saved:", filename)
    img_counter += 1
    time.sleep_ms(500)
    if img_counter >= 550:  # 停止条件
        break
