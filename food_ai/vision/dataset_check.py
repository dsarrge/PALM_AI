from __future__ import annotations

import argparse
from pathlib import Path
from statistics import mean

from PIL import Image, UnidentifiedImageError
from tqdm import tqdm

from food_ai.vision.config import FOOD101_IMAGE_DIR


IMAGE_EXTENSIONS = {".bmp", ".jpeg", ".jpg", ".png", ".webp"}


def _image_files(class_dir: Path) -> list[Path]:
    return sorted(path for path in class_dir.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS)


def _find_corrupt_images(image_paths: list[Path]) -> list[Path]:
    corrupt: list[Path] = []
    for image_path in tqdm(image_paths, desc="Checking images", unit="image"):
        try:
            with Image.open(image_path) as image:
                image.verify()
        except (OSError, UnidentifiedImageError):
            corrupt.append(image_path)
    return corrupt


def check_dataset(image_dir: Path = FOOD101_IMAGE_DIR, check_images: bool = False) -> dict[str, object]:
    image_dir = Path(image_dir)
    if not image_dir.exists():
        raise FileNotFoundError(f"Food-101 image directory not found: {image_dir}")

    class_dirs = sorted(path for path in image_dir.iterdir() if path.is_dir())
    counts = {class_dir.name: len(_image_files(class_dir)) for class_dir in class_dirs}
    all_images = [image_path for class_dir in class_dirs for image_path in _image_files(class_dir)]
    corrupt = _find_corrupt_images(all_images) if check_images else []
    non_empty_counts = list(counts.values())

    return {
        "image_dir": str(image_dir),
        "class_count": len(class_dirs),
        "total_images": sum(non_empty_counts),
        "empty_classes": [label for label, count in counts.items() if count == 0],
        "min_images_per_class": min(non_empty_counts) if non_empty_counts else 0,
        "max_images_per_class": max(non_empty_counts) if non_empty_counts else 0,
        "mean_images_per_class": mean(non_empty_counts) if non_empty_counts else 0.0,
        "smallest_classes": sorted(counts.items(), key=lambda item: item[1])[:10],
        "corrupt_images": [str(path) for path in corrupt],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the Food-101 ImageFolder dataset.")
    parser.add_argument("--image-dir", type=Path, default=FOOD101_IMAGE_DIR)
    parser.add_argument("--check-images", action="store_true", help="Open every image with PIL and report corrupt files.")
    args = parser.parse_args()

    summary = check_dataset(args.image_dir, check_images=args.check_images)
    print("Food-101 Dataset Check")
    print("=" * 24)
    print(f"Image dir: {summary['image_dir']}")
    print(f"Total classes: {summary['class_count']}")
    print(f"Total images: {summary['total_images']}")
    print(f"Images per class: min={summary['min_images_per_class']} max={summary['max_images_per_class']} mean={summary['mean_images_per_class']:.1f}")
    print(f"Empty folders: {len(summary['empty_classes'])}")
    if summary["empty_classes"]:
        print("Empty class folders:")
        for label in summary["empty_classes"]:
            print(f"  - {label}")
    print("10 smallest classes:")
    for label, count in summary["smallest_classes"]:
        print(f"  - {label}: {count}")
    print(f"Corrupt count: {len(summary['corrupt_images'])}")
    for image_path in summary["corrupt_images"][:20]:
        print(f"  - {image_path}")


if __name__ == "__main__":
    main()
