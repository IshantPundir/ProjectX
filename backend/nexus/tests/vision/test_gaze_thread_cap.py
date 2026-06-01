# tests/vision/test_gaze_thread_cap.py
import sys
import types

import pytest


@pytest.fixture
def stub_heavy_deps(monkeypatch):
    # Minimal stubs so MobileGazeEstimator.__init__ runs without real ONNX.
    captured = {}

    class _SessionOptions:
        def __init__(self):
            self.intra_op_num_threads = None
            self.inter_op_num_threads = None
            self.execution_mode = None

    class _ExecutionMode:
        ORT_SEQUENTIAL = "seq"

    class _InferenceSession:
        def __init__(self, weights_path, sess_options=None, providers=None):
            captured["intra"] = sess_options.intra_op_num_threads
            captured["inter"] = sess_options.inter_op_num_threads
            captured["mode"] = sess_options.execution_mode

        def get_inputs(self):
            return [types.SimpleNamespace(name="in")]

        def get_outputs(self):
            return [types.SimpleNamespace(name="yaw"), types.SimpleNamespace(name="pitch")]

    ort = types.ModuleType("onnxruntime")
    ort.SessionOptions = _SessionOptions
    ort.ExecutionMode = _ExecutionMode
    ort.InferenceSession = _InferenceSession
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)

    np = types.ModuleType("numpy")
    np.array = lambda *a, **k: types.SimpleNamespace(reshape=lambda *s: None)
    np.arange = lambda *a, **k: None
    np.float32 = "f32"
    monkeypatch.setitem(sys.modules, "numpy", np)

    uniface_det = types.ModuleType("uniface.detection")

    class _RetinaFace:
        def __init__(self):
            pass

    uniface_det.RetinaFace = _RetinaFace
    uniface_pkg = types.ModuleType("uniface")
    monkeypatch.setitem(sys.modules, "uniface", uniface_pkg)
    monkeypatch.setitem(sys.modules, "uniface.detection", uniface_det)
    return captured


def test_gaze_session_thread_capped(stub_heavy_deps):
    from app.modules.vision.gaze.mobilegaze import MobileGazeEstimator

    MobileGazeEstimator(weights_path="/w.onnx", intra_op_threads=1)
    assert stub_heavy_deps["intra"] == 1
    assert stub_heavy_deps["inter"] == 1
    assert stub_heavy_deps["mode"] == "seq"
