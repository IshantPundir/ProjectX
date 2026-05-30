# tests/vision/test_gaze_mobilegaze_lazy.py
import sys


def test_importing_module_does_not_import_heavy_deps():
    # Importing the wrapper must stay light — onnxruntime/cv2/uniface load only
    # when an estimator is constructed (inside the vision-worker image).
    for mod in ("onnxruntime", "cv2", "uniface"):
        sys.modules.pop(mod, None)
    import importlib
    import app.modules.vision.gaze.mobilegaze as m
    importlib.reload(m)
    assert "onnxruntime" not in sys.modules
    assert "cv2" not in sys.modules
    assert "uniface" not in sys.modules
    assert hasattr(m, "MobileGazeEstimator")
