---
name: image
description: Analyze image files, screenshots, and visible text using the configured vision model.
metadata:
  tool-category: image
---
# Image analysis

Only analyze observed local image files using the image_analyze tool with an actual path. Never invent visual details when the model cannot inspect an image. Report model uncertainty and tool errors instead of assuming a vision result is accurate. Image analysis does not authorize editing or deleting the source file.
