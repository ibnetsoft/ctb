from PIL import Image
import os

img_path = r'C:\Users\Pc\.gemini\antigravity\brain\03130af0-92e9-4f60-82fc-f7a6c0133c50\mmbot_icon_1776079856375.png'
ico_path = r'd:\다운로드Download\mmbot\icon.ico'

img = Image.open(img_path)
# Resize or just save as ico. ICO can contain multiple sizes.
img.save(ico_path, format='ICO', sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(f"Icon saved to {ico_path}")
