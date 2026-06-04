#!/usr/bin/env python3
"""
Face detection module using ONNX models.
"""

import numpy as np
from pathlib import Path
from typing import Optional, List, Tuple
import cv2
import onnxruntime as ort


class FaceDetector:
    """Face detector using ONNX runtime or OpenCV fallback."""

    def __init__(self, model_path: Optional[Path] = None, providers: Optional[List[str]] = None):
        """
        Initialize face detector.

        Args:
            model_path: Path to ONNX model (optional, for custom models)
            providers: List of ONNX providers (default: CPU)
        """
        self.model_path = model_path
        self.providers = providers or ["CPUExecutionProvider"]
        self.session = None

        # OpenCV fallback
        cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        self.face_cascade = cv2.CascadeClassifier(cascade_path)

        if model_path and model_path.exists():
            self.session = ort.InferenceSession(
                str(model_path),
                providers=[self.providers]
            )
            print(f"Loaded face detector: {model_path.name}")

    def detect(self, image: np.ndarray) -> List[dict]:
        """
        Detect faces in image.

        Args:
            image: Image as numpy array (BGR format from cv2)

        Returns:
            List of face dicts with 'bbox', 'landmarks', 'confidence'
        """
        if self.session is not None:
            return self._detect_onnx(image)
        else:
            return self._detect_opencv(image)

    def _detect_onnx(self, image: np.ndarray) -> List[dict]:
        """Detect faces using ONNX model."""
        h, w = image.shape[:2]

        # Preprocess
        img = cv2.resize(image, (128, 128))
        img = img.astype(np.float32) / 255.0
        img = img.transpose(2, 0, 1)
        img = np.expand_dims(img, axis=0)

        # Run inference
        outputs = self.session.run(None, {"input": img})
        boxes = outputs[0] if outputs else []

        # Parse results (placeholder - format depends on model)
        faces = []
        for box in boxes:
            if len(box) >= 5 and box[4] > 0.5:
                x1, y1, x2, y2 = map(int, box[:4])
                faces.append({
                    'bbox': (x1, y1, x2, y2),
                    'landmarks': [],
                    'confidence': float(box[4])
                })

        return faces

    def _detect_opencv(self, image: np.ndarray) -> List[dict]:
        """Fallback face detection using OpenCV Haar Cascade."""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        faces = self.face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(30, 30)
        )

        results = []
        for x, y, w, h in faces:
            results.append({
                'bbox': (x, y, x + w, y + h),
                'landmarks': [],
                'confidence': 1.0
            })

        return results