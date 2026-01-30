# database.py
import time
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool, QueuePool

try:
    from .config import settings
except ImportError:
    from config import settings

# Metrics import with caching - try multiple paths to handle different import contexts
_metrics_cache = {}

def _get_metrics():
    if 'metrics' not in _metrics_cache:
        m = None
        try:
            from observability.metrics import metrics
            m = metrics
        except ImportError:
            try:
                from .observability.metrics import metrics
                m = metrics
            except ImportError:
                pass
        _metrics_cache['metrics'] = m
    return _metrics_cache.get('metrics')

# Production connection pooling
engine = create_engine(
    settings.database_url,
    poolclass=QueuePool,
    pool_size=20,           # Number of persistent connections
    max_overflow=40,        # Maximum overflow connections
    pool_timeout=30,        # Timeout for getting connection
    pool_recycle=1800,      # Recycle connections after 30 minutes
    pool_pre_ping=True,     # Test connections before using
    echo=False              # Set to True for debugging
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    """Get database session with metrics tracking."""
    start_time = time.perf_counter()
    error = None
    db = SessionLocal()
    try:
        yield db
    except Exception as e:
        error = type(e).__name__
        raise
    finally:
        db.close()
        duration_ms = (time.perf_counter() - start_time) * 1000
        m = _get_metrics()
        if m:
            m.record_dependency("postgres", duration_ms, error)

def init_db():
    Base.metadata.create_all(bind=engine)