# 浮動式影片區域錄影工具 (video_region_recorder.py)

Windows 桌面錄影工具 — 自動偵測畫面中最大的播放中影片區域，按一下就能錄影。

## 特色

- 🎯 **自動偵測**：按「● 錄影」自動找出畫面中最大的影片播放區塊（透過畫面變動偵測）
- 🖥️ **無偵測到影片 → 錄全螢幕**
- 🔇 **系統聲音同步錄製**（WASAPI loopback，需 pyaudiowpatch）
- 🪟 **半透明浮動視窗**，永遠置頂、可拖曳、自動避開錄影區域
- 📁 **自動存到「下載」資料夾**，檔名格式：`YYYYMMDD_HHMMSS_亂數.mp4`

## 安裝

```bash
pip install mss numpy opencv-python PyAudioWPatch imageio-ffmpeg
```

> PyAudioWPatch 負責錄系統聲音，若裝失敗仍可錄影（無聲音）。

## 用法

```bash
python video_region_recorder.py
```

1. 執行後出現半透明浮動視窗
2. 按紅色的 **● 錄影** → 工具會自動偵測影片區域（約 1 秒）
3. 偵測到 → 只錄該區域；沒偵到 → 錄全螢幕
4. 再按 **■ 停止** → 自動存檔到「下載」資料夾

## 系統需求

- Windows（使用 DPI awareness、WASAPI loopback、mss 螢幕擷取）
- Python 3.6+
