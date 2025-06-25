# --- File: video_worker.py ---

import os
import subprocess
import sys
# Import for PyInstaller's temporary path
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if sys.platform == "win32":
    CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW
else:
    CREATE_NO_WINDOW = 0

from PIL import Image, ImageDraw, ImageFont
import tempfile
import re
import asyncio
from concurrent.futures import ThreadPoolExecutor
# import moviepy.editor as mp # REMOVED: No longer using moviepy
import requests # Add requests library for API calls
import json # Thêm thư viện json để đọc output của ffprobe

output_temp_dir = tempfile.gettempdir()
executor = ThreadPoolExecutor(max_workers=min(24, os.cpu_count()))

def get_ffmpeg_path():
    # Get the path to ffmpeg.exe in the same directory as the script or _MEIPASS
    return os.path.join(BASE_DIR, "ffmpeg", "ffmpeg.exe")

def get_ffprobe_path():
    # Get the path to ffprobe.exe (usually in the same directory as ffmpeg.exe)
    ffprobe_path = os.path.join(BASE_DIR, "ffmpeg", "ffprobe.exe")
    if not os.path.exists(ffprobe_path):
        # Nếu không tìm thấy ffprobe.exe, báo lỗi hoặc dùng ffmpeg làm fallback nếu có thể (nhưng ffprobe tốt hơn cho mediainfo)
        print(f"[\u26a0\ufe0f] Cảnh báo: Không tìm thấy ffprobe.exe tại {ffprobe_path}. Media info có thể không chính xác.")
        return None
    return ffprobe_path

# Helper function to normalize paths for FFmpeg (using forward slashes)
def normalize_path_for_ffmpeg(path):
    # Ensure path is properly quoted for FFmpeg in case of spaces
    # subprocess.run with list of arguments handles quoting automatically if paths are clean
    # For FFmpeg filter_complex, we need forward slashes, so replace backslashes
    return os.path.normpath(path).replace('\\', '/')

def split_sentences(text):
    # Improved sentence splitting for Japanese and Vietnamese
    # Splits by common sentence-ending punctuation and Unicode newlines
    return [s.strip() for s in re.split(r'[\u3002\uFF0E.!?\n]', text) if s.strip()]

def wrap_text(draw, text, font, max_width):
    # Function to wrap text to fit within a maximum width
    lines = []
    line = ''
    for ch in text:
        test_line = line + ch
        # draw.textlength() calculates text length with the given font
        if draw.textlength(test_line, font=font) <= max_width:
            line = test_line
        else:
            lines.append(line)
            line = ch
    if line:
        lines.append(line)
    return lines

async def generate_voicevox_audio(sentence, speaker_id, output_path, rate=1.0):
    VOICEVOX_API_BASE = "http://127.0.0.1:50021" 
    try:
        query_params = {
            "text": sentence,
            "speaker": speaker_id,
            "speedScale": rate
        }
        query_response = await asyncio.to_thread(
            lambda: requests.post(f"{VOICEVOX_API_BASE}/audio_query", params=query_params, timeout=30)
        )
        query_response.raise_for_status()
        audio_query = query_response.json()

        synthesis_params = {
            "speaker": speaker_id
        }
        audio_response = await asyncio.to_thread(
            lambda: requests.post(f"{VOICEVOX_API_BASE}/synthesis", params=synthesis_params, json=audio_query, timeout=60)
        )
        audio_response.raise_for_status()

        with open(output_path, "wb") as f:
            f.write(audio_response.content)
        return True
    except requests.exceptions.ConnectionError:
        print("❌ Voicevox Engine connection error. Make sure Voicevox Engine is running at http://127.0.0.1:50021 and not blocked by firewall.")
        return False
    except requests.exceptions.Timeout:
        print(f"❌ Timeout calling Voicevox API for: {sentence[:30]}...")
        return False
    except requests.exceptions.RequestException as e:
        print(f"❌ Voicevox API error for: {sentence[:30]}... => {str(e)}")
        return False
    except Exception as e:
        print(f"❌ Unknown error generating Voicevox audio for: {sentence[:30]}... => {str(e)}")
        return False

async def generate_tts_audio(sentence, speaker_id, output_path, rate=1.0):
    return await generate_voicevox_audio(sentence, speaker_id, output_path, rate)

# Modified: Use ffprobe to get audio duration instead of pydub/mediainfo
def get_audio_duration(path):
    ffprobe_path = get_ffprobe_path()
    if ffprobe_path is None:
        return 5.0 # Fallback if ffprobe not found

    # Cấu hình StartupInfo để ẩn cửa sổ console
    si = None
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE

    try:
        # Sử dụng ffprobe để lấy thời lượng audio
        cmd = [
            ffprobe_path,
            '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            normalize_path_for_ffmpeg(path) # Đảm bảo đường dẫn được chuẩn hóa
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, startupinfo=si)
        duration = float(result.stdout.strip())
        return duration
    except (subprocess.CalledProcessError, ValueError) as e:
        print(f"❌ Error getting audio duration for {path}: {e}")
        return 5.0 # Default to 5 seconds if duration cannot be obtained
    except Exception as e:
        print(f"❌ Unknown error getting audio duration for {path}: {e}")
        return 5.0 # Default to 5 seconds if any other error

async def render_sentence(index, sentence, voice, img, font, draw, ffmpeg_path,
                          font_path, subtitle_color, stroke_color, bg_color,
                          effect, encoder, volume_factor, bg_opacity,
                          voice_speed, stroke_width, sem):
    async with sem:
        sentence = sentence.lstrip('\ufeff\u200b').strip()
        audio_path = os.path.join(output_temp_dir, f"line_{index}.mp3")

        success = await generate_tts_audio(sentence, voice, audio_path, voice_speed)
        if not success or not os.path.exists(audio_path):
            print(f"[\u26a0\ufe0f] Skipping sentence (audio error or not found): {sentence[:30]}...")
            return None

        duration = get_audio_duration(audio_path)
        wrapped = wrap_text(draw, sentence, font, max_width=1100)
        line_heights = [draw.textbbox((0, 0), line, font=font)[3] for line in wrapped]
        total_height = sum(line_heights) + (len(wrapped) - 1) * 10
        max_line_width = max(draw.textlength(line, font=font) for line in wrapped)

        sub_image_width = max(int(max_line_width) + 80, 200)
        sub_image_height = max(total_height + 40, 80)

        img_sub = Image.new("RGBA", (sub_image_width, sub_image_height), (0, 0, 0, 0))
        draw_sub = ImageDraw.Draw(img_sub)
        bg_rgb = Image.new("RGB", (1, 1), bg_color).getpixel((0, 0))
        
        draw_sub.rectangle([(0, 0), img_sub.size], fill=(*bg_rgb, int(bg_opacity)))

        y = 20
        for line, h in zip(wrapped, line_heights):
            x = (img_sub.size[0] - draw.textlength(line, font=font)) // 2
            draw_sub.text((x, y), line, font=font, fill=subtitle_color,
                          stroke_width=stroke_width, stroke_fill=stroke_color)
            y += h + 10

        sub_path = os.path.join(output_temp_dir, f"subtitle_{index}.png")
        img_sub.save(sub_path)

        temp_out = os.path.join(output_temp_dir, f"temp_{index}.mp4")
        
        fps = 25
        num_frames = max(1, int(duration * fps))
        
        vf_parts = [
            "scale=1280:720:force_original_aspect_ratio=increase",
            "crop=1280:720"
        ]
        
        try:
            with Image.open(img) as original_img:
                img_width, img_height = original_img.size
        except Exception:
            img_width, img_height = 1280, 720
            
        if img_width > 1280 and img_height > 720:
            if effect == "zoom":
                vf_parts.append(f"zoompan=z='min(zoom+0.0007,1.3)':d={num_frames}:s=1280x720") 
            elif effect == "pan":
                vf_parts.append(f"zoompan=z=1.0:x='if(eq(n,0),0,x+1)':y='if(eq(n,0),0,y+1)':d={num_frames}:s=1280x720")
            elif effect == "zoom+pan":
                vf_parts.append(f"zoompan=z='min(zoom+0.0007,1.3)':x='if(eq(n,0),iw/2,x+(iw-iw/zoom)/{num_frames}/4)':y='if(eq(n,0),ih/2,y+(ih-ih/zoom)/{num_frames}/4)':d={num_frames}:s=1280x720")

        vf_parts.append("pad=1280:720:(ow-iw)/2:(oh-ih)/2") 

        vf_chain = ",".join(vf_parts)

        # Sử dụng normalize_path_for_ffmpeg để đảm bảo định dạng đường dẫn đúng cho FFmpeg (chuyển dấu '\' thành '/')
        # Không cần thêm dấu ngoặc kép ở đây vì subprocess.run với list arguments sẽ tự động xử lý.
        norm_ffmpeg_path = get_ffmpeg_path()
        norm_img_path = normalize_path_for_ffmpeg(img)
        norm_audio_path = normalize_path_for_ffmpeg(audio_path)
        norm_sub_path = normalize_path_for_ffmpeg(sub_path)
        norm_temp_out = normalize_path_for_ffmpeg(temp_out)

        encoder_preset_option = ["-preset", "fast"]
        if encoder in ["h264_nvenc", "h264_amf", "h264_qsv"]:
            encoder_preset_option = []

        # Cấu hình StartupInfo để ẩn cửa sổ console
        si = None
        if sys.platform == "win32":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE

        # Sửa lỗi f-string: Truyền các đường dẫn đã được normalize_path_for_ffmpeg vào list mà không cần f-string bọc lại dấu ngoặc kép.
        cmd = [
            norm_ffmpeg_path, '-y', '-loop', '1', '-i', norm_img_path,
            '-i', norm_audio_path, '-i', norm_sub_path,
            '-filter_complex', f"[0:v]format=rgba,{vf_chain}[v_bg];[v_bg][2:v]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)-30:enable=\'between(t,0,{duration:.2f})\'[v];[1:a]volume={volume_factor}[a]",
            '-map', '[v]', '-map', '[a]', '-c:v', encoder,
        ] + encoder_preset_option + [
            '-threads', str(os.cpu_count()), '-shortest', norm_temp_out
        ]
        cmd = [arg.strip() for arg in cmd if arg.strip()]

        try:
            result = await asyncio.get_event_loop().run_in_executor(
                executor, lambda: subprocess.run(cmd, check=True, capture_output=True, text=True, startupinfo=si)
            )
        except subprocess.CalledProcessError as e:
            print(f"❌ FFmpeg error creating clip {index}:\nCommand: {' '.join(e.cmd) if isinstance(e.cmd, list) else e.cmd}\nReturn Code: {e.returncode}\nSTDOUT:\n{e.stdout}\nSTDERR:\n{e.stderr}")
            return None
        except Exception as e:
            print(f"❌ Unknown error running FFmpeg for clip {index}: {e}")
            return None

        if os.path.exists(temp_out):
            return temp_out
        return None

async def render_shard(
    shard_id, texts, voice, image_paths, font_path,
    subtitle_color, stroke_color, bg_color, effect,
    output_path, encoder, progress_queue,
    volume_percent=100, bg_opacity=255, voice_speed=1.0,
    stroke_width=1, sem=None
):
    ffmpeg_path = get_ffmpeg_path()
    font = ImageFont.truetype(font_path, 48)
    draw = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    volume_factor = float(volume_percent) / 100.0

    tasks = []
    num_images = len(image_paths)
    total_sentences_in_shard = sum(len(split_sentences(text)) for text in texts)
    
    if num_images == 0:
        print("[\u26a0\ufe0f] No images selected. Video will only have a black background.")
        temp_black_image = os.path.join(output_temp_dir, "black_placeholder.png")
        Image.new("RGB", (1280, 720), (0, 0, 0)).save(temp_black_image)
        image_paths = [temp_black_image]
        num_images = 1
        
    sentences_per_image_block = total_sentences_in_shard / num_images if num_images > 0 else 1
    
    current_sentence_count_in_shard = 0
    
    for idx_text, text_block in enumerate(texts):
        sentences_in_block = split_sentences(text_block)
        for sentence_idx_in_block, sentence in enumerate(sentences_in_block):
            if not sentence:
                continue
            
            image_index = int(current_sentence_count_in_shard / sentences_per_image_block)
            img = image_paths[image_index % num_images]

            task = render_sentence(
                index=f"{shard_id}_{idx_text}_{sentence_idx_in_block}",
                sentence=sentence,
                voice=voice,
                img=img,
                font=font,
                draw=draw,
                ffmpeg_path=ffmpeg_path,
                font_path=font_path,
                subtitle_color=subtitle_color,
                stroke_color=stroke_color,
                bg_color=bg_color,
                effect=effect,
                encoder=encoder,
                volume_factor=volume_factor,
                bg_opacity=bg_opacity,
                voice_speed=voice_speed,
                stroke_width=stroke_width,
                sem=sem
            )
            tasks.append(task)
            current_sentence_count_in_shard += 1

    results = await asyncio.gather(*tasks)
    valid_videos = [r for r in results if r is not None]

    if not valid_videos:
        print(f"[\u26a0\ufe0f] No valid videos were created for shard {shard_id}. Skipping concatenation.")
        return

    concat_txt = os.path.join(output_temp_dir, f"shard_{shard_id}_concat.txt")
    with open(concat_txt, "w", encoding="utf-8") as f:
        for v in valid_videos:
            f.write(f"file '{normalize_path_for_ffmpeg(v)}'\n")

    norm_ffmpeg_path = get_ffmpeg_path()
    norm_concat_txt = normalize_path_for_ffmpeg(concat_txt)
    norm_output_path = normalize_path_for_ffmpeg(output_path)

    # Cấu hình StartupInfo để ẩn cửa sổ console
    si = None
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE

    # Sửa lỗi f-string: Truyền các đường dẫn đã được normalize_path_for_ffmpeg vào list mà không cần f-string bọc lại dấu ngoặc kép.
    concat_cmd = [
        norm_ffmpeg_path, '-y', '-f', 'concat', '-safe', '0', 
        '-i', norm_concat_txt, '-c', 'copy', norm_output_path
    ]
    concat_cmd = [arg.strip() for arg in concat_cmd if arg.strip()]

    try:
        subprocess.run(concat_cmd, check=True, stderr=subprocess.PIPE, text=True, startupinfo=si)
    except subprocess.CalledProcessError as e:
        print(f"❌ FFmpeg error concatenating shard {shard_id}:\nCommand: {' '.join(e.cmd) if isinstance(e.cmd, list) else e.cmd}\nReturn Code: {e.returncode}\nSTDOUT:\n{e.stdout}\nSTDERR:\n{e.stderr}")
        raise