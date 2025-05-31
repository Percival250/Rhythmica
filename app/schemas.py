from pydantic import BaseModel
from typing import List, Optional


# --- Схемы для песен ---

class SongBase(BaseModel):
    title: str
    artist: str
    genre: Optional[str] = None

class SongCreate(SongBase):
    pass  # для создания песни, если нужно

class SongOut(SongBase):
    id: int

    class Config:
        orm_mode = True


# --- Схемы для пользователя ---

class UserBase(BaseModel):
    username: str

class UserCreate(UserBase):
    password: str

class UserOut(UserBase):
    id: int

    class Config:
        orm_mode = True


# --- Схемы для предпочтений пользователя ---

class PreferenceBase(BaseModel):
    favorite_artist: Optional[str] = None
    favorite_genre: Optional[str] = None

class PreferenceOut(PreferenceBase):
    user_id: int
    version: Optional[int] = 0

    class Config:
        orm_mode = True

class PreferenceUpdate(PreferenceBase):
    pass  # если нужно обновлять


# --- Схемы для плейлистов ---

class PlaylistBase(BaseModel):
    name: str

class PlaylistCreate(PlaylistBase):
    pass

class PlaylistOut(PlaylistBase):
    id: int
    user_id: int
    songs: List[SongOut] = []

    class Config:
        orm_mode = True


# --- Схемы для избранных песен ---

class FavoriteSongOut(BaseModel):
    id: int
    song: SongOut

    class Config:
        orm_mode = True


# --- Схемы для ленты (рекомендаций) ---

class FeedSongOut(BaseModel):
    id: int
    user_id: int
    song_id: int
    title: str
    artist: str

    class Config:
        orm_mode = True


# --- Схемы для удаления (например, ID песни или плейлиста) ---

class IDSchema(BaseModel):
    id: int
