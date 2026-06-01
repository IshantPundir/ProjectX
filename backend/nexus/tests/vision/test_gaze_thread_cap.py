# tests/vision/test_gaze_thread_cap.py
import sys
import types


def _install_stubs(monkeypatch, retinaface_cls, captured):
    """Stub onnxruntime/numpy/uniface so MobileGazeEstimator.__init__ runs without
    real native deps. `retinaface_cls` lets each test pick a uniface RetinaFace
    that does or does not accept sess_options."""

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
            captured["so"] = sess_options

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
    uniface_det.RetinaFace = retinaface_cls
    uniface_pkg = types.ModuleType("uniface")
    monkeypatch.setitem(sys.modules, "uniface", uniface_pkg)
    monkeypatch.setitem(sys.modules, "uniface.detection", uniface_det)


def test_gaze_session_thread_capped_and_retinaface_fallback(monkeypatch):
    captured = {}

    class _RetinaFace:  # uniface version WITHOUT sess_options support
        def __init__(self):
            captured["rf_built"] = True

    _install_stubs(monkeypatch, _RetinaFace, captured)
    from app.modules.vision.gaze.mobilegaze import MobileGazeEstimator

    MobileGazeEstimator(weights_path="/w.onnx", intra_op_threads=1)
    assert captured["intra"] == 1
    assert captured["inter"] == 1
    assert captured["mode"] == "seq"
    # uniface without sess_options → bare RetinaFace() fallback (cgroup is the
    # detector's bound). The build must still succeed.
    assert captured["rf_built"] is True


def test_retinaface_receives_capped_options_when_supported(monkeypatch):
    captured = {}

    class _RetinaFace:  # uniface version that DOES accept sess_options
        def __init__(self, sess_options=None):
            captured["rf_so"] = sess_options

    _install_stubs(monkeypatch, _RetinaFace, captured)
    from app.modules.vision.gaze.mobilegaze import MobileGazeEstimator

    MobileGazeEstimator(weights_path="/w.onnx", intra_op_threads=1)
    # The same capped SessionOptions handed to the gaze session is passed to
    # RetinaFace when the uniface version supports it.
    assert captured["rf_so"] is not None
    assert captured["rf_so"] is captured["so"]
    assert captured["rf_so"].intra_op_num_threads == 1
