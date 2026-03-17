"""Convert bull-logo.png to bull-logo.ico for Windows executable."""
import os
from PIL import Image

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
png_path = os.path.join(BASE_DIR, "static", "bull-logo.png")
ico_path = os.path.join(BASE_DIR, "static", "bull-logo.ico")

if os.path.exists(png_path):
    img = Image.open(png_path)
    # Create multiple sizes for Windows icon
    img.save(ico_path, format="ICO",
             sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"Icon created: {ico_path}")
else:
    print(f"ERROR: {png_path} not found")
    exit(1)
