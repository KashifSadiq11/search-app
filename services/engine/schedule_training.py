# services/engine/schedule_training.py
import asyncio
import schedule
import time
from datetime import datetime
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import get_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def scheduled_training():
    """Run training on schedule."""
    logger.info(f"Starting scheduled training at {datetime.now()}")
    
    try:
        from train_models import train_ml_models
        db = next(get_db())
        await train_ml_models(db)
        logger.info("Scheduled training completed successfully")
    except Exception as e:
        logger.error(f"Scheduled training failed: {e}")

def run_training_scheduler():
    """Set up and run the training scheduler."""
    
    # Schedule daily training at 2 AM
    schedule.every().day.at("02:00").do(
        lambda: asyncio.run(scheduled_training())
    )
    
    # Schedule weekly full retraining on Sunday
    schedule.every().sunday.at("03:00").do(
        lambda: asyncio.run(scheduled_training())
    )
    
    logger.info("Training scheduler started")
    logger.info("Scheduled times:")
    logger.info("  - Daily at 02:00")
    logger.info("  - Weekly full training on Sunday at 03:00")
    
    while True:
        schedule.run_pending()
        time.sleep(60)  # Check every minute

if __name__ == "__main__":
    # For testing, run training immediately
    asyncio.run(scheduled_training())
    
    # Then start scheduler
    # Uncomment to run scheduler
    # run_training_scheduler()