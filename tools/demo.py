# -*- coding: utf-8 -*-
"""KimiQ 现场演示脚本：HTTP 直驱全状态走一遍（绕过 hooks，随时可跑）。"""
import time
import urllib.parse
import urllib.request


def go(to, text="", wait=2.4):
    url = "http://127.0.0.1:28765/state?to=" + to
    if text:
        url += "&text=" + urllib.parse.quote(text)
    urllib.request.urlopen(url, timeout=3).read()
    time.sleep(wait)


def baby(op, wait=1.2):
    urllib.request.urlopen("http://127.0.0.1:28765/baby?op=" + op, timeout=3).read()
    time.sleep(wait)


print("① 一轮完整工作流（接收→思考→翻资料→联网→干活→写回复→完成撒花）")
go("31", "", 2.2)
go("30", "任务 1/3 · 给小土表演", 2.6)
go("40", "读 README.md", 2.6)
go("36", "搜 emotion-ball", 2.6)
go("32", "跑 PyInstaller", 2.6)
go("39", "", 2.2)
go("33", "", 3.2)      # 撒花+音效

print("② 连续翻车红温（出错→慌张→红温）")
go("34", "跑 npm install", 2.2)
go("34", "跑 npm install", 2.2)
go("34", "跑 npm install", 3.0)   # 第三次 → 21 红温
go("33", "", 3.0)      # 完成消气

print("③ 子代理小球（坐两只 → 收走）")
baby("add")
baby("add", 3.0)
baby("remove")
baby("remove", 1.5)

go("02", "", 1.0)
print("演示结束，回待机")
