from pymongo import MongoClient

from app.core.config import get_settings


_settings = get_settings()
_client = MongoClient(_settings.mongodb_uri)
_db = _client[_settings.mongodb_db]


def get_db():
    return _db
