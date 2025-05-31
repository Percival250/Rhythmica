from yt_dlp import YoutubeDL
import re
import os
from app.config_handler import load_download_dir

def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "", name)

def download_song_as_mp3(song_name): 
    save_dir = load_download_dir()
    if not save_dir:
        raise ValueError("Путь загрузки не задан. Пожалуйста, укажите директорию на странице настроек.")

    sanitized_name = sanitize_filename(song_name)

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': os.path.join(save_dir, f'{sanitized_name}.%(ext)s'),
        'quiet': False,
        'noplaylist': True,
        'ffmpeg_location': r"C:\ProgramData\chocolatey\bin",
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    }

    with YoutubeDL(ydl_opts) as ydl:
        try:
            result = ydl.download([f"ytsearch1:{song_name}"])
            output_path = os.path.join(save_dir, f"{sanitized_name}.mp3")
            if os.path.exists(output_path):
                return output_path  # ✅ теперь возвращаем путь
            else:
                print("Файл не найден после загрузки:", output_path)
                return None
        except Exception as e:
            print(f"Ошибка загрузки: {e}")
            return None
