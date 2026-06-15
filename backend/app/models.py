from sqlalchemy import Column, Integer, String, Text, DateTime
from sqlalchemy.ext.declarative import declarative_base
import datetime

Base = declarative_base()

class Review(Base):
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True)
    marketplace = Column(String)
    text = Column(Text)
    answer = Column(Text)
    status = Column(String, default="new")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
