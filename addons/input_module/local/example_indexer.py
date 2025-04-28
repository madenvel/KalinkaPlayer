import time
import logging
from addons.input_module.local.filedb import FileDb
from addons.input_module.local.indexer import FileIndexer

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def main():
    """Example of using FileIndexer to index audio files."""
    # Path to the music directory to scan
    music_dir = "/home/envel/Music"

    # Path to the database file
    db_path = "myfiles.db"

    # Create FileDb instance
    db = FileDb(db_path)

    # Create FileIndexer instance
    indexer = FileIndexer(music_dir, db)

    # Check the initial status
    logger.info(f"Initial indexer status: {indexer.get_status()}")

    # Start indexing with cleanup of missing files enabled (default)
    logger.info("Starting indexing process...")
    indexer.start_indexing(cleanup_missing_files=True)

    # Check the status periodically while indexing is running
    while indexer.get_status() == "running":
        logger.info(f"Indexing in progress... Status: {indexer.get_status()}")
        time.sleep(2)  # Wait for 2 seconds before checking again

    # Indexing is complete
    logger.info(f"Indexing completed with status: {indexer.get_status()}")

    # Get the list of indexed files
    indexed_files = indexer.get_indexed_files()
    logger.info(f"Total files indexed: {len(indexed_files)}")

    # Display first 5 indexed files (if any)
    if indexed_files:
        logger.info("Sample of indexed files:")
        for file_path in indexed_files[:5]:
            logger.info(f"  - {file_path}")


if __name__ == "__main__":
    main()
