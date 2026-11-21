import os

from dotenv import load_dotenv
from sqlalchemy import JSON, Integer, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

load_dotenv()
database_url = os.getenv("DATABASE_URL", "sqlite:///./adrobot.db")
engine = create_engine(database_url)
Session = sessionmaker(engine)


class Base(DeclarativeBase):
    pass


class StreamState(Base):
    __tablename__ = "stream_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    campaign_id: Mapped[int]
    published: Mapped[list] = mapped_column(JSON)
    archive: Mapped[list] = mapped_column(JSON)
    pins: Mapped[dict] = mapped_column(JSON)
    draft: Mapped[dict] = mapped_column(JSON, nullable=True)
