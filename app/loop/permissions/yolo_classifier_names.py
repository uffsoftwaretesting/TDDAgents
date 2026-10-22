"""
The classifier's tool name, in a module of its own.

`classifierDecision.ts` imports `YOLO_CLASSIFIER_TOOL_NAME` from `yoloClassifier.ts`; it lives
here so the allowlist does not import the whole classifier (and its model seam) to read one
string. `yolo_classifier.py` re-exports it.
"""

from __future__ import annotations

#: `YOLO_CLASSIFIER_TOOL_NAME`.
YOLO_CLASSIFIER_TOOL_NAME = "classify_result"

__all__ = ["YOLO_CLASSIFIER_TOOL_NAME"]
