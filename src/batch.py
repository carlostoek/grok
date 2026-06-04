#!/usr/bin/env python3
"""
Batch processing module for face swap.
"""

import time
from pathlib import Path
from typing import List, Optional
import cv2
import numpy as np
from tqdm import tqdm

from .detector import FaceDetector
from .face_swap import FaceSwapper


def process_single_image(
    source_img: np.ndarray,
    target_path: Path,
    detector: FaceDetector,
    swapper: FaceSwapper
) -> Optional[np.ndarray]:
    """
    Process a single target image with face swap.

    Args:
        source_img: Source face image
        target_path: Path to target image
        detector: Face detector instance
        swapper: Face swapper instance

    Returns:
        Swapped image or None if no face detected
    """
    target_img = cv2.imread(str(target_path))
    if target_img is None:
        return None

    # Detect faces in target
    faces = detector.detect(target_img)

    if not faces:
        print(f"No face detected in {target_path.name}")
        return target_img

    # Use first detected face
    face = faces[0]
    bbox = face['bbox']

    # Swap face
    result = swapper.swap_face(source_img, target_img, bbox)

    return result


def process_batch(
    source_path: Path,
    input_dir: Path,
    output_dir: Path,
    detector: FaceDetector,
    swapper: FaceSwapper,
    batch_size: int = 10,
    extensions: tuple = ('.jpg', '.jpeg', '.png', '.webp')
) -> dict:
    """
    Process batch of images.

    Args:
        source_path: Path to source image (face to swap)
        input_dir: Directory with target images
        output_dir: Directory for output images
        detector: Face detector instance
        swapper: Face swapper instance
        batch_size: Batch size for processing
        extensions: Image file extensions to process

    Returns:
        Dict with statistics
    """
    # Load source image
    source_img = cv2.imread(str(source_path))
    if source_img is None:
        raise ValueError(f"Cannot read source image: {source_path}")

    # Find all images
    image_files = []
    for ext in extensions:
        image_files.extend(input_dir.glob(f"*{ext}"))
        image_files.extend(input_dir.glob(f"*{ext.upper()}"))

    image_files = sorted(image_files)
    total = len(image_files)

    if total == 0:
        print(f"No images found in {input_dir}")
        return {"total": 0, "processed": 0, "failed": 0, "time": 0}

    print(f"Found {total} images to process")
    print(f"Source: {source_path.name}")
    print(f"Batch size: {batch_size}")
    print("-" * 40)

    output_dir.mkdir(parents=True, exist_ok=True)

    stats = {"total": total, "processed": 0, "failed": 0, "no_face": 0}
    start_time = time.time()

    # Process in batches with progress bar
    for i in tqdm(range(0, total, batch_size), desc="Batches"):
        batch = image_files[i:i + batch_size]

        for target_path in batch:
            try:
                result = process_single_image(
                    source_img, target_path, detector, swapper
                )

                if result is not None:
                    # Check if any face was swapped
                    faces = detector.detect(result)
                    if faces:
                        output_path = output_dir / target_path.name
                        cv2.imwrite(str(output_path), result)
                        stats["processed"] += 1
                    else:
                        stats["no_face"] += 1
                        # Save original as fallback
                        output_path = output_dir / target_path.name
                        cv2.imwrite(str(output_path), result)
                else:
                    stats["failed"] += 1

            except Exception as e:
                print(f"Error processing {target_path.name}: {e}")
                stats["failed"] += 1

    stats["time"] = time.time() - start_time

    # Print summary
    print("-" * 40)
    print("Processing complete!")
    print(f"  Total: {stats['total']}")
    print(f"  Processed: {stats['processed']}")
    print(f"  No face detected: {stats['no_face']}")
    print(f"  Failed: {stats['failed']}")
    print(f"  Time: {stats['time']:.2f}s")
    print(f"  Avg per image: {stats['time']/max(1, stats['processed']):.2f}s")

    return stats