#!/usr/bin/env python3
"""
Face swap using Replicate API (cloud).
"""

import os
import time
from pathlib import Path
from typing import Optional
import replicate


class ReplicateFaceSwapper:
    """Face swapper using Replicate API."""

    def __init__(self, api_token: str, model: str = "ddvinh1/inswapper:25bdae46f2713138640b6e8c04dc4ca18625ce95b1863936b053eee42d9ba6db"):
        """
        Initialize with Replicate API token.

        Args:
            api_token: Replicate API token
            model: Model identifier (default: ddvinh1/inswapper)
        """
        self.client = replicate.Client(api_token=api_token)
        self.model = model

    def swap_face(
        self,
        source_path: str,
        target_path: str
    ) -> tuple:
        """
        Swap face using Replicate API.

        Args:
            source_path: Path to source face image
            target_path: Path to target image

        Returns:
            Tuple of (url, file_content)
        """
        # Use replicate.run() as shown in docs
        output = replicate.run(
            self.model,
            input={
                "source_img": open(source_path, "rb"),
                "target_img": open(target_path, "rb")
            }
        )

        # output is a file-like object with .url and .read()
        return output.url, output

    def swap_face_urls(
        self,
        source_url: str,
        target_url: str
    ) -> tuple:
        """
        Swap face using URLs.

        Args:
            source_url: URL to source face image
            target_url: URL to target image

        Returns:
            Tuple of (url, file_content)
        """
        output = replicate.run(
            self.model,
            input={
                "source_img": source_url,
                "target_img": target_url
            }
        )

        return output.url, output


def process_batch_replicate(
    source_path: str,
    input_dir: Path,
    output_dir: Path,
    api_token: str,
    model: str = "ddvinh1/inswapper:25bdae46f2713138640b6e8c04dc4ca18625ce95b1863936b053eee42d9ba6db",
    batch_size: int = 10
) -> dict:
    """
    Process batch of images using Replicate API.

    Args:
        source_path: Path to source face image
        input_dir: Directory with target images
        output_dir: Directory for output images
        api_token: Replicate API token
        batch_size: Unused (API processes one at a time)

    Returns:
        Dict with statistics
    """
    from tqdm import tqdm

    # Find all images
    extensions = ('.jpg', '.jpeg', '.png', '.webp')
    image_files = []
    for ext in extensions:
        image_files.extend(list(input_dir.glob(f"*{ext}")))
        image_files.extend(list(input_dir.glob(f"*{ext.upper()}")))

    image_files = sorted(set(image_files))
    total = len(image_files)

    if total == 0:
        print(f"No images found in {input_dir}")
        return {"total": 0, "processed": 0, "failed": 0, "time": 0, "cost": 0}

    print(f"Found {total} images to process")
    print(f"Using Replicate API: {model}")
    print("-" * 40)

    output_dir.mkdir(parents=True, exist_ok=True)

    swapper = ReplicateFaceSwapper(api_token, model)

    stats = {"total": total, "processed": 0, "failed": 0, "time": 0, "cost": 0}
    start_time = time.time()

    # Rate limiting: max 6 requests per minute
    rate_limit_delay = 10  # seconds between requests

    # Process each image
    for target_path in tqdm(image_files, desc="Processing"):
        try:
            _, output = swapper.swap_face(source_path, str(target_path))

            # Write output to disk
            output_path = output_dir / target_path.name
            with open(output_path, "wb") as f:
                f.write(output.read())

            stats["processed"] += 1

            # Rate limiting - be nice to the API
            time.sleep(rate_limit_delay)

        except Exception as e:
            print(f"\nError processing {target_path.name}: {e}")
            stats["failed"] += 1

            # If rate limited, wait longer
            if "429" in str(e) or "throttled" in str(e).lower():
                print("Rate limited, waiting 30s...")
                time.sleep(30)

    stats["time"] = time.time() - start_time

    # Estimate cost (~$0.002 per image)
    stats["cost"] = stats["processed"] * 0.002

    print("-" * 40)
    print("Processing complete!")
    print(f"  Total: {stats['total']}")
    print(f"  Processed: {stats['processed']}")
    print(f"  Failed: {stats['failed']}")
    print(f"  Time: {stats['time']:.2f}s")
    print(f"  Est. cost: ${stats['cost']:.4f}")

    return stats