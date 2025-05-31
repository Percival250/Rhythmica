import requests
import random
import httpx
import json
import os
import asyncio
from datetime import datetime
from sqlalchemy import func
from typing import Optional
from fastapi import FastAPI, Request, Form, Depends, HTTPException, Query, Path, APIRouter
from fastapi.responses import RedirectResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.models import models
from app.models.models import User, Song, FavoriteSong, Playlist, Download, UserPreference, FeedSong, FeedSongCache, UserFeedCacheInfo
from app import database
from passlib.context import CryptContext  
from typing import Optional
from app.downloader import download_song_as_mp3
from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from app.config_handler import save_download_dir, load_download_dir
from starlette.middleware.sessions import SessionMiddleware
from fastapi.responses import JSONResponse
from app.api_routes import api_router


app = FastAPI(title="Rhythmica API")
app.add_middleware(SessionMiddleware, secret_key="your-secret-key")
app.mount("/static", StaticFiles(directory="static"), name="static")
router = APIRouter()
templates = Jinja2Templates(directory="templates")
app.include_router(api_router, prefix="/api")

# Пароли
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

def get_current_user_id(request: Request, db: Session = Depends(get_db)) -> int:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")
    return user_id

async def get_genre_from_deezer(artist: str, title: str) -> str:
    """
    Получает жанр песни по названию и исполнителю через Deezer API.
    """
    try:
        search_url = f"https://api.deezer.com/search?q=artist:\"{artist}\" track:\"{title}\""

        async with httpx.AsyncClient() as client:
            response = await client.get(search_url)

        if response.status_code != 200:
            return 'Unknown'

        data = response.json()
        if not data['data']:
            return 'Unknown'

        track = data['data'][0]
        album_id = track['album']['id']

        # Теперь получаем жанр через альбом
        album_url = f"https://api.deezer.com/album/{album_id}"

        async with httpx.AsyncClient() as client:
            album_response = await client.get(album_url)

        if album_response.status_code != 200:
            return 'Unknown'

        album_data = album_response.json()
        genres = album_data.get('genres', {}).get('data', [])

        if genres:
            return genres[0].get('name', 'Unknown')

        return 'Unknown'

    except Exception as e:
        print(f"Ошибка при получении жанра из Deezer: {e}")
        return 'Unknown'


def limit_preferences(pref_str: str, max_len: int = 5) -> str:
    if not pref_str:
        return pref_str
    items = [item.strip() for item in pref_str.split(",") if item.strip()]
    if len(items) <= max_len:
        return ",".join(items)
    # Всегда оставляем первый, дальше максимум (max_len - 1)
    limited = [items[0]] + items[1:max_len]
    return ",".join(limited)

async def fetch_genre_id(client: httpx.AsyncClient, genre_name: str) -> Optional[int]:
    r = await client.get("https://api.deezer.com/genre")
    if r.status_code == 200:
        genres = r.json().get("data", [])
        for g in genres:
            if g["name"].lower() == genre_name.lower():
                return g["id"]
    return None 

async def fetch_artist_top_songs(client: httpx.AsyncClient, artist_id: int):
    r = await client.get(f"https://api.deezer.com/artist/{artist_id}/top")
    if r.status_code == 200:
        return r.json().get("data", [])
    return []

async def fetch_genre_artists(client: httpx.AsyncClient, genre_id: int):
    r = await client.get(f"https://api.deezer.com/genre/{genre_id}/artists")
    if r.status_code == 200:
        return r.json().get("data", [])
    return []

async def fetch_songs_by_artist_name(client: httpx.AsyncClient, artist_name: str):
    r = await client.get("https://api.deezer.com/search", params={"q": artist_name})
    if r.status_code == 200:
        return r.json().get("data", [])
    return []

async def create_feed_cache_async(user_id: int, db: Session, preferences):
    async with httpx.AsyncClient() as client:
        all_songs = []
        seen_titles = set()

        # 1. Получаем жанры и их ID
        genres = [g.strip() for g in preferences.favorite_genre.split(",") if g.strip()]
        genre_ids = await asyncio.gather(*[fetch_genre_id(client, g) for g in genres])
        genre_ids = [gid for gid in genre_ids if gid]

        # 2. Получаем всех артистов по жанрам
        artists_lists = await asyncio.gather(*[fetch_genre_artists(client, gid) for gid in genre_ids])
        all_artists = [artist for sublist in artists_lists for artist in sublist]

        # 3. Получаем топ песни артистов по жанрам
        songs_lists = await asyncio.gather(*[fetch_artist_top_songs(client, artist["id"]) for artist in all_artists])

        for songs in songs_lists:
            for song in songs:
                if song['title'] not in seen_titles:
                    all_songs.append(song)
                    seen_titles.add(song['title'])

        # 4. Добавляем песни по любимым артистам
        artists = [a.strip() for a in preferences.favorite_artist.split(",") if a.strip()]
        artist_songs_lists = await asyncio.gather(*[fetch_songs_by_artist_name(client, a) for a in artists])

        for songs in artist_songs_lists:
            for song in songs:
                if song['title'] not in seen_titles:
                    all_songs.append(song)
                    seen_titles.add(song['title'])

        # 5. Перемешиваем
        random.shuffle(all_songs)

        # 6. Сохраняем песни в таблицу songs (если нет) и кэшируем их
        for song in all_songs:
            existing = db.query(Song).filter_by(api_id=song["id"]).first()
            if not existing:
                genre = await get_genre_from_deezer(song["artist"]["name"], song["title"])
                existing = Song(
                    api_id=song["id"],
                    title=song["title"],
                    artist=song["artist"]["name"],
                    genre=genre
                )
                db.add(existing)
                db.commit()
                db.refresh(existing)

            # Добавляем в кэш
            db.add(FeedSongCache(
                user_id=user_id,
                song_id=existing.id,  # Ссылаемся на сохранённую песню
                title=song["title"],
                artist=song["artist"]["name"],
                album_cover=song["album"]["cover_small"],
                preview_url=song.get("preview", "")
            ))

        db.commit()

def load_config():
    with open("config.json", "r", encoding="utf-8") as file:
        return json.load(file)

# Получение всех mp3 файлов в указанной директории
def get_all_songs_from_device(download_dir):
    mp3_files = []
    for root, dirs, files in os.walk(download_dir):
        for file in files:
            if file.endswith(".mp3"):
                full_path = os.path.join(root, file)

                # Разделим название файла на title и artist (если возможно)
                # Пример: "Adele - Hello.mp3" => artist: Adele, title: Hello
                filename = os.path.splitext(file)[0]  # убираем .mp3
                if " - " in filename:
                    artist, title = filename.split(" - ", 1)
                else:
                    artist = "Unknown"
                    title = filename

                mp3_files.append({
                    "file_path": full_path,
                    "title": title.strip(),
                    "artist": artist.strip(),
                    "genre": "rap"  # можно заменить на определение по-другому
                })
    return mp3_files

# Добавление песни в базу данных, если её нет
def add_songs_to_db(songs_on_device, db: Session, user_id: int):
    for song in songs_on_device:
        file_name = os.path.basename(song["file_path"])

        # Проверяем, есть ли уже такая запись в Download
        existing_download = (
            db.query(Download)
            .filter_by(user_id=user_id, file_path=file_name)
            .first()
        )

        # Ищем песню в таблице Song
        db_song = (
            db.query(Song)
            .filter_by(title=song["title"], artist=song["artist"])
            .first()
        )

        # Если песни нет в базе — пропускаем
        if not db_song:
            continue

        if existing_download:
            # Обновляем song_id, если нужно
            existing_download.song_id = db_song.id
        else:
            # Создаём новую запись в Download
            download = Download(
                song_id=db_song.id,
                user_id=user_id,
                file_path=file_name,
                downloaded_at=datetime.now()
            )
            db.add(download)

    db.commit()

# API Models
class UserCreate(BaseModel):
    username: str
    password: str

# ========== РОУТЫ ========== #

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# Страница регистрации
@app.get("/register", response_class=HTMLResponse)
async def register(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})
@app.post("/register")
async def register_user(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Form(...),
    password: str = Form(...)
):
    existing_user = db.query(User).filter(User.username == username).first()
    if existing_user:
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": "Пользователь уже существует"
        })

    hashed_password = pwd_context.hash(password)
    new_user = User(username=username, password=hashed_password)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
 
    # Сохраняем ID пользователя в сессии
    request.session["user_id"] = new_user.id

    return RedirectResponse("/preferences", status_code=303)
# Страница входа
@app.get("/login", response_class=HTMLResponse)
async def login(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})
    
@app.post("/login")
async def login(request: Request, db: Session = Depends(get_db), username: str = Form(...), password: str = Form(...)):
    user = db.query(User).filter(User.username == username).first()
    if not user or not pwd_context.verify(password, user.password):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Неверные данные"})

    request.session["user_id"] = user.id

    # Проверяем, есть ли предпочтения
    preferences = db.query(UserPreference).filter(UserPreference.user_id == user.id).first()
    if preferences:
        return RedirectResponse("/my-songs", status_code=303)
    else:
        return RedirectResponse("/preferences", status_code=303)

@app.get("/songs", response_class=HTMLResponse)
def show_songs(request: Request, q: Optional[str] = None):
    songs = []
    genre_cache = {}  # Кэш по album_id

    if q:
        response = requests.get("https://api.deezer.com/search", params={"q": q})
        if response.status_code == 200:
            results = response.json().get("data", [])
            for song_data in results:
                # album_id = song_data.get("album", {}).get("id")
                # genre_name = "Unknown"

                # if album_id:
                #     if album_id in genre_cache:
                #         genre_name = genre_cache[album_id]
                #     else:
                #         album_resp = requests.get(f"https://api.deezer.com/album/{album_id}")
                #         if album_resp.ok:
                #             genre_list = album_resp.json().get("genres", {}).get("data", [])
                #             if genre_list:
                #                 genre_name = genre_list[0].get("name", "Unknown")
                #         genre_cache[album_id] = genre_name

                # song_data["genre"] = genre_name
                songs.append(song_data)

    return templates.TemplateResponse("songs.html", {
        "request": request,
        "songs": songs,
        "query": q or ""
    })

# Обработчик скачивания песни
@app.post("/download")
async def download_song(
    request: Request,
    title: str = Form(...),
    artist: str = Form(...),
    genre: str = Form("Unknown"),
    query: str = Form(None),
    referer: str = Form(None),
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db)
):
    try:
        search_query = f"{artist} {title}"
        result = download_song_as_mp3(search_query)

        if not isinstance(result, str):
            return JSONResponse(content={
                "success": False,
                "error": "Ошибка загрузки файла",
                "redirect": f"/songs?q={query or ''}&status=error"
            })

        title_clean = title.strip().lower()
        artist_clean = artist.strip().lower()

        existing_song = db.query(models.Song).filter(
            func.lower(models.Song.title) == title_clean,
            func.lower(models.Song.artist) == artist_clean
        ).first()

        if existing_song:
            song = existing_song
        else:
            if genre == "Unknown":
                genre = await get_genre_from_deezer(artist, title)

            song = models.Song(title=title, artist=artist, genre=genre)
            db.add(song)
            db.commit()
            db.refresh(song)

        filename = os.path.basename(result)

        existing_download = db.query(models.Download).filter_by(
            song_id=song.id,
            user_id=user_id,
            file_path=filename
        ).first()

        if not existing_download:
            downloaded_song = models.Download(
                song_id=song.id,
                user_id=user_id,
                file_path=filename,
                downloaded_at=datetime.now()
            )
            db.add(downloaded_song)

        # Обновляем предпочтения пользователя
        existing_pref = db.query(models.UserPreference).filter_by(user_id=user_id).first()
        if not existing_pref:
            new_pref = models.UserPreference(user_id=user_id, favorite_artist=artist, favorite_genre=genre)
            db.add(new_pref)
        else:
            new_artists = list(filter(None, set((existing_pref.favorite_artist or "").split(",") + [artist])))
            existing_pref.favorite_artist = limit_preferences(",".join(new_artists))
            
            new_genres = list(filter(None, set((existing_pref.favorite_genre or "").split(",") + [genre])))
            existing_pref.favorite_genre = limit_preferences(",".join(new_genres))

        db.commit()

        response_data = {
            "success": True,
            "redirect": "/feed" if referer == "feed" else f"/songs?q={query}&status=success"
        }
        return JSONResponse(content=response_data)

    except Exception as e:
        return JSONResponse(content={
            "success": False,
            "error": str(e),
            "redirect": f"/songs?q={query or ''}&status=error"
        })

@app.post("/like")
async def like_song(
    request: Request,
    title: str = Form(...),
    artist: str = Form(...),
    genre: str = Form("Unknown"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        song = db.query(models.Song).filter_by(title=title, artist=artist).first()
        if not song:
            if genre == "Unknown":
                genre = await get_genre_from_deezer(artist, title)
            song = models.Song(title=title, artist=artist, genre=genre)
            db.add(song)
            db.commit()
            db.refresh(song)

        existing_like = db.query(models.FavoriteSong).filter_by(song_id=song.id, user_id=current_user.id).first()
        if existing_like:
            return JSONResponse(content={"success": False, "message": "Песня уже лайкнута"}, status_code=409)

        new_like = models.FavoriteSong(song_id=song.id, user_id=current_user.id)
        db.add(new_like)

        prefs = db.query(models.UserPreference).filter_by(user_id=current_user.id).first()
        if not prefs:
            prefs = models.UserPreference(
                user_id=current_user.id,
                favorite_artist=artist,
                favorite_genre=genre
            )
            db.add(prefs)
        else:
            new_artists = list(filter(None, set((prefs.favorite_artist or "").split(",") + [artist])))
            prefs.favorite_artist = limit_preferences(",".join(new_artists))
            
            new_genres = list(filter(None, set((prefs.favorite_genre or "").split(",") + [genre])))
            prefs.favorite_genre = limit_preferences(",".join(new_genres))

        db.commit()
        return JSONResponse(content={"success": True, "message": "Песня лайкнута"})

    except Exception as e:
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)

@app.get("/set_download_dir")
async def get_download_dir_page(request: Request):
    return templates.TemplateResponse("set_download_dir.html", {"request": request})

@app.post("/set_download_dir")
async def set_download_dir(path: str = Form(...)):
    save_download_dir(path)
    return RedirectResponse(url="/songs", status_code=302)

@app.get("/preferences", response_class=HTMLResponse)
def show_preferences(request: Request):
    return templates.TemplateResponse("preferences.html", {"request": request})

# Обработка формы предпочтений

@app.post("/preferences")
def save_preferences(
    request: Request,
    db: Session = Depends(get_db),
    favorite_artist: str = Form(...),
    favorite_genre: str = Form(...)
):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)

    existing = db.query(UserPreference).filter_by(user_id=user_id).first()

    def merge_items(existing_str, new_item):
        existing_items = [item.strip() for item in existing_str.split(',')] if existing_str else []
        if new_item not in existing_items:
            existing_items.append(new_item)
        return ",".join(existing_items)

    if existing:
        existing.favorite_artist = limit_preferences(merge_items(existing.favorite_artist, favorite_artist))
        existing.favorite_genre = limit_preferences(merge_items(existing.favorite_genre, favorite_genre))
        existing.version += 1  # увеличиваем версию
    else:
        db.add(UserPreference(
            user_id=user_id,
            favorite_artist=favorite_artist,
            favorite_genre=favorite_genre,
            version=1
        ))
 
    db.commit()
    return RedirectResponse("/my-songs", status_code=302)

@app.get("/feed", response_class=HTMLResponse)
async def feed(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)

    preferences = db.query(UserPreference).filter(UserPreference.user_id == user_id).first()
    if not preferences:
        return RedirectResponse("/preferences", status_code=303)

    # Очищаем кеш при загрузке, если нужно (можно добавить логику версии)
    db.query(FeedSong).filter(FeedSong.user_id == user_id).delete()
    db.query(FeedSongCache).filter(FeedSongCache.user_id == user_id).delete()
    db.commit()

    await create_feed_cache_async(user_id, db, preferences)

    # Обновляем версию кеша
    cache_info = db.query(UserFeedCacheInfo).filter(UserFeedCacheInfo.user_id == user_id).first()
    if not cache_info:
        cache_info = UserFeedCacheInfo(user_id=user_id, version=preferences.version)
        db.add(cache_info)
    else:
        cache_info.version = preferences.version
    db.commit()

    return templates.TemplateResponse("feed.html", {
        "request": request,
        "preferences": preferences
    })

@app.get("/feed/slice")
async def feed_slice(
    request: Request,
    limit: int = Query(10, gt=0, le=15),
    db: Session = Depends(get_db)
):
    user_id = request.session.get("user_id")
    if not user_id:
        return JSONResponse(content={"songs": []})

    pref = db.query(UserPreference).filter_by(user_id=user_id).first()
    if not pref:
        return JSONResponse(content={"songs": []})

    cache_info = db.query(UserFeedCacheInfo).filter_by(user_id=user_id).first()
    cached_version = cache_info.version if cache_info else None

    if cached_version != pref.version:
        db.query(FeedSong).filter(FeedSong.user_id == user_id).delete()
        db.query(FeedSongCache).filter(FeedSongCache.user_id == user_id).delete()
        db.commit()

        await create_feed_cache_async(user_id, db, pref)

        if not cache_info:
            cache_info = UserFeedCacheInfo(user_id=user_id, version=pref.version)
            db.add(cache_info)
        else:
            cache_info.version = pref.version
        db.commit()

    shown_ids = {sid for (sid,) in db.query(FeedSong.song_id).filter_by(user_id=user_id).all()}

    new_songs = db.query(FeedSongCache).filter(
        FeedSongCache.user_id == user_id,
        ~FeedSongCache.song_id.in_(shown_ids)
    ).limit(limit).all()

    if not new_songs:
        return JSONResponse(content={"songs": []})

    songs = []
    for song in new_songs:
        songs.append({
            "id": song.song_id,
            "title": song.title,
            "artist": {"name": song.artist},
            "album": {"cover_small": song.album_cover},
            "preview": song.preview_url
        })

        exists = db.query(FeedSong).filter_by(user_id=user_id, song_id=song.song_id).first()
        if not exists:
            db.add(FeedSong(
                user_id=user_id,
                song_id=song.song_id,
                title=song.title,
                artist=song.artist
            ))

    db.commit()
    return JSONResponse(content={"songs": songs})

@app.get("/my-songs", response_class=HTMLResponse)
def my_songs(request: Request, db: Session = Depends(get_db)):
    # Получаем user_id из сессии
    user_id = request.session.get("user_id")
    
    # Если нет user_id (пользователь не авторизован), редиректим на страницу логина
    if not user_id:
        return RedirectResponse("/login", status_code=303)

    # Загружаем конфигурацию
    config = load_config()
    download_dir = config.get("download_dir", "")

    if not download_dir:
        return {"error": "Download directory is not configured."}

    # Получаем все песни с устройства
    songs_on_device = get_all_songs_from_device(download_dir)

    # Добавляем песни в базу данных (с учетом обновления записей)
    add_songs_to_db(songs_on_device, db, user_id)

    # ⬇️ ВАЖНО: исключаем записи с отсутствующим song_id
    songs = (
        db.query(Download)
        .filter_by(user_id=user_id)
        .filter(Download.song_id.isnot(None))  # <--- эта строка — ключевая
        .all()
    )

    # Получаем все плейлисты пользователя
    playlists = db.query(Playlist).filter_by(user_id=user_id).all()

    # Возвращаем HTML-страницу с данными (песни и плейлисты)
    return templates.TemplateResponse("my_songs.html", {
        "request": request,
        "songs": songs,
        "playlists": playlists
    })

@app.post("/songs/{download_id}/delete")
def delete_user_song(
    request: Request,
    db: Session = Depends(get_db),
    download_id: int = Path(...)
):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)

    # Получаем запись загрузки
    download = db.query(Download).filter_by(id=download_id, user_id=user_id).first()
    if not download:
        raise HTTPException(status_code=404, detail="Song not found")

    # Удаляем файл с устройства, если он существует
    if download.file_path and os.path.exists(download.file_path):
        os.remove(download.file_path)

    # Удаляем запись из базы данных
    db.delete(download)
    db.commit()

    return RedirectResponse("/my-songs", status_code=303)

@app.post("/playlists/create")
def create_playlist(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...)
):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)

    playlist = Playlist(name=name, user_id=user_id)
    db.add(playlist)
    db.commit()
    return RedirectResponse("/my-songs", status_code=303)

@app.post("/playlists/add")
def add_song_to_playlist(
    request: Request,
    db: Session = Depends(get_db),
    playlist_id: int = Form(...),
    song_id: int = Form(...),
):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)

    # Проверяем, существует ли плейлист у этого пользователя
    playlist = db.query(Playlist).filter_by(id=playlist_id, user_id=user_id).first()
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    # Проверяем, существует ли песня в таблице songs
    song = db.query(Song).filter_by(id=song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")

    # Добавляем песню в плейлист, если её ещё нет
    if song not in playlist.songs:
        playlist.songs.append(song)
        db.commit()

    return RedirectResponse("/my-songs", status_code=303)

@app.post("/playlists/{playlist_id}/rename")
def rename_playlist(
    request: Request,
    db: Session = Depends(get_db),
    playlist_id: int = Path(...),
    new_name: str = Form(...)
):
    user_id = request.session.get("user_id")
    playlist = db.query(Playlist).filter_by(id=playlist_id, user_id=user_id).first()
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    playlist.name = new_name
    db.commit()
    return RedirectResponse("/my-songs", status_code=303)

@app.post("/playlists/{playlist_id}/delete")
def delete_playlist(
    request: Request,
    db: Session = Depends(get_db),
    playlist_id: int = Path(...)
):
    user_id = request.session.get("user_id")
    playlist = db.query(Playlist).filter_by(id=playlist_id, user_id=user_id).first()
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    db.delete(playlist)
    db.commit()
    return RedirectResponse("/my-songs", status_code=303)

@app.get("/playlists/{playlist_id}", response_class=HTMLResponse)
def view_playlist(
    playlist_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")

    playlist = db.query(Playlist).filter(Playlist.id == playlist_id, Playlist.user_id == user_id).first()
    if not playlist:
        return HTMLResponse("Playlist not found", status_code=404)

    # Получаем все песни, которые принадлежат пользователю и находятся в этом плейлисте
    songs = []
    for download in db.query(Download).filter_by(user_id=user_id).all():
        if download.song and download.song.id in [song.id for song in playlist.songs]:
            songs.append({
                "song": download.song,
                "file_path": download.file_path  # Добавляем путь к файлу
            })

    return templates.TemplateResponse("playlist.html", {
        "request": request,
        "playlist": playlist,
        "songs": songs
    })

@app.post("/playlists/{playlist_id}/remove-song")
def remove_song_from_playlist(
    request: Request,
    db: Session = Depends(get_db),
    playlist_id: int = Path(...),
    song_id: int = Form(...),
):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)

    playlist = db.query(Playlist).filter_by(id=playlist_id, user_id=user_id).first()
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    song = db.query(Song).filter_by(id=song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")

    # Удаляем песню из плейлиста
    if song in playlist.songs:
        playlist.songs.remove(song)
        db.commit()

    return RedirectResponse(f"/playlists/{playlist_id}", status_code=303)

@app.get("/media/{path:path}")
def get_media(path: str): 
    config = load_config()
    base_dir = config.get("download_dir", "")

    full_path = os.path.normpath(os.path.join(base_dir, path))

    if not full_path.startswith(os.path.abspath(base_dir)):
        raise HTTPException(status_code=403, detail="Access forbidden")

    # Проверяем, что путь существует и это именно файл
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(full_path, media_type="audio/mpeg")
