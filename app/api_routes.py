import requests
import random
import httpx
import json
import os
from datetime import datetime
from sqlalchemy import func
from fastapi import FastAPI, Request, Form, Depends, HTTPException, Query, Path, APIRouter
from fastapi.responses import RedirectResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
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
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List # предположим, такие модели есть
from app.schemas import SongOut, PlaylistOut, PreferenceOut  # Pydantic-схемы для ответов

app = FastAPI()
api_router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

# Зависимость для подключения к БД (пример)
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

@api_router.post("/register")
def register_user(username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if user:
        raise HTTPException(status_code=400, detail="Username already registered")
    
    hashed_password = get_password_hash(password)
    new_user = User(username=username, hashed_password=hashed_password)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return JSONResponse(status_code=201, content={"user_id": new_user.id, "username": new_user.username})

@api_router.post("/login")
def login_user(username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    # Возвращаем user_id, в реальном приложении лучше JWT токен
    return {"user_id": user.id, "username": user.username}
@api_router.get("/api/my-songs", response_model=List[SongOut])
def get_my_songs(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    songs = db.query(Song).filter(Song.owner_id == user.id).all()
    return songs


# Получение плейлистов пользователя
@api_router.get("/playlists", response_model=List[PlaylistOut])
def get_playlists(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    playlists = db.query(Playlist).filter(Playlist.owner_id == user.id).all()
    return playlists


# Получение песен в плейлисте
@api_router.get("/playlists/{playlist_id}", response_model=PlaylistOut)
def get_playlist(playlist_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    playlist = db.query(Playlist).filter(Playlist.id == playlist_id, Playlist.owner_id == user.id).first()
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    return playlist


# Удаление песни из плейлиста
@api_router.delete("/playlists/{playlist_id}/songs/{song_id}", status_code=204)
def delete_song_from_playlist(playlist_id: int, song_id: int,
                              db: Session = Depends(get_db),
                              user: User = Depends(get_current_user)):
    playlist = db.query(Playlist).filter(Playlist.id == playlist_id, Playlist.owner_id == user.id).first()
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    song = db.query(Song).filter(Song.id == song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")

    if song not in playlist.songs:
        raise HTTPException(status_code=404, detail="Song not in playlist")

    playlist.songs.remove(song)
    db.commit()
    return {"detail": "Song removed from playlist"}


# Получение лайкнутых песен
@api_router.get("/favorites", response_model=List[SongOut])
def get_favorites(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    favorites = db.query(Favorite).filter(Favorite.user_id == user.id).all()
    # Предположим, Favorite содержит поле song (отношение к Song)
    liked_songs = [fav.song for fav in favorites]
    return liked_songs