import logging
from pathlib import Path
from typing import Any, List, Dict

from extractors.utils import save_json_file, LOGGER_NAME

def export_to_json(artists: List[Dict[str, Any]], output_path: Path) -> None:
    """
    Export a list of artist dictionaries to a JSON file.

    The file will contain an array of artist objects as described in the README.
    """
    logger = logging.getLogger(LOGGER_NAME)
    if not isinstance(output_path, Path):
        output_path = Path(output_path)

    logger.info("Writing %d artist(s) to %s", len(artists), output_path)
    save_json_file(output_path, artists)