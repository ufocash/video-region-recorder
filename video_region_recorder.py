# -*- coding: utf-8 -*-
"""
video_region_recorder.py — 浮動式影片區域錄影工具 (Windows)

功能:
  - 執行後出現一個小型半透明浮動視窗 (永遠置頂、可拖曳)
  - 按「● 錄影」: 自動偵測畫面中「最大的正在播放影片的區域」(以畫面變動偵測, 不需手動框選)
  - 再按一次「■ 停止」: 結束錄影, 自動將影片(含系統聲音)存到「下載」資料夾
  - 檔名格式: YYYYMMDD_HHMMSS_亂數.mp4
  - 偵測與錄影過程不會有任何不透明視窗蓋住影片; 若浮動視窗與錄影區域重疊會自動移開

安裝 (一次即可):
  pip install mss numpy opencv-python PyAudioWPatch imageio-ffmpeg

執行:
  python video_region_recorder.py
"""

import ctypes
import datetime
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

import tkinter as tk

import numpy as np
import cv2
import mss

try:
    import pyaudiowpatch as pyaudio
    HAS_AUDIO_LIB = True
except ImportError:
    HAS_AUDIO_LIB = False

FPS = 24                 # 錄影幀率
DETECT_FRAMES = 6        # 偵測時取樣張數
DETECT_INTERVAL = 0.15   # 取樣間隔(秒)
DETECT_SCALE = 0.25      # 偵測用縮圖比例(加速)
DIFF_THRESHOLD = 12      # 像素變動門檻
MIN_AREA_RATIO = 0.01    # 偵測區域至少佔全螢幕 1%, 否則改錄全螢幕


# ---------------------------------------------------------------- 工具函式

def set_dpi_aware():
    """避免高 DPI 縮放造成座標錯位"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def get_downloads_dir() -> Path:
    """取得 Windows 真正的「下載」資料夾 (支援使用者自訂位置)"""
    try:
        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", ctypes.c_ulong),
                ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        folderid_downloads = GUID(
            0x374DE290, 0x123F, 0x4565,
            (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B),
        )
        path_ptr = ctypes.c_wchar_p()
        result = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(folderid_downloads), 0, None, ctypes.byref(path_ptr)
        )
        if result == 0 and path_ptr.value:
            p = Path(path_ptr.value)
            ctypes.windll.ole32.CoTaskMemFree(path_ptr)
            return p
    except Exception:
        pass
    return Path.home() / "Downloads"


def make_output_name() -> str:
    now = datetime.datetime.now()
    return f"{now:%Y%m%d_%H%M%S}_{random.randint(1000, 9999)}.mp4"


# ---------------------------------------------------------------- 區域偵測

def detect_video_region(exclude_rect=None):
    """
    偵測畫面中最大的動態(影片播放)區域。
    exclude_rect: (left, top, right, bottom) 浮動視窗位置, 偵測時忽略。
    回傳 mss 用的 dict {left, top, width, height}; 偵測不到則回傳全螢幕。
    """
    with mss.mss() as sct:
        mon = sct.monitors[0]  # 整個虛擬桌面(含多螢幕)
        frames = []
        for _ in range(DETECT_FRAMES):
            img = np.asarray(sct.grab(mon))[:, :, :3]
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            small = cv2.resize(gray, None, fx=DETECT_SCALE, fy=DETECT_SCALE,
                               interpolation=cv2.INTER_AREA)
            frames.append(small)
            time.sleep(DETECT_INTERVAL)

    mask = np.zeros_like(frames[0], dtype=np.uint8)
    for a, b in zip(frames, frames[1:]):
        diff = cv2.absdiff(a, b)
        mask |= (diff > DIFF_THRESHOLD).astype(np.uint8) * 255

    # 忽略浮動視窗本身的區域
    if exclude_rect:
        l, t, r, b = exclude_rect
        sl = int((l - mon["left"]) * DETECT_SCALE) - 2
        st = int((t - mon["top"]) * DETECT_SCALE) - 2
        sr = int((r - mon["left"]) * DETECT_SCALE) + 2
        sb = int((b - mon["top"]) * DETECT_SCALE) + 2
        h, w = mask.shape
        mask[max(0, st):min(h, sb), max(0, sl):min(w, sr)] = 0

    # 合併鄰近動態區塊
    kernel = np.ones((15, 15), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8))

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    full = {"left": mon["left"], "top": mon["top"],
            "width": mon["width"], "height": mon["height"]}
    if not contours:
        return full, False

    largest = max(contours, key=cv2.contourArea)
    x, y, w, h = cv2.boundingRect(largest)
    if (w * h) < (mask.shape[0] * mask.shape[1] * MIN_AREA_RATIO):
        return full, False

    # 換算回實際座標
    inv = 1.0 / DETECT_SCALE
    left = mon["left"] + int(x * inv)
    top = mon["top"] + int(y * inv)
    width = int(w * inv)
    height = int(h * inv)

    # 邊界修正 + 寬高取偶數(編碼需求)
    left = max(mon["left"], left)
    top = max(mon["top"], top)
    width = min(width, mon["left"] + mon["width"] - left)
    height = min(height, mon["top"] + mon["height"] - top)
    width -= width % 2
    height -= height % 2
    if width < 16 or height < 16:
        return full, False

    return {"left": left, "top": top, "width": width, "height": height}, True


# ---------------------------------------------------------------- 錄影/錄音

def video_capture_loop(region, out_path, stop_evt, started_evt):
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(out_path, fourcc, FPS,
                             (region["width"], region["height"]))
    period = 1.0 / FPS
    with mss.mss() as sct:
        started_evt.set()
        next_t = time.perf_counter()
        while not stop_evt.is_set():
            img = np.asarray(sct.grab(region))[:, :, :3]
            writer.write(np.ascontiguousarray(img))
            next_t += period
            delay = next_t - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                next_t = time.perf_counter()  # 趕不上就重新對時
    writer.release()


def audio_capture_loop(out_path, stop_evt, result):
    """以 WASAPI loopback 錄製系統聲音; 失敗時 result['ok']=False, 不影響錄影"""
    if not HAS_AUDIO_LIB:
        result["ok"] = False
        return
    p = pyaudio.PyAudio()
    try:
        wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        speakers = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
        if not speakers.get("isLoopbackDevice", False):
            for lb in p.get_loopback_device_info_generator():
                if speakers["name"] in lb["name"]:
                    speakers = lb
                    break
            else:
                result["ok"] = False
                return
        rate = int(speakers["defaultSampleRate"])
        channels = max(1, int(speakers["maxInputChannels"]))
        chunk = 1024
        stream = p.open(format=pyaudio.paInt16, channels=channels, rate=rate,
                        input=True, input_device_index=speakers["index"],
                        frames_per_buffer=chunk)
        wf = wave.open(out_path, "wb")
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        while not stop_evt.is_set():
            wf.writeframes(stream.read(chunk, exception_on_overflow=False))
        stream.stop_stream()
        stream.close()
        wf.close()
        result["ok"] = True
    except Exception:
        result["ok"] = False
    finally:
        p.terminate()


def mux_av(video_path, audio_path, out_path):
    """用 ffmpeg 合併影像與聲音"""
    import imageio_ffmpeg
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ffmpeg, "-y", "-i", video_path, "-i", audio_path,
           "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", out_path]
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    subprocess.run(cmd, check=True, capture_output=True, creationflags=flags)


# ---------------------------------------------------------------- 浮動介面

class RecorderApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)          # 無邊框
        self.root.attributes("-topmost", True)    # 永遠置頂
        self.root.attributes("-alpha", 0.88)      # 半透明
        self.root.configure(bg="#1e1e1e")

        self.btn = tk.Button(self.root, text="● 錄影", font=("Microsoft JhengHei", 11, "bold"),
                             fg="#ff4444", bg="#2d2d2d", activebackground="#3d3d3d",
                             activeforeground="#ff4444", bd=0, padx=14, pady=4,
                             command=self.toggle)
        self.btn.pack(fill="x")

        self.status = tk.Label(self.root, text="待命", font=("Microsoft JhengHei", 8),
                               fg="#aaaaaa", bg="#1e1e1e")
        self.status.pack(fill="x")

        close = tk.Label(self.root, text="✕", font=("Arial", 8), fg="#888888", bg="#1e1e1e",
                         cursor="hand2")
        close.place(relx=1.0, x=-14, y=0)
        close.bind("<Button-1>", lambda e: self.quit())

        # 拖曳
        for w in (self.root, self.status):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)

        # 放在主螢幕右上角
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        self.root.geometry(f"+{sw - self.root.winfo_width() - 40}+20")

        self.recording = False
        self.busy = False
        self.stop_evt = None
        self.threads = []
        self.tmp_video = None
        self.tmp_audio = None
        self.audio_result = {}
        self.t0 = 0.0
        self.region_ok = False

    # ---- 拖曳 ----
    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _widget_rect(self):
        x, y = self.root.winfo_x(), self.root.winfo_y()
        return (x, y, x + self.root.winfo_width(), y + self.root.winfo_height())

    def _move_outside(self, region):
        """若浮動視窗在錄影區域內, 自動移到區域外"""
        x, y, r, b = self._widget_rect()
        rl, rt = region["left"], region["top"]
        rr, rb = rl + region["width"], rt + region["height"]
        if r > rl and x < rr and b > rt and y < rb:
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            w, h = self.root.winfo_width(), self.root.winfo_height()
            candidates = [(rl - w - 8, y), (rr + 8, y), (x, rt - h - 8), (x, rb + 8),
                          (8, 8), (sw - w - 8, sh - h - 48)]
            for cx, cy in candidates:
                if 0 <= cx <= sw - w and 0 <= cy <= sh - h and not (
                        cx + w > rl and cx < rr and cy + h > rt and cy < rb):
                    self.root.geometry(f"+{int(cx)}+{int(cy)}")
                    return

    # ---- 主流程 ----
    def toggle(self):
        if self.busy:
            return
        if not self.recording:
            self.busy = True
            self.btn.config(state="disabled")
            self.status.config(text="偵測影片區域中…")
            threading.Thread(target=self._start_recording, daemon=True).start()
        else:
            self.busy = True
            self.btn.config(state="disabled")
            self.status.config(text="儲存中…")
            threading.Thread(target=self._stop_recording, daemon=True).start()

    def _start_recording(self):
        region, found = detect_video_region(exclude_rect=self._widget_rect())
        self.region_ok = found

        tmp = tempfile.gettempdir()
        stamp = f"{int(time.time())}_{random.randint(100, 999)}"
        self.tmp_video = os.path.join(tmp, f"rec_{stamp}.mp4")
        self.tmp_audio = os.path.join(tmp, f"rec_{stamp}.wav")

        self.stop_evt = threading.Event()
        self.audio_result = {}
        started = threading.Event()
        tv = threading.Thread(target=video_capture_loop,
                              args=(region, self.tmp_video, self.stop_evt, started),
                              daemon=True)
        ta = threading.Thread(target=audio_capture_loop,
                              args=(self.tmp_audio, self.stop_evt, self.audio_result),
                              daemon=True)
        tv.start()
        started.wait(timeout=5)  # 影像開始擷取後才啟動聲音, 減少不同步
        ta.start()
        self.threads = [tv, ta]
        self.t0 = time.time()
        self.region = region

        def ui():
            self._move_outside(region)
            txt = (f"{region['width']}×{region['height']}" if found
                   else f"未偵測到播放中影片, 錄全螢幕 {region['width']}×{region['height']}")
            self.status.config(text=txt)
            self.btn.config(text="■ 停止", fg="#ffffff", state="normal")
            self.recording = True
            self.busy = False
            self._tick()
        self.root.after(0, ui)

    def _tick(self):
        if self.recording:
            sec = int(time.time() - self.t0)
            base = f"{self.region['width']}×{self.region['height']}"
            self.status.config(text=f"REC {sec // 60:02d}:{sec % 60:02d}  {base}")
            self.root.after(500, self._tick)

    def _stop_recording(self):
        self.stop_evt.set()
        for t in self.threads:
            t.join(timeout=10)

        out_path = str(get_downloads_dir() / make_output_name())
        try:
            if self.audio_result.get("ok") and os.path.exists(self.tmp_audio) \
                    and os.path.getsize(self.tmp_audio) > 1024:
                mux_av(self.tmp_video, self.tmp_audio, out_path)
            else:
                shutil.move(self.tmp_video, out_path)
            msg = f"已存檔: {os.path.basename(out_path)}"
            if not self.audio_result.get("ok"):
                msg += " (無聲音)"
        except Exception as e:
            # 合併失敗時至少保留影像檔
            try:
                if os.path.exists(self.tmp_video):
                    shutil.move(self.tmp_video, out_path)
                    msg = f"已存檔(無聲音): {os.path.basename(out_path)}"
                else:
                    msg = f"失敗: {e}"
            except Exception as e2:
                msg = f"失敗: {e2}"
        finally:
            for f in (self.tmp_video, self.tmp_audio):
                try:
                    if f and os.path.exists(f):
                        os.remove(f)
                except OSError:
                    pass

        def ui():
            self.recording = False
            self.busy = False
            self.btn.config(text="● 錄影", fg="#ff4444", state="normal")
            self.status.config(text=msg)
        self.root.after(0, ui)

    def quit(self):
        if self.recording and self.stop_evt:
            self.stop_evt.set()
            time.sleep(0.5)
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if sys.platform != "win32":
        print("此程式僅支援 Windows")
        sys.exit(1)
    set_dpi_aware()
    RecorderApp().run()
