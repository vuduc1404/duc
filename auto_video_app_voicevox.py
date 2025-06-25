# --- File: auto_video_app.py ---

import os
import tkinter as tk
from tkinter import filedialog, colorchooser, messagebox, ttk
import asyncio
import tempfile
import threading
import platform
import json
import math
import subprocess
import sys
# Import for PyInstaller's temporary path
if getattr(sys, 'frozen', False): # Check if running as a bundled executable
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if sys.platform == "win32":
    CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW
else:
    CREATE_NO_WINDOW = 0 # Unix-like systems don't use this flag

from multiprocessing import Queue
# Import render_shard và normalize_path_for_ffmpeg từ video_worker.py
from video_worker import render_shard, normalize_path_for_ffmpeg 
import requests # Thêm thư viện requests để gọi API Voicevox

output_temp_dir = tempfile.gettempdir()

def get_ffmpeg_path():
    # Lấy đường dẫn ffmpeg.exe trong thư mục cùng cấp với script hoặc trong thư mục _MEIPASS
    ffmpeg_exe_path = os.path.join(BASE_DIR, "ffmpeg", "ffmpeg.exe")
    if not os.path.exists(ffmpeg_exe_path):
        messagebox.showerror("Lỗi FFmpeg", 
                             f"Không tìm thấy FFmpeg.exe tại đường dẫn: {ffmpeg_exe_path}\n"
                             "Vui lòng đảm bảo bạn đã tải FFmpeg và đặt ffmpeg.exe vào thư mục 'ffmpeg' cùng cấp với ứng dụng.")
        return None
    return ffmpeg_exe_path

def list_fonts():
    # Liệt kê các font .ttf và .ttc trên Windows
    if platform.system() == "Windows":
        font_dir = os.path.join(os.environ['WINDIR'], 'Fonts')
        # Lọc các file font thực sự
        return [f for f in os.listdir(font_dir) if f.lower().endswith(('.ttf', '.ttc')) and os.path.isfile(os.path.join(font_dir, f))]
    return [] # Trả về danh sách rỗng nếu không phải Windows

def split_sentences(text):
    import re
    # Tách câu cải tiến cho tiếng Nhật và tiếng Việt
    return [s.strip() for s in re.split(r'[。\u3002.!?\n]', text) if s.strip()]

def detect_available_encoders():
    # Phát hiện các encoder video có sẵn và ưu tiên GPU (nếu hoạt động) hơn CPU
    ffmpeg = get_ffmpeg_path()
    if ffmpeg is None: # Kiểm tra nếu FFmpeg không tồn tại
        return ["libx264"] # Trả về mặc định libx264 và lỗi đã được thông báo

    # Cấu hình StartupInfo để ẩn cửa sổ console
    si = None
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE # Ẩn cửa sổ

    try:
        # Lấy danh sách các encoder từ FFmpeg
        result = subprocess.run([ffmpeg, '-encoders'], capture_output=True, text=True, startupinfo=si, check=True)
        
        encoders_priority = ["h264_nvenc", "h264_amf", "h264_qsv", "libx264"] 
        available = []
        
        for encoder in encoders_priority:
            if encoder in result.stdout:
                if encoder in ["h264_nvenc", "h264_amf", "h264_qsv"]:
                    test_cmd = [ffmpeg, '-f', 'lavfi', '-i', 'nullsrc=s=1280x720:d=1', '-c:v', encoder, '-f', 'null', '-']
                    try:
                        subprocess.run(test_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=5, check=True, startupinfo=si)
                        available.append(encoder)
                    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                        error_output = e.stderr.decode() if e.stderr else "Unknown error (stderr was None)"
                        print(f"Encoder '{encoder}' phát hiện nhưng không hoạt động đúng: {error_output}")
                else:
                    available.append(encoder)
        
        if "libx264" not in available and "libx264" in result.stdout:
            available.append("libx264")

        if not available:
            messagebox.showwarning("Cảnh báo", "Không tìm thấy encoder video nào tương thích. Video có thể không được tạo.")
            return ["libx264"]

        return available
    except subprocess.CalledProcessError as e:
        error_message = e.stderr if e.stderr else "Unknown FFmpeg error during encoder detection."
        print(f"Lỗi khi phát hiện encoder (CalledProcessError): {error_message}")
        messagebox.showerror("Lỗi", f"Không thể phát hiện các encoder video: {error_message}\nĐảm bảo FFmpeg đã được cài đặt đúng.")
        return ["libx64"] # Fallback to libx64 in case of error
    except Exception as e:
        print(f"Lỗi khi phát hiện encoder: {e}")
        messagebox.showerror("Lỗi", f"Không thể phát hiện các encoder video: {e}\nĐảm bảo FFmpeg đã được cài đặt đúng.")
        return ["libx264"] # Fallback to libx264 in case of general error

class AutoVideoCreator:
    def __init__(self, root):
        self.root = root
        self.root.title("Auto Video Creator (Voicevox Nhật Bản) - Code by Vũ Đức")
        self.root.geometry("800x980")
        self.root.resizable(False, False)
        self.root.configure(bg="#f0f2f5")

        self.text_path = None
        self.image_paths = []
        self.subtitle_color = "#FFFF00"
        self.stroke_color = "#000000"
        self.bg_color = "#FFFFFF"
        self.output_dir = os.getcwd()
        
        self.available_encoders = detect_available_encoders()
        self.encoder = self.available_encoders[0]

        self.voicevox_speakers = []
        self.load_voicevox_speakers()

        self.init_ui()

    def init_ui(self):
        default_font = ("Segoe UI", 10)
        title_font = ("Segoe UI", 14, "bold")
        button_font = ("Segoe UI", 10, "bold")

        style = ttk.Style()
        style.theme_use('clam')
        style.configure("TFrame", background="#f0f2f5")
        style.configure("TLabel", background="#f0f2f5", font=default_font)
        style.configure("TButton", font=button_font, padding=8)
        style.map("TButton",
                  foreground=[('active', 'white'), ('!disabled', 'black')],
                  background=[('active', '#4CAF50'), ('!disabled', '#607d8b')])
        style.configure("TCombobox", font=default_font, padding=3)
        style.configure("TEntry", font=default_font, padding=3)
        style.configure("Horizontal.TScale", background="#f0f2f5", troughcolor="#e0e0e0")

        main_frame = ttk.Frame(self.root, padding="20 20 20 20", relief="raised")
        main_frame.pack(padx=20, pady=20, fill="both", expand=True)

        ttk.Label(main_frame, text="Tạo Video Tự Động (Voicevox)", font=title_font, foreground="#333").grid(row=0, column=0, columnspan=4, pady=(0, 20))

        input_frame = ttk.LabelFrame(main_frame, text="Đầu vào", padding="15 15 15 15")
        input_frame.grid(row=1, column=0, columnspan=4, sticky="ew", pady=10)

        ttk.Button(input_frame, text="📄 Chọn file TXT", command=self.select_text).grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.text_label = ttk.Label(input_frame, text="Chưa chọn file văn bản", font=default_font, foreground="gray")
        self.text_label.grid(row=0, column=1, columnspan=3, sticky="w", padx=5, pady=5)

        ttk.Button(input_frame, text="🖼 Chọn ảnh", command=self.select_images).grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.img_label = ttk.Label(input_frame, text="Chưa chọn ảnh", font=default_font, foreground="gray")
        self.img_label.grid(row=1, column=1, columnspan=3, sticky="w", padx=5, pady=5)

        options_frame = ttk.LabelFrame(main_frame, text="Tùy chọn Voicevox & Video", padding="15 15 15 15")
        options_frame.grid(row=2, column=0, columnspan=4, sticky="ew", pady=10)

        ttk.Label(options_frame, text="Giọng (Japanese):").grid(row=0, column=0, sticky="e", padx=5, pady=5)
        self.voice_option = ttk.Combobox(options_frame, values=[s["name"] for s in self.voicevox_speakers], state="readonly", width=30)
        if self.voicevox_speakers:
            self.voice_option.set(self.voicevox_speakers[0]["name"])
        else:
            self.voice_option.set("Không có speaker")
            self.voice_option.config(state="disabled")
        self.voice_option.grid(row=0, column=1, sticky="ew", padx=5, pady=5)

        ttk.Label(options_frame, text="Tốc độ giọng:").grid(row=0, column=2, sticky="e", padx=5, pady=5)
        self.voice_speed = ttk.Entry(options_frame, width=8, font=default_font)
        self.voice_speed.insert(0, "1.0")
        self.voice_speed.grid(row=0, column=3, sticky="w", padx=5, pady=5)
        self.voice_speed.bind("<FocusOut>", self.validate_numeric_input)

        ttk.Label(options_frame, text="Font chữ:").grid(row=1, column=0, sticky="e", padx=5, pady=5)
        all_fonts = list_fonts()
        japanese_fonts = [f for f in all_fonts if any(p.lower() in f.lower() for p in ["meiryo", "msgothic", "msmincho", "yugoth", "notosansjp"])]
        if not japanese_fonts:
            japanese_fonts = all_fonts
            if not japanese_fonts:
                japanese_fonts = ["Arial"]
        
        self.font_option = ttk.Combobox(options_frame, values=japanese_fonts, state="readonly", width=30)
        if japanese_fonts:
            self.font_option.set(japanese_fonts[0])
        else:
            self.font_option.set("Không có font")
            self.font_option.config(state="disabled")
        self.font_option.grid(row=1, column=1, sticky="ew", padx=5, pady=5)

        ttk.Label(options_frame, text="Hiệu ứng ảnh:").grid(row=1, column=2, sticky="e", padx=5, pady=5)
        self.effect_option = ttk.Combobox(options_frame, values=["none", "zoom", "pan", "zoom+pan"], state="readonly", width=8)
        self.effect_option.set("none")
        self.effect_option.grid(row=1, column=3, sticky="w", padx=5, pady=5)

        ttk.Label(options_frame, text="Âm lượng giọng (%):").grid(row=2, column=0, sticky="e", padx=5, pady=5)
        self.volume_entry = ttk.Entry(options_frame, width=8, font=default_font)
        self.volume_entry.insert(0, "100")
        self.volume_entry.grid(row=2, column=1, sticky="ew", padx=5, pady=5)
        self.volume_entry.bind("<FocusOut>", self.validate_numeric_input)

        ttk.Label(options_frame, text="Độ trong suốt nền:").grid(row=2, column=2, sticky="e", padx=5, pady=5)
        self.bg_opacity = ttk.Scale(options_frame, from_=0, to=255, orient="horizontal", style="Horizontal.TScale", length=150)
        self.bg_opacity.set(200)
        self.bg_opacity.grid(row=2, column=3, sticky="w", padx=5, pady=5)

        ttk.Label(options_frame, text="Chọn Encoder:").grid(row=3, column=0, sticky="e", padx=5, pady=5)
        self.encoder_option = ttk.Combobox(options_frame, values=self.available_encoders, state="readonly", width=30)
        self.encoder_option.set(self.encoder)
        self.encoder_option.grid(row=3, column=1, columnspan=2, sticky="ew", padx=5, pady=5)


        output_frame = ttk.LabelFrame(main_frame, text="Tùy chọn phụ đề & Đầu ra", padding="15 15 15 15")
        output_frame.grid(row=4, column=0, columnspan=4, sticky="ew", pady=10) 

        ttk.Button(output_frame, text="🎨 Màu chữ", command=self.pick_text_color, style="Accent.TButton").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.text_color_preview = tk.Label(output_frame, text="●", fg=self.subtitle_color, font=("Arial", 16), bg="#f0f2f5")
        self.text_color_preview.grid(row=0, column=1, sticky="w", padx=0, pady=5)

        ttk.Button(output_frame, text="🎨 Viền chữ", command=self.pick_stroke_color, style="Accent.TButton").grid(row=0, column=2, sticky="w", padx=5, pady=5)
        self.stroke_color_preview = tk.Label(output_frame, text="●", fg=self.stroke_color, font=("Arial", 16), bg="#f0f2f5")
        self.stroke_color_preview.grid(row=0, column=3, sticky="w", padx=0, pady=5)
        
        ttk.Label(output_frame, text="Size viền:").grid(row=1, column=0, sticky="e", padx=5, pady=5)
        self.stroke_size = ttk.Entry(output_frame, width=8, font=default_font)
        self.stroke_size.insert(0, "2")
        self.stroke_size.grid(row=1, column=1, sticky="ew", padx=5, pady=5)
        self.stroke_size.bind("<FocusOut>", self.validate_numeric_input)

        ttk.Button(output_frame, text="🎨 Nền phụ đề", command=self.pick_bg_color, style="Accent.TButton").grid(row=1, column=2, sticky="w", padx=5, pady=5)
        self.bg_color_preview = tk.Label(output_frame, text="●", fg=self.bg_color, font=("Arial", 16), bg="#f0f2f5")
        self.bg_color_preview.grid(row=1, column=3, sticky="w", padx=0, pady=5)

        ttk.Label(output_frame, text="Tên video:").grid(row=2, column=0, sticky="e", padx=5, pady=5)
        self.output_name = ttk.Entry(output_frame, width=25, font=default_font)
        self.output_name.insert(0, "video_voicevox.mp4")
        self.output_name.grid(row=2, column=1, columnspan=3, sticky="ew", padx=5, pady=5)

        ttk.Button(output_frame, text="📂 Chọn thư mục lưu", command=self.select_output_folder, style="Accent.TButton").grid(row=3, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        self.output_dir_label = ttk.Label(output_frame, text=f"Thư mục: {os.path.basename(self.output_dir)}", font=default_font, foreground="gray")
        self.output_dir_label.grid(row=3, column=2, columnspan=2, sticky="w", padx=5, pady=5)


        style.configure("Green.TButton", background="#4CAF50", foreground="white", font=("Segoe UI", 12, "bold"))
        style.map("Green.TButton", background=[('active', '#388E3C')])
        ttk.Button(main_frame, text="🎬 TẠO VIDEO NGAY! 🎞", style="Green.TButton",
                  command=lambda: threading.Thread(target=self.safe_run).start()).grid(row=5, column=0, columnspan=4, pady=(20, 10), sticky="ew") 

        style.configure("Gray.TButton", background="#607d8b", foreground="white")
        style.map("Gray.TButton", background=[('active', '#455A64')])
        ttk.Button(main_frame, text="🧹 XÓA FILE TẠM", style="Gray.TButton",
                  command=self.clean_temp_files).grid(row=6, column=0, columnspan=4, pady=5, sticky="ew") 

        self.progress_bar = ttk.Progressbar(main_frame, orient="horizontal", length=300, mode="determinate")
        self.progress_bar.grid(row=7, column=0, columnspan=4, pady=(10, 5), sticky="ew") 

        self.status = ttk.Label(main_frame, text="Sẵn sàng...", font=("Segoe UI", 10, "italic"), foreground="#555")
        self.status.grid(row=8, column=0, columnspan=4, pady=(5, 0)) 

        self.update_fonts_by_language()

    def validate_numeric_input(self, event):
        try:
            value = float(self.voice_speed.get())
        except ValueError:
            messagebox.showerror("Lỗi nhập liệu", "Tốc độ giọng phải là một số.")
            self.voice_speed.set("1.0")

        try:
            value = int(self.volume_entry.get())
        except ValueError:
            messagebox.showerror("Lỗi nhập liệu", "Âm lượng giọng phải là một số nguyên.")
            self.volume_entry.set("100")

        try:
            value = int(self.stroke_size.get())
        except ValueError:
            messagebox.showerror("Lỗi nhập liệu", "Kích thước viền phải là một số nguyên.")
            self.stroke_size.set("2")

    def clean_temp_files(self):
        count = 0
        for f in os.listdir(output_temp_dir):
            if f.startswith(("line_", "subtitle_", "temp_", "shard_", "concat_list")):
                try:
                    os.remove(os.path.join(output_temp_dir, f))
                except Exception as e:
                    print(f"Lỗi khi xóa {f}: {e}")
        messagebox.showinfo("Hoàn tất", f"Đã xóa {count} file tạm khỏi thư mục {output_temp_dir}.")

    def load_voicevox_speakers(self):
        VOICEVOX_API_BASE = "http://127.0.0.1:50021"
        try:
            response = requests.get(f"{VOICEVOX_API_BASE}/speakers", timeout=10)
            response.raise_for_status()
            speakers_data = response.json()

            self.voicevox_speakers = []
            for speaker in speakers_data:
                for style in speaker.get("styles", []):
                    if "id" in style and "name" in style:
                        self.voicevox_speakers.append({
                            "name": f"{speaker['name']} ({style['name']})",
                            "id": style["id"]
                        })
            
            self.voicevox_speakers.sort(key=lambda x: x["name"])

        except requests.exceptions.ConnectionError:
            messagebox.showerror("Lỗi kết nối", "Không thể kết nối đến Voicevox Engine. Đảm bảo Voicevox Engine đang chạy tại http://127.0.0.1:50021.")
            self.voicevox_speakers = []
        except requests.exceptions.Timeout:
            messagebox.showerror("Lỗi kết nối", "Hết thời gian chờ khi kết nối Voicevox Engine. Đảm bảo Voicevox Engine đang chạy và phản hồi.")
            self.voicevox_speakers = []
        except requests.exceptions.RequestException as e:
            messagebox.showerror("Lỗi API", f"Lỗi khi tải speaker từ Voicevox API: {e}")
            self.voicevox_speakers = []
        except Exception as e:
            messagebox.showerror("Lỗi", f"Lỗi không xác định khi tải speaker Voicevox: {e}")
            self.voicevox_speakers = []
        
        if hasattr(self, 'voice_option'):
            self.voice_option["values"] = [s["name"] for s in self.voicevox_speakers]
            if self.voicevox_speakers:
                self.voice_option.set(self.voicevox_speakers[0]["name"])
                self.voice_option.config(state="readonly")
            else:
                self.voice_option.set("Không có speaker")
                self.voice_option.config(state="disabled")


    def update_fonts_by_language(self):
        all_fonts = list_fonts()
        japanese_fonts = [f for f in all_fonts if any(p.lower() in f.lower() for p in ["meiryo", "msgothic", "msmincho", "yugoth", "notosansjp"])]
        
        if not japanese_fonts:
            japanese_fonts = all_fonts
            if not japanese_fonts:
                japanese_fonts = ["Arial.ttf"]
                messagebox.showwarning("Cảnh báo", "Không tìm thấy font tiếng Nhật. Vui lòng cài đặt font tiếng Nhật như Meiryo, Gothic, Mincho, YuGothic.")

        self.font_option["values"] = japanese_fonts
        if japanese_fonts:
            if "meiryo.ttc" in [f.lower() for f in japanese_fonts]:
                self.font_option.set([f for f in japanese_fonts if "meiryo.ttc" in f.lower()][0])
            elif "meiryo.ttf" in [f.lower() for f in japanese_fonts]:
                self.font_option.set([f for f in japanese_fonts if "meiryo.ttf" in f.lower()][0])
            else:
                self.font_option.set(japanese_fonts[0])
        else:
            self.font_option.set("Không có font")
            self.font_option.config(state="disabled")

    def pick_text_color(self):
        c = colorchooser.askcolor(self.subtitle_color)[1]
        if c:
            self.subtitle_color = c
            self.text_color_preview.config(fg=self.subtitle_color)

    def pick_stroke_color(self):
        c = colorchooser.askcolor(self.stroke_color)[1]
        if c:
            self.stroke_color = c
            self.stroke_color_preview.config(fg=self.stroke_color)

    def pick_bg_color(self):
        c = colorchooser.askcolor(self.bg_color)[1]
        if c:
            self.bg_color = c
            self.bg_color_preview.config(fg=self.bg_color)

    def select_text(self):
        path = filedialog.askopenfilename(filetypes=[("Text Files", "*.txt")])
        if path:
            self.text_path = path
            self.text_label.config(text=os.path.basename(path), foreground="black")

    def select_images(self):
        paths = filedialog.askopenfilenames(filetypes=[("Images", "*.jpg *.png *.jpeg *.gif")])
        if paths:
            self.image_paths = list(paths)
            self.img_label.config(text=f"{len(paths)} ảnh đã chọn", foreground="black")

    def select_output_folder(self):
        path = filedialog.askdirectory()
        if path:
            self.output_dir = path
            self.output_dir_label.config(text=f"Thư mục: {os.path.basename(path)}")


    def safe_run(self):
        self.status.config(text="🔄 Đang chuẩn bị...", foreground="#555")
        self.progress_bar["value"] = 0
        self.root.update_idletasks()

        try:
            asyncio.run(self.create_video())
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Lỗi khi render: {e}")
            messagebox.showerror("Lỗi", f"Render gặp lỗi:\n{e}\nKiểm tra console để biết thêm chi tiết.")
        finally:
            self.status.config(text="Hoàn tất hoặc gặp lỗi.", foreground="#555")
            self.root.update_idletasks()

    async def create_video(self):
        for f in os.listdir(output_temp_dir):
            if f.startswith(("line_", "subtitle_", "temp_", "shard_", "concat_list")):
                try:
                    os.remove(os.path.join(output_temp_dir, f))
                except:
                    pass

        if not self.text_path or not self.image_paths:
            messagebox.showerror("Lỗi", "Vui lòng chọn file văn bản và ít nhất một ảnh.")
            self.status.config(text="Lỗi: Chưa đủ đầu vào.", foreground="red")
            return

        selected_speaker_name = self.voice_option.get()
        speaker_id = None
        for s in self.voicevox_speakers:
            if s["name"] == selected_speaker_name:
                speaker_id = s["id"]
                break

        if speaker_id is None:
            messagebox.showerror("Lỗi", "Không tìm thấy ID speaker Voicevox. Vui lòng chọn một giọng hợp lệ.")
            self.status.config(text="Lỗi: Speaker không hợp lệ.", foreground="red")
            return
            
        font_name = self.font_option.get()
        font_path = os.path.join(os.environ['WINDIR'], 'Fonts', font_name)
        if not os.path.exists(font_path):
            messagebox.showwarning("Cảnh báo Font", f"Không tìm thấy font '{font_name}'. Sử dụng font mặc định.")
            font_path = "arial.ttf"

        with open(self.text_path, "r", encoding="utf-8") as f:
            sentences = split_sentences(f.read())
        
        if not sentences:
            messagebox.showwarning("Cảnh báo", "File văn bản không chứa câu nào hợp lệ.")
            self.status.config(text="Hoàn tất: Không có câu để xử lý.", foreground="orange")
            return

        try:
            volume = int(self.volume_entry.get())
            if not (0 <= volume <= 200):
                raise ValueError("Âm lượng phải trong khoảng 0-200.")
        except ValueError as e:
            messagebox.showerror("Lỗi nhập liệu", f"Âm lượng giọng không hợp lệ: {e}. Đặt lại 100.")
            self.volume_entry.set("100")
            volume = 100

        try:
            speed = float(self.voice_speed.get())
            if not (0.5 <= speed <= 2.0):
                raise ValueError("Tốc độ phải trong khoảng 0.5-2.0.")
        except ValueError as e:
            messagebox.showerror("Lỗi nhập liệu", f"Tốc độ giọng không hợp lệ: {e}. Đặt lại 1.0.")
            self.voice_speed.set("1.0")
            speed = 1.0

        try:
            stroke_size = int(self.stroke_size.get())
            if not (0 <= stroke_size <= 10):
                raise ValueError("Kích thước viền phải trong khoảng 0-10.")
        except ValueError as e:
            messagebox.showerror("Lỗi nhập liệu", f"Kích thước viền không hợp lệ: {e}. Đặt lại 2.")
            self.stroke_size.set("2")
            stroke_size = 2

        bg_opacity = self.bg_opacity.get()
        selected_encoder = self.encoder_option.get()

        num_shards = os.cpu_count() 
        shard_size = math.ceil(len(sentences) / num_shards)
        shard_paths = []
        progress_queue = Queue()
        sem = asyncio.Semaphore(os.cpu_count()) 

        total_sentences = len(sentences)
        self.status.config(text=f"🔄 Đang xử lý {total_sentences} câu... (0%)", foreground="blue")
        self.progress_bar["maximum"] = total_sentences
        self.progress_bar["value"] = 0
        self.root.update_idletasks()

        tasks = []
        for i in range(num_shards):
            part_texts = sentences[i * shard_size:(i + 1) * shard_size]
            if not part_texts:
                continue

            out_path = os.path.join(output_temp_dir, f"shard_{i}.mp4")
            shard_paths.append(out_path)

            tasks.append(
                render_shard(
                    i, part_texts, speaker_id, self.image_paths,
                    font_path, self.subtitle_color, self.stroke_color,
                    self.bg_color, self.effect_option.get(), out_path, selected_encoder,
                    progress_queue, volume, bg_opacity, speed, stroke_size, sem
                )
            )
        
        await asyncio.gather(*tasks)

        self.status.config(text="🔗 Đang ghép video cuối cùng...", foreground="green")
        self.root.update()

        existing_shard_paths = [p for p in shard_paths if os.path.exists(p)]
        if not existing_shard_paths:
            messagebox.showerror("Lỗi", "Không có phần video nào được tạo để ghép. Vui lòng kiểm tra lại quá trình xử lý.")
            self.status.config(text="Lỗi: Không có video để ghép.", foreground="red")
            return

        concat_list_file_path = os.path.join(output_temp_dir, "concat_list.txt")
        with open(concat_list_file_path, "w", encoding="utf-8") as f:
            for p in existing_shard_paths:
                f.write(f"file '{normalize_path_for_ffmpeg(p)}'\n") # Sử dụng normalize_path_for_ffmpeg cho đường dẫn

        final_output = os.path.join(self.output_dir, self.output_name.get())
        ffmpeg_path = get_ffmpeg_path()
        if ffmpeg_path is None:
            self.status.config(text="Lỗi: FFmpeg không tìm thấy.", foreground="red")
            return

        # Cấu hình StartupInfo để ẩn cửa sổ console
        si = None
        if sys.platform == "win32":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE

        try:
            # Truyền đường dẫn đã được normalize_path_for_ffmpeg trực tiếp vào list
            concat_cmd = [
                ffmpeg_path, '-y', '-f', 'concat', '-safe', '0', 
                '-i', normalize_path_for_ffmpeg(concat_list_file_path),
                '-c', 'copy', normalize_path_for_ffmpeg(final_output)
            ]
            concat_cmd = [arg.strip() for arg in concat_cmd if arg.strip()]

            subprocess.run(concat_cmd, check=True, stderr=subprocess.PIPE, startupinfo=si)
            final_output_display = final_output.replace(os.sep, '/')
            self.status.config(text=f"✅ Xong! Video đã lưu tại: {final_output_display}", foreground="darkgreen")
            messagebox.showinfo("Hoàn tất", f"Đã tạo video thành công:\n{final_output_display}")
        except subprocess.CalledProcessError as e:
            error_message = f"Lỗi khi ghép video:\n{e.stderr.decode() if e.stderr else 'Unknown FFmpeg error.'}"
            print(error_message)
            messagebox.showerror("Lỗi ghép video", error_message)
            self.status.config(text="Lỗi ghép video.", foreground="red")
        except Exception as e:
            messagebox.showerror("Lỗi", f"Lỗi không xác định khi ghép video: {e}")
            self.status.config(text="Lỗi ghép video không xác định.", foreground="red")


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    root = tk.Tk()
    app = AutoVideoCreator(root)
    root.mainloop()