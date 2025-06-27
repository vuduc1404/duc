import sys
import os
import subprocess
import tempfile
import re
import asyncio
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageDraw, ImageFont
import requests
import json

# Thiết lập BASE_DIR để luôn đúng cả khi chạy bằng PyInstaller (đã đóng gói .exe)
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

EFFECTS_DIR = os.path.join(BASE_DIR, "effects")
print(f"[DEBUG] BASE_DIR: {BASE_DIR}")
print(f"[DEBUG] EFFECTS_DIR: {EFFECTS_DIR}")

if sys.platform == "win32":
    CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW
else:
    CREATE_NO_WINDOW = 0

output_temp_dir = tempfile.gettempdir()
executor = ThreadPoolExecutor(max_workers=min(24, os.cpu_count()))

def get_ffmpeg_path():
    return os.path.join(BASE_DIR, "ffmpeg", "ffmpeg.exe")

def get_ffprobe_path():
    ffprobe_path = os.path.join(BASE_DIR, "ffmpeg", "ffprobe.exe")
    if not os.path.exists(ffprobe_path):
        print(f"[⚠️] Cảnh báo: Không tìm thấy ffprobe.exe tại {ffprobe_path}. Media info có thể không chính xác.")
        return None
    return ffprobe_path

def normalize_path_for_ffmpeg(path):
    return os.path.normpath(path).replace('\\', '/')

def split_sentences(text):
    return [s.strip() for s in re.split(r'[\u3002\uFF0E.!?\n]', text) if s.strip()]

def wrap_text(draw, text, font, max_width):
    lines = []
    line = ''
    for ch in text:
        test_line = line + ch
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

async def generate_edge_tts_audio(sentence, speaker_id, output_path, rate=1.0):
    try:
        import edge_tts
        percent = int(round((rate - 1) * 100))
        if percent >= 0:
            rate_str = f"+{percent}%"
        else:
            rate_str = f"{percent}%"
        communicate = edge_tts.Communicate(text=sentence, voice=speaker_id, rate=rate_str)
        await communicate.save(output_path)
        if not os.path.exists(output_path) or os.path.getsize(output_path) < 1024:
            print(f"❌ edge-tts tạo file audio lỗi hoặc rỗng: {output_path}")
            return False
        return True
    except ImportError:
        print("❌ Chưa cài đặt edge-tts. Cài đặt với: pip install edge-tts")
        return False
    except Exception as e:
        print(f"❌ edge-tts error: {e}")
        return False

async def generate_tts_audio(sentence, speaker_id, output_path, rate=1.0, voice_source="Voicevox"):
    if voice_source.lower() == "edge-tts":
        return await generate_edge_tts_audio(sentence, speaker_id, output_path, rate)
    else:
        return await generate_voicevox_audio(sentence, speaker_id, output_path, rate)

def get_audio_duration(path):
    ffprobe_path = get_ffprobe_path()
    if ffprobe_path is None:
        return 5.0

    si = None
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE

    try:
        cmd = [
            ffprobe_path,
            '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            normalize_path_for_ffmpeg(path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, startupinfo=si)
        duration = float(result.stdout.strip())
        return duration
    except (subprocess.CalledProcessError, ValueError) as e:
        print(f"❌ Error getting audio duration for {path}: {e}")
        return 5.0
    except Exception as e:
        print(f"❌ Unknown error getting audio duration for {path}: {e}")
        return 5.0

async def render_sentence(
    index, sentence, voice, img_or_video, font, draw, ffmpeg_path,
    font_path, subtitle_color, stroke_color, bg_color, effect, encoder,
    volume_factor, bg_opacity, voice_speed, stroke_width, sem,
    video_speed=1.0, is_video_input=False, voice_source="Voicevox",
    effects_dir=None, overlay_effect="none" # thêm overlay_effect
):
    async with sem:
        sentence = sentence.lstrip('\ufeff\u200b').strip()
        audio_path = os.path.join(output_temp_dir, f"line_{index}.mp3")

        success = await generate_tts_audio(sentence, voice, audio_path, voice_speed, voice_source=voice_source)
        if not success or not os.path.exists(audio_path):
            print(f"[⚠️] Skipping sentence (audio error or not found): {sentence[:30]}...")
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

        encoder_preset_option = ["-preset", "fast"]
        if encoder in ["h264_nvenc", "h264_amf", "h264_qsv"]:
            encoder_preset_option = []

        si = None
        if sys.platform == "win32":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE

        # Sử dụng đường dẫn hiệu ứng dựa trên BASE_DIR cho mọi trường hợp
        EFFECTS_DIR_LOCAL = os.path.join(BASE_DIR, "effects") if effects_dir is None else effects_dir

        # In debug trước khi dùng hiệu ứng
        if overlay_effect in ["snow", "sakura"]:
            overlay_mov = os.path.join(EFFECTS_DIR_LOCAL, f"{overlay_effect}_alpha.mov")
            print(f"[DEBUG] overlay_mov: {overlay_mov}")

        if is_video_input:
            norm_ffmpeg_path = get_ffmpeg_path()
            norm_video_path = normalize_path_for_ffmpeg(img_or_video)
            norm_audio_path = normalize_path_for_ffmpeg(audio_path)
            norm_sub_path = normalize_path_for_ffmpeg(sub_path)
            norm_temp_out = normalize_path_for_ffmpeg(temp_out)

            vf_parts = [
                "scale=1280:720:force_original_aspect_ratio=increase",
                "crop=1280:720"
            ]
            if abs(float(video_speed) - 1.0) > 0.01:
                vf_parts.append(f"setpts=1/{video_speed}*PTS")
            vf_chain = ",".join(vf_parts)

            # Overlay hiệu ứng snow/sakura MOV nếu chọn
            filter_complex = ""
            inputs = [
                norm_video_path, norm_audio_path, norm_sub_path
            ]
            map_video = "[v]"
            if overlay_effect in ["snow", "sakura"] and EFFECTS_DIR_LOCAL is not None:
                overlay_mov = os.path.join(EFFECTS_DIR_LOCAL, f"{overlay_effect}_alpha.mov")
                print(f"[DEBUG] overlay_mov: {overlay_mov}")  # Chèn debug ở đây
                if os.path.exists(overlay_mov):
                    overlay_mov_ffmpeg = normalize_path_for_ffmpeg(overlay_mov)
                    inputs.append(overlay_mov_ffmpeg)
                    filter_complex = (
                        f"[0:v]{vf_chain}[vbg];"
                        f"[vbg][2:v]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)-30:enable='between(t,0,{duration:.2f})'[tmpv];"
                        f"[tmpv][3:v]overlay=0:0:shortest=1[v];"
                        f"[1:a]volume={volume_factor}[a]"
                    )
                    map_video = "[v]"
                else:
                    print(f"[⚠️] Không tìm thấy file hiệu ứng: {overlay_mov}")
                    filter_complex = (
                        f"[0:v]{vf_chain}[vbg];"
                        f"[vbg][2:v]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)-30:enable='between(t,0,{duration:.2f})'[v];"
                        f"[1:a]volume={volume_factor}[a]"
                    )
            else:
                filter_complex = (
                    f"[0:v]{vf_chain}[vbg];"
                    f"[vbg][2:v]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)-30:enable='between(t,0,{duration:.2f})'[v];"
                    f"[1:a]volume={volume_factor}[a]"
                )

            cmd = [norm_ffmpeg_path, '-y']
            for ip in inputs:
                cmd.extend(['-i', ip])
            cmd.extend([
                '-filter_complex', filter_complex,
                '-map', map_video, '-map', '[a]',
                '-c:v', encoder, '-r', '25'
            ])
            cmd += encoder_preset_option + [
                '-threads', str(os.cpu_count()), '-shortest', '-an', norm_temp_out
            ]
            cmd = [arg for arg in cmd if arg]

            try:
                result = await asyncio.get_event_loop().run_in_executor(
                    executor, lambda: subprocess.run(cmd, check=True, capture_output=True, text=True, startupinfo=si)
                )
            except subprocess.CalledProcessError as e:
                print(f"❌ FFmpeg error creating video clip {index}:\nCommand: {' '.join(e.cmd) if isinstance(e.cmd, list) else e.cmd}\nReturn Code: {e.returncode}\nSTDOUT:\n{e.stdout}\nSTDERR:\n{e.stderr}")
                return None
            except Exception as e:
                print(f"❌ Unknown error running FFmpeg for video clip {index}: {e}")
                return None
            if os.path.exists(temp_out):
                return temp_out
            return None

        # --- Xử lý ẢNH INPUT ---
        norm_ffmpeg_path = get_ffmpeg_path()
        norm_img_path = normalize_path_for_ffmpeg(img_or_video)
        norm_audio_path = normalize_path_for_ffmpeg(audio_path)
        norm_sub_path = normalize_path_for_ffmpeg(sub_path)
        norm_temp_out = normalize_path_for_ffmpeg(temp_out)

        fps = 25
        num_frames = max(1, int(duration * fps))

        # Hiệu ứng zoom/pan/zoom+pan chỉ thêm khi ảnh lớn hơn 1280x720
        vf_parts = [
            "scale=1280:720:force_original_aspect_ratio=increase",
            "crop=1280:720"
        ]
        try:
            with Image.open(img_or_video) as original_img:
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

        # Áp dụng đồng thời hiệu ứng zoom/pan + overlay snow/sakura nếu chọn
        filter_complex = ""
        inputs = [
            norm_img_path, norm_audio_path, norm_sub_path
        ]
        map_video = "[v]"
        if overlay_effect in ["snow", "sakura"] and EFFECTS_DIR_LOCAL is not None:
            overlay_mov = os.path.join(EFFECTS_DIR_LOCAL, f"{overlay_effect}_alpha.mov")
            print(f"[DEBUG] overlay_mov: {overlay_mov}")   # Chèn debug ở đây
            if os.path.exists(overlay_mov):
                overlay_mov_ffmpeg = normalize_path_for_ffmpeg(overlay_mov)
                inputs.append(overlay_mov_ffmpeg)
                filter_complex = (
                    f"[0:v]format=rgba,{vf_chain}[v_bg];"
                    f"[v_bg][2:v]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)-30:enable='between(t,0,{duration:.2f})'[tmpv];"
                    f"[tmpv][3:v]overlay=0:0:shortest=1[v];"
                    f"[1:a]volume={volume_factor}[a]"
                )
                map_video = "[v]"
            else:
                print(f"[⚠️] Không tìm thấy file hiệu ứng: {overlay_mov}")
                filter_complex = (
                    f"[0:v]format=rgba,{vf_chain}[v_bg];"
                    f"[v_bg][2:v]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)-30:enable='between(t,0,{duration:.2f})'[v];"
                    f"[1:a]volume={volume_factor}[a]"
                )
        else:
            filter_complex = (
                f"[0:v]format=rgba,{vf_chain}[v_bg];"
                f"[v_bg][2:v]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)-30:enable='between(t,0,{duration:.2f})'[v];"
                f"[1:a]volume={volume_factor}[a]"
            )

        cmd = [
            norm_ffmpeg_path, '-y', '-loop', '1'
        ]
        for ip in inputs:
            cmd.extend(['-i', ip])
        cmd.extend([
            '-filter_complex', filter_complex,
            '-map', map_video, '-map', '[a]', '-c:v', encoder, '-r', '25',
        ] + encoder_preset_option + [
            '-threads', str(os.cpu_count()), '-shortest', norm_temp_out
        ])
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
    shard_id, texts, voice, image_or_video_paths, font_path,
    subtitle_color, stroke_color, bg_color, effect,
    output_path, encoder, progress_queue,
    volume_percent=100, bg_opacity=255, voice_speed=1.0,
    stroke_width=1, sem=None, video_speed=1.0, is_video_input=False,
    offset_in_all=0, voice_source="Voicevox", effects_dir=None,
    overlay_effect="none"
):
    ffmpeg_path = get_ffmpeg_path()
    font = ImageFont.truetype(font_path, 48)
    draw = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    volume_factor = float(volume_percent) / 100.0
    tasks = []
    num_files = len(image_or_video_paths)
    if num_files == 0:
        print("[⚠️] No images/videos selected. Video will only have a black background.")
        temp_black_image = os.path.join(output_temp_dir, "black_placeholder.png")
        Image.new("RGB", (1280, 720), (0, 0, 0)).save(temp_black_image)
        image_or_video_paths = [temp_black_image]
        num_files = 1

    global_sentence_idx = offset_in_all
    for idx_text, text_block in enumerate(texts):
        sentences_in_block = split_sentences(text_block)
        for sentence_idx_in_block, sentence in enumerate(sentences_in_block):
            if not sentence:
                continue
            file_index = global_sentence_idx % num_files
            file_path = image_or_video_paths[file_index]

            # Truyền riêng effect (zoom/pan/zoom+pan/none) và overlay_effect (snow/sakura/none) xuống render_sentence
            task = render_sentence(
                index=f"{shard_id}_{idx_text}_{sentence_idx_in_block}",
                sentence=sentence,
                voice=voice,
                img_or_video=file_path,
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
                sem=sem,
                video_speed=video_speed,
                is_video_input=is_video_input,
                voice_source=voice_source,
                effects_dir=effects_dir,  # EFFECTS_DIR sẽ mặc định là BASE_DIR/effects nếu None
                overlay_effect=overlay_effect
            )
            tasks.append(task)
            global_sentence_idx += 1

    results = await asyncio.gather(*tasks)
    valid_videos = [r for r in results if r is not None]

    if not valid_videos:
        print(f"[⚠️] No valid videos were created for shard {shard_id}. Skipping concatenation.")
        return

    concat_txt = os.path.join(output_temp_dir, f"shard_{shard_id}_concat.txt")
    with open(concat_txt, "w", encoding="utf-8") as f:
        for v in valid_videos:
            f.write(f"file '{normalize_path_for_ffmpeg(v)}'\n")

    norm_ffmpeg_path = get_ffmpeg_path()
    norm_concat_txt = normalize_path_for_ffmpeg(concat_txt)
    norm_output_path = normalize_path_for_ffmpeg(output_path)

    si = None
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE

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