import os

from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

source_folder = os.getenv("SOURCE_FOLDER")
destination_folder = os.getenv("DESTINATION_FOLDER")
embedding_model = os.getenv("EMBEDDING_MODEL")
embedding_columns = os.getenv("EMBEDDING_COLUMNS").split(", ")

source_root = (Path(__file__).parent / source_folder).resolve()
destination_root = (Path(__file__).parent / destination_folder).resolve()

source_paths = list(source_root.rglob("*.jsonl"))

def get_dest_path(source_path: Path, source_root: Path, destination_root: Path) -> Path:
    return destination_root / source_path.relative_to(source_root)

for source_path in source_paths:
    dest_path = get_dest_path(source_path, source_root, destination_root)
    print(source_path, "->", dest_path)
