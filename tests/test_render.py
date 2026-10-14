from clipforge.core.settings import Settings
from clipforge.pipeline.clips import Clip
from clipforge.pipeline.render import encoder_args, layout_filter, output_name, safe_name


def test_safe_name_strips_windows_chars():
    assert safe_name('Boss: "letzter" Treffer?/\\') == "Boss_letzter_Treffer"


def test_output_name_pattern():
    c = Clip(start=0, end=20, trigger_time=18, reasons=[], title="Mega Fail")
    assert output_name(c, Settings(), "2026-10-14", "Morni", 1) == "2026-10-14_Morni_Mega_Fail"


def test_filters_end_in_video_label():
    for layout in ("blur", "crop"):
        assert layout_filter(layout, 60).endswith("fps=60,format=yuv420p[v]")


def test_encoder_selection():
    assert "h264_nvenc" in encoder_args("h264", True)
    assert "libx265" in encoder_args("hevc", False)
