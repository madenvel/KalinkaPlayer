#!/usr/bin/env python3
"""
Migration helper script to transition from the old thread-based enricher to the new async enricher.
This is a simple wrapper that renames the files, creating backups of the original files.
"""

import os
import sys
import shutil
import datetime
import logging
import argparse

logger = logging.getLogger(__name__)


def backup_file(file_path):
    """Create a backup of a file with timestamp"""
    if not os.path.exists(file_path):
        logger.warning(f"File {file_path} does not exist, skipping backup")
        return False

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{file_path}.{timestamp}.bak"
    try:
        shutil.copy2(file_path, backup_path)
        logger.info(f"Created backup: {backup_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to create backup of {file_path}: {e}")
        return False


def migrate_enricher(enricher_dir, force=False):
    """Migrate from old thread-based enricher to new async enricher"""
    # Define file pairs (old -> new)
    file_pairs = [
        ("enricher.py", "enricher_async.py"),
        ("enricher_db.py", "enricher_db_async.py"),
    ]

    # Check if the directory exists
    if not os.path.isdir(enricher_dir):
        logger.error(f"Directory {enricher_dir} does not exist")
        return False

    # Check if all new files exist
    for _, new_file in file_pairs:
        new_file_path = os.path.join(enricher_dir, new_file)
        if not os.path.exists(new_file_path):
            logger.error(
                f"New file {new_file_path} not found. Run the migration script first."
            )
            return False

    # Backup and rename files
    for old_file, new_file in file_pairs:
        old_path = os.path.join(enricher_dir, old_file)
        new_path = os.path.join(enricher_dir, new_file)

        # Check if old file exists
        if not os.path.exists(old_path):
            logger.warning(f"Original file {old_path} not found.")
            if force:
                logger.warning(f"Force mode enabled, continuing without original file.")
            else:
                logger.error("Use --force to continue without all original files.")
                return False
        else:
            # Backup old file
            if backup_file(old_path):
                # Remove old file
                try:
                    os.remove(old_path)
                    logger.info(f"Removed original file: {old_path}")
                except Exception as e:
                    logger.error(f"Failed to remove {old_path}: {e}")
                    return False

        # Rename new file to old file name
        try:
            shutil.copy2(new_path, old_path)
            logger.info(f"Created {old_path} from {new_path}")
        except Exception as e:
            logger.error(f"Failed to copy {new_path} to {old_path}: {e}")
            return False

    logger.info(f"Successfully migrated to async enricher in {enricher_dir}")
    return True


if __name__ == "__main__":
    # Setup argument parser
    parser = argparse.ArgumentParser(
        description="Migrate from thread-based enricher to async enricher"
    )
    parser.add_argument(
        "--enricher-dir",
        help="Path to the enricher directory",
        default="/home/envel/Source/RpiPlayer/addons/input_module/localfiles/enricher",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force migration even if original files are missing",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        help="Set logging level",
    )

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Run migration
    if migrate_enricher(args.enricher_dir, args.force):
        print("Migration completed successfully.")
        sys.exit(0)
    else:
        print("Migration failed. Check logs for details.")
        sys.exit(1)
