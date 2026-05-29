from __future__ import annotations

from pathlib import Path


def available_test_images() -> list[Path]:
    root = Path(__file__).resolve().parent
    return sorted(path for path in root.iterdir() if path.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp'})


if __name__ == '__main__':
    for path in available_test_images():
        print(path)
