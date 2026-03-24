"""
VLM Object Matcher — client for VLM (Vision Language Model) integration.
"""
import base64
import json
import logging
import os
import time
from typing import List, Optional

import numpy as np
import requests

from ontology.vlm_objects import VLMObjectDescription, VLMDetection

logger = logging.getLogger(__name__)


class VLMObjectMatcher:
    """
    Client for VLM service.

    Environment variables:
        VLM_ENDPOINT: URL of VLM service (default: http://localhost:5000/predict)
        VLM_TIMEOUT_SEC: timeout in seconds (default: 10)
        VLM_MAX_RETRIES: number of retries (default: 3)
    """

    def __init__(self, endpoint: Optional[str] = None):
        self.endpoint = endpoint or os.environ.get(
            "VLM_ENDPOINT", "http://localhost:5000/predict"
        )
        self.timeout = float(os.environ.get("VLM_TIMEOUT_SEC", "10"))
        self.max_retries = int(os.environ.get("VLM_MAX_RETRIES", "3"))
        self.session = requests.Session()

    def find_objects(
        self,
        image: np.ndarray,
        description: VLMObjectDescription,
    ) -> List[VLMDetection]:
        """
        Find objects in image matching the text description.

        Args:
            image: numpy array (H, W, 3) uint8 (RGB)
            description: VLMObjectDescription

        Returns:
            List of VLMDetection objects.
        """
        # Encode image to base64
        import cv2
        _, buffer = cv2.imencode('.jpg', image)
        image_b64 = base64.b64encode(buffer).decode('utf-8')

        payload = {
            "image": image_b64,
            "text_description": description.text_description,
            "attributes": description.attributes,
            "context_hints": description.context_hints,
            "min_confidence": description.min_confidence,
        }

        for attempt in range(self.max_retries):
            try:
                resp = self.session.post(
                    self.endpoint,
                    json=payload,
                    timeout=self.timeout
                )
                if resp.status_code == 200:
                    data = resp.json()
                    detections = []
                    for det in data.get("detections", []):
                        detections.append(VLMDetection(
                            bbox=det["bbox"],
                            confidence=det["confidence"],
                            description=det.get("description", ""),
                            attributes=det.get("attributes", {}),
                            class_name=det.get("class_name", ""),
                        ))
                    return detections
                else:
                    logger.error(
                        "VLM service returned status %d: %s",
                        resp.status_code, resp.text
                    )
            except (requests.RequestException, json.JSONDecodeError) as e:
                logger.warning(
                    "Attempt %d/%d failed: %s",
                    attempt + 1, self.max_retries, e
                )
                if attempt == self.max_retries - 1:
                    raise

        # If all retries failed and we haven't returned, return empty list
        return []