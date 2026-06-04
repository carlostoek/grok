#!/usr/bin/env python3
"""
Face swap module using ONNX models.
"""

import numpy as np
from pathlib import Path
from typing import Optional, List
import cv2
import onnxruntime as ort


class FaceSwapper:
    """Face swapper using ONNX model."""

    def __init__(
        self,
        model_path: Optional[Path] = None,
        providers: Optional[List[str]] = None
    ):
        """
        Initialize face swapper.

        Args:
            model_path: Path to inswapper ONNX model
            providers: List of ONNX providers (default: CPU)
        """
        self.model_path = model_path
        self.providers = providers or ["CPUExecutionProvider"]
        self.session = None

        if model_path and model_path.exists():
            self._load_model(model_path)
        else:
            print(f"Warning: Model not found at {model_path}")
            print("Please download the inswapper model first")

    def _load_model(self, model_path: Path):
        """Load ONNX model."""
        self.session = ort.InferenceSession(
            str(model_path),
            providers=self.providers
        )
        print(f"Loaded face swapper: {model_path.name}")

    def swap_face(
        self,
        source_face: np.ndarray,
        target_face: np.ndarray,
        face_bbox: tuple
    ) -> np.ndarray:
        """
        Swap face from source to target.

        Args:
            source_face: Source face image (BGR)
            target_face: Target face image (BGR)
            face_bbox: Target face bounding box (x1, y1, x2, y2)

        Returns:
            Swapped face image
        """
        if self.session is None:
            # Simple blend fallback (no actual swap)
            return target_face.copy()

        x1, y1, x2, y2 = face_bbox
        h, w = target_face.shape[:2]

        # Crop to face region
        face_region = target_face[y1:y2, x1:x2]

        # Resize source to match target face size
        source_resized = cv2.resize(source_face, (face_region.shape[1], face_region.shape[0]))

        # Preprocess
        target_crop = cv2.resize(face_region, (128, 128))
        source_crop = cv2.resize(source_resized, (128, 128))

        target_input = target_crop.astype(np.float32) / 255.0
        target_input = target_input.transpose(2, 0, 1)
        target_input = np.expand_dims(target_input, axis=0)

        source_input = source_crop.astype(np.float32) / 255.0
        source_input = source_input.transpose(2, 0, 1)
        source_input = np.expand_dims(source_input, axis=0)

        # Run inference
        outputs = self.session.run(
            None,
            {"target": target_input, "source": source_input}
        )

        result = outputs[0][0]
        result = result.transpose(1, 2, 0)
        result = (result * 255).astype(np.uint8)
        result = cv2.resize(result, (face_region.shape[1], face_region.shape[0]))

        # Blend result with original
        blended = cv2.addWeighted(face_region, 0.3, result, 0.7, 0)

        # Replace face region
        result_img = target_face.copy()
        result_img[y1:y2, x1:x2] = blended

        return result_img