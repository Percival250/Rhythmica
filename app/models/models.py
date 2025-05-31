from sqlalchemy import Column, Integer, String, ForeignKey, Table, DateTime, UniqueConstraint, BigInteger
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


# Ассоциативная таблица: песни в плейлисте

playlist_songs = Table(
    "playlist_songs",
    Base.metadata,
    Column("playlist_id", Integer, ForeignKey("playlists.id"), primary_key=True),
    Column("song_id", Integer, ForeignKey("songs.id"), primary_key=True)
)

# Модель пользователя
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(255), unique=True, index=True)
    password = Column(String(255))

    favorite_songs = relationship("FavoriteSong", back_populates="owner")
    preferences = relationship("UserPreference", back_populates="user")
    downloads = relationship("Download", back_populates="user")
    
    # Добавляем связь с Playlist
    playlists = relationship("Playlist", back_populates="user")

# Модель предпочтений пользователя (жанр или артист)
class UserPreference(Base):
    __tablename__ = 'user_preferences'

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), unique=True)
    favorite_artist = Column(String(100))  # <-- Важно! 
    favorite_genre = Column(String(100))
    version = Column(Integer, default=0) 
    user = relationship("User", back_populates="preferences")

class UserFeedCacheInfo(Base):
    __tablename__ = "user_feed_cache_info"
    user_id = Column(Integer, primary_key=True)
    version = Column(Integer, default=0)

# Модель песни
class Song(Base):
    __tablename__ = "songs"
    id = Column(Integer, primary_key=True, index=True)
    api_id = Column(BigInteger, unique=True, nullable=False)
    title = Column(String(255))
    artist = Column(String(255))
    genre = Column(String(255))

    downloads = relationship("Download", back_populates="song")
    playlists = relationship("Playlist", secondary=playlist_songs, back_populates="songs")

# Модель избранной песни
class FavoriteSong(Base):
    __tablename__ = "favorite_songs"

    id = Column(Integer, primary_key=True, index=True)
    song_id = Column(Integer, ForeignKey("songs.id"))
    user_id = Column(Integer, ForeignKey("users.id"))

    song = relationship("Song")
    owner = relationship("User", back_populates="favorite_songs")


# Модель скачанной песни
class Download(Base):
    __tablename__ = "downloads"

    id = Column(Integer, primary_key=True, index=True)
    song_id = Column(Integer, ForeignKey("songs.id"))
    user_id = Column(Integer, ForeignKey("users.id"))
    file_path = Column(String(500))  # Путь к файлу на диске
    downloaded_at = Column(DateTime(timezone=True), server_default=func.now())

    song = relationship("Song", back_populates="downloads")
    user = relationship("User", back_populates="downloads")


# Модель плейлиста
class Playlist(Base):
    __tablename__ = "playlists"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255))
    user_id = Column(Integer, ForeignKey("users.id"))

    user = relationship("User", back_populates="playlists")
    songs = relationship("Song", secondary=playlist_songs, back_populates="playlists")


class FeedSong(Base):
    __tablename__ = 'feed_songs'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    song_id = Column(Integer, nullable=False)
    title = Column(String(255))
    artist = Column(String(255))

    __table_args__ = (
        UniqueConstraint('user_id', 'song_id', name='uq_user_song'),
    )

class FeedSongCache(Base):
    __tablename__ = "feed_song_cache"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)
    song_id = Column(Integer, index=True)
    title = Column(String)
    artist = Column(String)
    album_cover = Column(String)
    preview_url = Column(String)