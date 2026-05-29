from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from PIL import Image
from torch import nn
from torch.utils.data import DataLoader, Subset
from torchvision import datasets, models, transforms
from tqdm import tqdm

from food_ai.vision.config import (
    BATCH_SIZE,
    CLASS_INDEX_PATH,
    DEFAULT_ARCHITECTURE,
    EPOCHS,
    FOOD101_IMAGE_DIR,
    IMG_SIZE,
    LR,
    MODEL_PATH,
    NUM_WORKERS,
    SEED,
    SUPPORTED_ARCHITECTURES,
    VAL_SPLIT,
)


IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]


def build_model(architecture: str, num_classes: int, pretrained: bool = True) -> nn.Module:
    if architecture == "mobilenet_v3_large":
        weights = models.MobileNet_V3_Large_Weights.DEFAULT if pretrained else None
        model = models.mobilenet_v3_large(weights=weights)
        in_features = model.classifier[-1].in_features
        model.classifier[-1] = nn.Linear(in_features, num_classes)
        return model
    if architecture == "efficientnet_b0":
        weights = models.EfficientNet_B0_Weights.DEFAULT if pretrained else None
        model = models.efficientnet_b0(weights=weights)
        in_features = model.classifier[-1].in_features
        model.classifier[-1] = nn.Linear(in_features, num_classes)
        return model
    raise ValueError(f"Unsupported architecture: {architecture}. Supported: {', '.join(SUPPORTED_ARCHITECTURES)}")


def train_transforms(img_size: int = IMG_SIZE) -> transforms.Compose:
    return transforms.Compose(
        [
            transforms.RandomResizedCrop(img_size, scale=(0.75, 1.0)),
            transforms.RandomHorizontalFlip(),
            transforms.ColorJitter(brightness=0.15, contrast=0.15, saturation=0.12, hue=0.02),
            transforms.ToTensor(),
            transforms.Normalize(mean=IMAGENET_MEAN, std=IMAGENET_STD),
        ]
    )


def validation_transforms(img_size: int = IMG_SIZE) -> transforms.Compose:
    return transforms.Compose(
        [
            transforms.Resize((img_size, img_size)),
            transforms.ToTensor(),
            transforms.Normalize(mean=IMAGENET_MEAN, std=IMAGENET_STD),
        ]
    )


def _pil_loader(path: str) -> Image.Image:
    with open(path, "rb") as handle:
        image = Image.open(handle)
        return image.convert("RGB")


def _split_indices(dataset_size: int, val_split: float, seed: int) -> tuple[list[int], list[int]]:
    generator = torch.Generator().manual_seed(seed)
    indices = torch.randperm(dataset_size, generator=generator).tolist()
    val_count = max(1, int(dataset_size * val_split))
    val_indices = indices[:val_count]
    train_indices = indices[val_count:]
    return train_indices, val_indices


def _topk_correct(logits: torch.Tensor, targets: torch.Tensor, top_k: int) -> int:
    k = min(top_k, logits.shape[1])
    _, predictions = logits.topk(k, dim=1)
    correct = predictions.eq(targets.view(-1, 1).expand_as(predictions))
    return int(correct.any(dim=1).sum().item())


def _run_epoch(
    model: nn.Module,
    loader: DataLoader,
    criterion: nn.Module,
    device: torch.device,
    optimizer: torch.optim.Optimizer | None = None,
) -> dict[str, float]:
    training = optimizer is not None
    model.train(training)
    total_loss = 0.0
    total_samples = 0
    top1_correct = 0
    top5_correct = 0

    with torch.set_grad_enabled(training):
        for images, targets in tqdm(loader, desc="train" if training else "val", leave=False):
            images = images.to(device)
            targets = targets.to(device)

            if training:
                optimizer.zero_grad(set_to_none=True)

            logits = model(images)
            loss = criterion(logits, targets)

            if training:
                loss.backward()
                optimizer.step()

            batch_size = targets.size(0)
            total_loss += float(loss.item()) * batch_size
            total_samples += batch_size
            top1_correct += _topk_correct(logits, targets, 1)
            top5_correct += _topk_correct(logits, targets, 5)

    return {
        "loss": total_loss / max(total_samples, 1),
        "top1": top1_correct / max(total_samples, 1),
        "top5": top5_correct / max(total_samples, 1),
    }


def save_class_index(class_to_idx: dict[str, int], output_path: Path, architecture: str, img_size: int) -> None:
    idx_to_class = {str(index): label for label, index in class_to_idx.items()}
    payload = {
        "class_to_idx": class_to_idx,
        "idx_to_class": idx_to_class,
        "classes": [label for label, _ in sorted(class_to_idx.items(), key=lambda item: item[1])],
        "architecture": architecture,
        "img_size": img_size,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def train_classifier(
    data_dir: Path = FOOD101_IMAGE_DIR,
    architecture: str = DEFAULT_ARCHITECTURE,
    epochs: int = EPOCHS,
    batch_size: int = BATCH_SIZE,
    lr: float = LR,
    val_split: float = VAL_SPLIT,
    num_workers: int = NUM_WORKERS,
    model_path: Path = MODEL_PATH,
    class_index_path: Path = CLASS_INDEX_PATH,
    seed: int = SEED,
) -> Path:
    if architecture not in SUPPORTED_ARCHITECTURES:
        raise ValueError(f"Unsupported architecture: {architecture}. Supported: {', '.join(SUPPORTED_ARCHITECTURES)}")
    if not data_dir.exists():
        raise FileNotFoundError(f"Food-101 image directory not found: {data_dir}")

    torch.manual_seed(seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    base_dataset = datasets.ImageFolder(data_dir, loader=_pil_loader)
    if len(base_dataset.classes) < 2:
        raise ValueError(f"Expected at least 2 class folders in {data_dir}; found {len(base_dataset.classes)}")

    train_dataset = datasets.ImageFolder(data_dir, transform=train_transforms(), loader=_pil_loader)
    val_dataset = datasets.ImageFolder(data_dir, transform=validation_transforms(), loader=_pil_loader)
    train_indices, val_indices = _split_indices(len(base_dataset), val_split, seed)

    train_loader = DataLoader(
        Subset(train_dataset, train_indices),
        batch_size=batch_size,
        shuffle=True,
        num_workers=num_workers,
        pin_memory=device.type == "cuda",
    )
    val_loader = DataLoader(
        Subset(val_dataset, val_indices),
        batch_size=batch_size,
        shuffle=False,
        num_workers=num_workers,
        pin_memory=device.type == "cuda",
    )

    model = build_model(architecture, len(base_dataset.classes), pretrained=True).to(device)
    criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr)

    model_path.parent.mkdir(parents=True, exist_ok=True)
    save_class_index(base_dataset.class_to_idx, class_index_path, architecture, IMG_SIZE)

    print("PALM Food AI Dish Classifier Training")
    print("=" * 38)
    print(f"Data: {data_dir}")
    print(f"Classes: {len(base_dataset.classes)}")
    print(f"Images: {len(base_dataset)}")
    print(f"Train/val: {len(train_indices)}/{len(val_indices)}")
    print(f"Architecture: {architecture}")
    print(f"Device: {device}")
    print(f"Checkpoint: {model_path}")
    print()

    best_val_top1 = 0.0
    idx_to_class = {str(index): label for label, index in base_dataset.class_to_idx.items()}
    for epoch in range(1, epochs + 1):
        print(f"Epoch {epoch}/{epochs}")
        train_metrics = _run_epoch(model, train_loader, criterion, device, optimizer)
        val_metrics = _run_epoch(model, val_loader, criterion, device)
        print(
            "  "
            f"train loss={train_metrics['loss']:.4f} top1={train_metrics['top1']:.4f} top5={train_metrics['top5']:.4f} | "
            f"val loss={val_metrics['loss']:.4f} top1={val_metrics['top1']:.4f} top5={val_metrics['top5']:.4f}"
        )

        if val_metrics["top1"] >= best_val_top1:
            best_val_top1 = val_metrics["top1"]
            checkpoint = {
                "model_state_dict": model.state_dict(),
                "class_to_idx": base_dataset.class_to_idx,
                "idx_to_class": idx_to_class,
                "num_classes": len(base_dataset.classes),
                "img_size": IMG_SIZE,
                "architecture": architecture,
                "best_val_top1": best_val_top1,
                "epoch": epoch,
            }
            torch.save(checkpoint, model_path)
            print(f"  saved best checkpoint: val top1={best_val_top1:.4f}")
        print()

    print(f"Done. Best val top1: {best_val_top1:.4f}")
    print(f"Class index: {class_index_path}")
    return model_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the PALM Food-101 dish classifier.")
    parser.add_argument("--data-dir", type=Path, default=FOOD101_IMAGE_DIR)
    parser.add_argument("--architecture", choices=SUPPORTED_ARCHITECTURES, default=DEFAULT_ARCHITECTURE)
    parser.add_argument("--epochs", type=int, default=EPOCHS)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--lr", type=float, default=LR)
    parser.add_argument("--val-split", type=float, default=VAL_SPLIT)
    parser.add_argument("--num-workers", type=int, default=NUM_WORKERS)
    parser.add_argument("--model-path", type=Path, default=MODEL_PATH)
    parser.add_argument("--class-index-path", type=Path, default=CLASS_INDEX_PATH)
    args = parser.parse_args()

    train_classifier(
        data_dir=args.data_dir,
        architecture=args.architecture,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        val_split=args.val_split,
        num_workers=args.num_workers,
        model_path=args.model_path,
        class_index_path=args.class_index_path,
    )


if __name__ == "__main__":
    main()
